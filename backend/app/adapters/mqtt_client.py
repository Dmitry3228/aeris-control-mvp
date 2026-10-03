import json
import logging
from collections import Counter
from re import fullmatch
from threading import Lock
from typing import Any
from uuid import UUID

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from app.api.websocket import TelemetryStream
from app.services.command_service import CommandNotFoundError, CommandService, CommandView
from app.services.ingestion import TelemetryIngestionService


logger = logging.getLogger(__name__)


class MqttRuntime:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        ingestion: TelemetryIngestionService,
        command_service: CommandService,
        telemetry_stream: TelemetryStream,
        client_id: str = "aeris-telemetry-api",
        keepalive: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._ingestion = ingestion
        self._command_service = command_service
        self._telemetry_stream = telemetry_stream
        self._keepalive = keepalive
        self._client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=False,
            manual_ack=True,
        )
        self._client.reconnect_delay_set(min_delay=1, max_delay=8)
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._started = False
        self._processing_healthy = True
        self._subscriptions_ready = False
        self._subscription_mid: int | None = None
        self._lock = Lock()
        self._acknowledgement_rejections: Counter[str] = Counter()
        self._acknowledgements_accepted = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            recovered = self._ingestion.recover_pending()
            if recovered:
                logger.info("Recovered pending telemetry events: %s", recovered)
            self._processing_healthy = True
            self._subscriptions_ready = False
            self._subscription_mid = None
            self._client.connect_async(self._host, self._port, keepalive=self._keepalive)
            self._client.loop_start()
            self._started = True

    def publish_command(self, command: CommandView) -> None:
        if not self.is_healthy():
            raise RuntimeError("MQTT broker is unavailable")
        payload = json.dumps(
            {
                "commandId": str(command.id),
                "kind": command.kind.value,
                "value": command.value,
            },
            separators=(",", ":"),
        )
        result = self._client.publish(
            f"devices/{command.device_id}/commands", payload, qos=1
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT command publish failed with code {result.rc}")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        self._client.disconnect()
        self._client.loop_stop()

    def is_healthy(self) -> bool:
        with self._lock:
            runtime_healthy = (
                self._started
                and self._processing_healthy
                and self._subscriptions_ready
            )
        return runtime_healthy and self._client.is_connected()

    def metrics(self) -> dict[str, object]:
        ingestion_metrics = getattr(self._ingestion, "metrics", None)
        telemetry = ingestion_metrics() if callable(ingestion_metrics) else {}
        with self._lock:
            acknowledgements = {
                "rejected": dict(sorted(self._acknowledgement_rejections.items())),
                "accepted": self._acknowledgements_accepted,
            }
        return {"telemetry": telemetry, "acknowledgements": acknowledgements}

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        del userdata, flags, properties
        with self._lock:
            self._subscriptions_ready = False
            self._subscription_mid = None
        if reason_code.is_failure:
            logger.warning("MQTT connection rejected: %s", reason_code)
            return
        result, mid = client.subscribe(
            [("devices/+/telemetry", 1), ("devices/+/command-acks", 1)]
        )
        if result != mqtt.MQTT_ERR_SUCCESS or mid is None:
            logger.error("MQTT subscription request failed with code %s", result)
            return
        with self._lock:
            self._subscription_mid = mid

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[ReasonCode],
        properties: Properties | None,
    ) -> None:
        del client, userdata, properties
        with self._lock:
            expected_mid = self._subscription_mid
            if mid != expected_mid:
                return
            self._subscription_mid = None
            self._subscriptions_ready = all(
                not reason_code.is_failure for reason_code in reason_code_list
            )
        if not self._subscriptions_ready:
            logger.error("MQTT broker rejected one or more subscriptions")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        del client, userdata, disconnect_flags, reason_code, properties
        with self._lock:
            self._subscriptions_ready = False
            self._subscription_mid = None

    def _on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        del client, userdata
        try:
            if message.topic.endswith("/telemetry"):
                self._consume_telemetry(message.topic, message.payload)
            elif message.topic.endswith("/command-acks"):
                self._consume_acknowledgement(message.topic, message.payload)
            else:
                logger.warning(
                    "Rejected MQTT message on unsupported topic",
                    extra={"topic": message.topic, "reason": "unsupported_topic"},
                )
            if message.qos > 0:
                acknowledgement = self._client.ack(message.mid, message.qos)
                if acknowledgement != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(
                        f"MQTT acknowledgement failed with code {acknowledgement}"
                    )
        except Exception:
            with self._lock:
                self._processing_healthy = False
            logger.exception("MQTT message processing failed", extra={"topic": message.topic})
        else:
            with self._lock:
                self._processing_healthy = True

    def _consume_telemetry(self, topic: str, payload: bytes) -> None:
        result = self._ingestion.process(topic, payload)
        if not result.accepted:
            logger.warning(
                "Rejected MQTT telemetry: %s",
                result.reason or "unknown",
                extra={"topic": topic, "reason": result.reason or "unknown"},
            )
            return
        if result.state_updated:
            try:
                decoded = json.loads(payload)
                device_id = decoded["deviceId"]
            except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                return
            if isinstance(decoded, dict) and isinstance(device_id, str):
                self._telemetry_stream.publish(device_id, decoded)
        if result.alarm_update is not None:
            alarm = result.alarm_update
            self._telemetry_stream.publish_alarm(
                alarm.device_id,
                {
                    "id": str(alarm.id),
                    "deviceId": alarm.device_id,
                    "ruleKey": alarm.rule_key,
                    "action": alarm.action.value,
                    "status": alarm.status.value,
                    "severity": alarm.severity,
                    "message": alarm.message,
                    "openedAt": alarm.opened_at.isoformat(),
                    "updatedAt": alarm.updated_at.isoformat(),
                    "acknowledgedAt": (
                        alarm.acknowledged_at.isoformat()
                        if alarm.acknowledged_at is not None
                        else None
                    ),
                    "closedAt": (
                        alarm.closed_at.isoformat() if alarm.closed_at is not None else None
                    ),
                },
            )

    def _consume_acknowledgement(self, topic: str, payload: bytes) -> None:
        topic_match = fullmatch(r"devices/([^/]+)/command-acks", topic)
        if topic_match is None:
            self._reject_acknowledgement("invalid_topic", topic)
            return
        topic_device_id = topic_match.group(1)
        try:
            decoded = json.loads(payload)
            command_id = UUID(decoded["commandId"])
            accepted = decoded["accepted"]
            if not isinstance(accepted, bool):
                raise TypeError("accepted must be a boolean")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._reject_acknowledgement("malformed_payload", topic)
            return
        try:
            command = self._command_service.get(command_id)
        except CommandNotFoundError:
            self._reject_acknowledgement("unknown_command", topic, command_id)
            return
        if command.device_id != topic_device_id:
            self._reject_acknowledgement("device_mismatch", topic, command_id)
            return
        updated = self._command_service.acknowledge(command_id, accepted)
        self._telemetry_stream.publish_command(
            updated.device_id,
            {
                "id": str(updated.id),
                "deviceId": updated.device_id,
                "kind": updated.kind.value,
                "value": updated.value,
                "status": updated.status.value,
                "createdAt": updated.created_at.isoformat(),
                "timeoutAt": updated.timeout_at.isoformat(),
                "acknowledgedAt": (
                    updated.acknowledged_at.isoformat()
                    if updated.acknowledged_at is not None
                    else None
                ),
            },
        )
        with self._lock:
            self._acknowledgements_accepted += 1

    def _reject_acknowledgement(
        self,
        reason: str,
        topic: str,
        command_id: UUID | None = None,
    ) -> None:
        with self._lock:
            self._acknowledgement_rejections[reason] += 1
        logger.warning(
            "Rejected MQTT command acknowledgement: %s",
            reason,
            extra={
                "topic": topic,
                "reason": reason,
                "command_id": str(command_id) if command_id is not None else None,
            },
        )
