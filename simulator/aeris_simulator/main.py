import argparse
import json
import logging
import os
import signal
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from aeris_simulator.fleet import Fleet, Message, create_fleet


DEFAULT_SIMULATION_START = datetime(2026, 10, 2, 8, tzinfo=UTC)
logger = logging.getLogger(__name__)
HEARTBEAT_PATH = Path(os.getenv("AERIS_SIMULATOR_HEARTBEAT", "/tmp/aeris-simulator-ready"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "correlationId": "simulator",
            },
            separators=(",", ":"),
        )


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    args = _arguments()
    fleet = create_fleet(args.devices, seed=args.seed)
    connected = Event()
    stopping = Event()
    client = _client(fleet, connected)
    client.reconnect_delay_set(min_delay=1, max_delay=8)
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    for termination_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(termination_signal, lambda *_: stopping.set())
    try:
        if not connected.wait(timeout=15):
            raise RuntimeError("MQTT broker connection timed out")
        logger.info("simulator_connected")
        recent_history_present = has_recent_history(
            args.backend_url, device_id="AHU-204", end=args.start
        )
        startup_batches = startup_history_batches(
            fleet,
            end=args.start,
            recent_history_present=recent_history_present,
        )
        for messages in startup_batches:
            publish_messages(client, messages)
        logger.info(
            "simulator_recent_history_%s",
            "skipped" if recent_history_present else "published",
        )
        for messages in telemetry_batches(fleet, interval=args.interval, start=args.start):
            if stopping.is_set():
                break
            publish_messages(client, messages)
            HEARTBEAT_PATH.touch()
            if stopping.wait(args.interval):
                break
    except KeyboardInterrupt:
        pass
    finally:
        HEARTBEAT_PATH.unlink(missing_ok=True)
        client.disconnect()
        client.loop_stop()
        logger.info("simulator_stopped")


def telemetry_batches(
    fleet: Fleet,
    *,
    interval: float,
    start: datetime = DEFAULT_SIMULATION_START,
) -> Iterator[list[Message]]:
    sequence = 0
    while True:
        timestamp = start + timedelta(seconds=sequence * interval)
        yield fleet.telemetry_messages(sequence, timestamp)
        sequence += 1


def startup_history_batches(
    fleet: Fleet,
    *,
    end: datetime,
    recent_history_present: bool,
    points: int = 12,
    duration: timedelta = timedelta(hours=1),
) -> list[list[Message]]:
    if recent_history_present:
        return []
    start = end - duration
    if points <= 0:
        return []
    step = duration / points
    return [
        fleet.telemetry_messages(sequence, start + step * sequence)
        for sequence in range(points)
    ]


def has_recent_history(backend_url: str, *, device_id: str, end: datetime) -> bool:
    start = end - timedelta(hours=1)
    query = urlencode({"start": _timestamp(start), "end": _timestamp(end)})
    url = f"{backend_url.rstrip('/')}/api/equipment/{quote(device_id, safe='')}?{query}"
    with urlopen(url, timeout=5) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("history"), list):
        raise RuntimeError("Backend returned an invalid equipment history response")
    return bool(payload["history"])


def publish_messages(client: mqtt.Client, messages: Iterable[Message]) -> None:
    for topic, payload in messages:
        result = client.publish(topic, payload, qos=1)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed for {topic} with code {result.rc}")


def _client(fleet: Fleet, connected_event: Event | None = None) -> mqtt.Client:
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="aeris-equipment-simulator")

    def on_connect(
        connected: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        del userdata, flags, properties
        if not reason_code.is_failure:
            connected.subscribe("devices/+/commands", qos=1)
            if connected_event is not None:
                connected_event.set()

    def on_message(
        connected: mqtt.Client, userdata: object, message: mqtt.MQTTMessage
    ) -> None:
        del userdata
        acknowledgement = fleet.acknowledge(message.topic, message.payload)
        if acknowledgement is not None:
            try:
                publish_messages(connected, [acknowledgement])
            except RuntimeError as error:
                logger.error("%s", error)

    client.on_connect = on_connect
    client.on_message = on_message
    return client


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish deterministic Aeris HVAC telemetry")
    parser.add_argument("--devices", type=int, default=12)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--start", type=_parse_start, default=_runtime_start())
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument(
        "--backend-url",
        default=os.getenv("AERIS_BACKEND_URL", "http://localhost:4100"),
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.devices <= 0:
        parser.error("--devices must be greater than zero")
    return args


def _runtime_start() -> datetime:
    configured = os.getenv("AERIS_SIMULATION_START")
    if configured:
        return _parse_start(configured)
    return datetime.now(UTC).replace(microsecond=0)


def _parse_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--start must include a timezone")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
