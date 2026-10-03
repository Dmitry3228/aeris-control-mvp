import asyncio
import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import UUID

import paho.mqtt.client as mqtt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.mqtt_client import MqttRuntime
from app.api.websocket import TelemetryStream
from app.db import Base
from app.domain.commands import CommandKind, CommandStatus
from app.domain.telemetry import TelemetrySample
from app.main import create_app, create_production_app
from app.models import Device
from app.services.command_service import CommandService, CommandView
from app.services.ingestion import IngestionResult, TelemetryIngestionService


NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))


class MemoryHistory:
    def __init__(self) -> None:
        self.samples: list[TelemetrySample] = []

    def append(self, sample: TelemetrySample, event_id: str) -> None:
        del event_id
        self.samples.append(sample)


class MemoryState:
    def __init__(self) -> None:
        self.samples: dict[str, TelemetrySample] = {}

    def put_if_newer(self, sample: TelemetrySample) -> bool:
        current = self.samples.get(sample.device_id)
        if current is not None and current.timestamp >= sample.timestamp:
            return False
        self.samples[sample.device_id] = sample
        return True


class RuntimePublisher:
    target: MqttRuntime | None = None

    def publish_command(self, command: CommandView) -> None:
        if self.target is None:
            raise RuntimeError("runtime unavailable")
        self.target.publish_command(command)


class RecordingRuntime:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.published: list[CommandView] = []

    def start(self) -> None:
        self.started = True

    def publish_command(self, command: CommandView) -> None:
        self.published.append(command)

    def stop(self) -> None:
        self.stopped = True

    def is_healthy(self) -> bool:
        return self.started and not self.stopped


class FailOnceIngestion:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, topic: str, payload: bytes) -> IngestionResult:
        del topic, payload
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("history unavailable")
        return IngestionResult(True, None, True, None)


class RejectingIngestion:
    def process(self, topic: str, payload: bytes) -> IngestionResult:
        del topic, payload
        return IngestionResult(False, "topic_device_mismatch", False, None)

    def metrics(self) -> dict[str, object]:
        return {"rejected": {"topic_device_mismatch": 1}}


@pytest.fixture
def sessions() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
    yield factory
    engine.dispose()


def test_websocket_replays_latest_state_then_streams_monotonic_events(
    sessions: sessionmaker[Session],
) -> None:
    stream = TelemetryStream(epoch="epoch-a")
    stream.publish("AHU-204", {"pressure": 7.1})
    stream.publish("AHU-204", {"pressure": 7.2})
    app = create_app(
        session_factory=sessions,
        telemetry_stream=stream,
        dependency_checks={"database": lambda: True},
    )

    with TestClient(app) as client, client.websocket_connect("/api/ws/telemetry") as socket:
        session = socket.receive_json()
        replay = socket.receive_json()
        stream.publish("AHU-204", {"pressure": 7.3})
        live = socket.receive_json()

    assert session == {"type": "session", "epoch": "epoch-a", "sequence": 2}
    assert replay == {
        "type": "telemetry",
        "epoch": "epoch-a",
        "sequence": 2,
        "deviceId": "AHU-204",
        "payload": {"pressure": 7.2},
    }
    assert live == {
        "type": "telemetry",
        "epoch": "epoch-a",
        "sequence": 3,
        "deviceId": "AHU-204",
        "payload": {"pressure": 7.3},
    }
    assert set(live) == {"type", "epoch", "sequence", "deviceId", "payload"}


def test_alarm_open_close_and_reopen_are_published_as_live_events(
    sessions: sessionmaker[Session],
) -> None:
    history = MemoryHistory()
    state = MemoryState()
    stream = TelemetryStream(epoch="epoch-alarm")
    ingestion = TelemetryIngestionService(history, state, sessions, clock=lambda: NOW)
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=ingestion,
        command_service=CommandService(sessions, RecordingRuntime(), clock=lambda: NOW),
        telemetry_stream=stream,
    )

    def payload(timestamp: datetime, pressure: float) -> bytes:
        return json.dumps(
            {
                "deviceId": "AHU-204",
                "timestamp": timestamp.isoformat(),
                "temperature": 21.4,
                "pressure": pressure,
                "power": 3.2,
                "connected": True,
            }
        ).encode()

    async def capture() -> list[dict[str, object]]:
        async with stream.subscribe() as subscription:
            runtime._consume_telemetry(
                "devices/AHU-204/telemetry", payload(NOW, 7.8)
            )
            runtime._consume_telemetry(
                "devices/AHU-204/telemetry", payload(NOW + timedelta(seconds=1), 7.2)
            )
            runtime._consume_telemetry(
                "devices/AHU-204/telemetry", payload(NOW + timedelta(seconds=2), 7.9)
            )
            return [
                await asyncio.wait_for(subscription.live.get(), timeout=1)
                for _ in range(6)
            ]

    messages = asyncio.run(capture())
    alarm_messages = [message for message in messages if message["type"] == "alarm"]

    assert [message["payload"]["action"] for message in alarm_messages] == [
        "OPEN",
        "CLOSE",
        "OPEN",
    ]
    assert alarm_messages[0]["payload"]["id"] != alarm_messages[2]["payload"]["id"]
    assert alarm_messages[0]["payload"]["ruleKey"] == "pressure_high"
    assert alarm_messages[0]["payload"]["severity"] == "WARNING"
    assert {message["epoch"] for message in messages} == {"epoch-alarm"}


def test_websocket_snapshot_is_not_truncated_by_live_queue_capacity() -> None:
    stream = TelemetryStream(queue_size=2)
    for index in range(300):
        stream.publish(f"AHU-{index:03d}", {"index": index})

    async def capture_snapshot() -> tuple[dict[str, object], ...]:
        async with stream.subscribe() as subscription:
            return subscription.snapshot

    snapshot = asyncio.run(capture_snapshot())

    assert len(snapshot) == 300
    assert [message["deviceId"] for message in snapshot] == [
        f"AHU-{index:03d}" for index in range(300)
    ]


def test_websocket_live_event_cannot_fall_into_snapshot_registration_gap() -> None:
    stream = TelemetryStream(queue_size=1)
    stream.publish("AHU-001", {"version": 1})

    async def capture_replay_and_live() -> tuple[dict[str, object], dict[str, object]]:
        async with stream.subscribe() as subscription:
            replay = subscription.snapshot[0]
            stream.publish("AHU-001", {"version": 2})
            live = await asyncio.wait_for(subscription.live.get(), timeout=1)
            return replay, live

    replay, live = asyncio.run(capture_replay_and_live())

    assert replay["sequence"] == 1
    assert live["sequence"] == 2
    assert live["payload"] == {"version": 2}


def test_mqtt_callback_failure_degrades_health_then_recovers(
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingestion = FailOnceIngestion()
    command_service = CommandService(sessions, RecordingRuntime(), clock=lambda: NOW)
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=ingestion,  # type: ignore[arg-type]
        command_service=command_service,
        telemetry_stream=TelemetryStream(),
    )
    monkeypatch.setattr(runtime._client, "is_connected", lambda: True)
    runtime._started = True
    runtime._subscriptions_ready = True
    message = mqtt.MQTTMessage(mid=1)
    message._topic = b"devices/AHU-204/telemetry"
    message.payload = json.dumps(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.2,
            "power": 3.2,
            "connected": True,
        }
    ).encode()
    callback = runtime._client.on_message
    assert callback is not None

    callback(runtime._client, None, message)

    assert not runtime.is_healthy()
    assert "history unavailable" in caplog.text

    callback(runtime._client, None, message)

    assert runtime.is_healthy()
    assert ingestion.calls == 2


def test_mqtt_health_waits_for_subscription_acknowledgement(
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=RejectingIngestion(),  # type: ignore[arg-type]
        command_service=CommandService(sessions, RecordingRuntime(), clock=lambda: NOW),
        telemetry_stream=TelemetryStream(),
    )
    monkeypatch.setattr(runtime._client, "is_connected", lambda: True)
    runtime._started = True

    assert not runtime.is_healthy()

    runtime._subscription_mid = 17
    runtime._on_subscribe(runtime._client, None, 17, [], None)

    assert runtime.is_healthy()


def test_qos1_message_is_acknowledged_only_after_durable_processing(
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestion = FailOnceIngestion()
    command_service = CommandService(sessions, RecordingRuntime(), clock=lambda: NOW)
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=ingestion,  # type: ignore[arg-type]
        command_service=command_service,
        telemetry_stream=TelemetryStream(),
    )
    acknowledgements: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runtime._client,
        "ack",
        lambda mid, qos: acknowledgements.append((mid, qos)) or mqtt.MQTT_ERR_SUCCESS,
    )
    message = mqtt.MQTTMessage(mid=41)
    message._topic = b"devices/AHU-204/telemetry"
    message.payload = json.dumps(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.2,
            "power": 3.2,
            "connected": True,
        }
    ).encode()
    message.qos = 1
    callback = runtime._client.on_message
    assert callback is not None

    callback(runtime._client, None, message)
    assert acknowledgements == []

    callback(runtime._client, None, message)
    assert acknowledgements == [(41, 1)]
    assert runtime._client._manual_ack is True
    assert runtime._client._clean_session is False


def test_permanent_rejection_is_counted_logged_and_acknowledged(
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=RejectingIngestion(),  # type: ignore[arg-type]
        command_service=CommandService(sessions, RecordingRuntime(), clock=lambda: NOW),
        telemetry_stream=TelemetryStream(),
    )
    acknowledgements: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runtime._client,
        "ack",
        lambda mid, qos: acknowledgements.append((mid, qos)) or mqtt.MQTT_ERR_SUCCESS,
    )
    message = mqtt.MQTTMessage(mid=42)
    message._topic = b"devices/AHU-204/telemetry"
    message.payload = b"{}"
    message.qos = 1
    callback = runtime._client.on_message
    assert callback is not None

    callback(runtime._client, None, message)

    assert acknowledgements == [(42, 1)]
    assert runtime.metrics()["telemetry"] == {
        "rejected": {"topic_device_mismatch": 1}
    }
    assert "topic_device_mismatch" in caplog.text


def test_acknowledgement_topic_device_must_match_persisted_command(
    sessions: sessionmaker[Session],
) -> None:
    publisher = RecordingRuntime()
    service = CommandService(sessions, publisher, clock=lambda: NOW)
    pending = service.create("AHU-204", CommandKind.START, None)
    runtime = MqttRuntime(
        host="unused",
        port=1883,
        ingestion=RejectingIngestion(),  # type: ignore[arg-type]
        command_service=service,
        telemetry_stream=TelemetryStream(),
    )

    runtime._consume_acknowledgement(
        "devices/AHU-203/command-acks",
        json.dumps({"commandId": str(pending.id), "accepted": True}).encode(),
    )

    assert service.get(pending.id).status is CommandStatus.PENDING
    assert runtime.metrics()["acknowledgements"] == {
        "rejected": {"device_mismatch": 1},
        "accepted": 0,
    }


def test_lifespan_starts_runtime_expires_commands_and_stops_cleanly(
    sessions: sessionmaker[Session],
) -> None:
    runtime = RecordingRuntime()
    current = [NOW]
    app = create_app(
        session_factory=sessions,
        command_publisher=runtime,
        dependency_checks={"mqtt": runtime.is_healthy},
        clock=lambda: current[0],
        runtime_factory=lambda command_service, stream: runtime,
        expiry_interval=0.01,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json()["dependencies"]["mqtt"] == "ok"
        created = client.post(
            "/api/commands", json={"deviceId": "AHU-204", "kind": "START"}
        ).json()
        current[0] = NOW + timedelta(seconds=11)
        _wait_until(
            lambda: client.get("/api/commands").json()[0]["status"] == "TIMED_OUT"
        )

    assert runtime.started
    assert runtime.stopped
    assert [command.id for command in runtime.published] == [UUID(created["id"])]


def test_production_app_defers_network_clients_until_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network adapter was constructed before startup")

    monkeypatch.setattr("app.main.ClickHouseTelemetryStore", fail_if_constructed)
    monkeypatch.setattr("app.main.RedisDeviceState", fail_if_constructed)
    monkeypatch.setattr("app.main.MqttRuntime", fail_if_constructed)

    application = create_production_app()

    assert application.title == "Aeris Control API"


@pytest.mark.skipif(
    os.getenv("AERIS_RUN_INTEGRATION") != "1",
    reason="requires the Compose-network MQTT broker",
)
def test_mqtt_round_trip_ingests_telemetry_and_acknowledges_command(
    sessions: sessionmaker[Session],
) -> None:
    history = MemoryHistory()
    state = MemoryState()
    stream = TelemetryStream()
    publisher = RuntimePublisher()
    command_service = CommandService(sessions, publisher, clock=lambda: NOW)
    ingestion = TelemetryIngestionService(history, state, sessions)
    runtime = MqttRuntime(
        host=MQTT_HOST,
        port=MQTT_PORT,
        ingestion=ingestion,
        command_service=command_service,
        telemetry_stream=stream,
        client_id="aeris-backend-integration",
    )
    publisher.target = runtime
    simulator = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aeris-simulator-integration")

    def acknowledge_command(
        client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage
    ) -> None:
        del userdata
        command = json.loads(message.payload)
        device_id = message.topic.split("/")[1]
        client.publish(
            f"devices/{device_id}/command-acks",
            json.dumps({"commandId": command["commandId"], "accepted": True}),
            qos=1,
        )

    simulator.on_message = acknowledge_command
    try:
        runtime.start()
        simulator.connect(MQTT_HOST, MQTT_PORT)
        simulator.subscribe("devices/+/commands", qos=1)
        simulator.loop_start()
        _wait_until(runtime.is_healthy)

        simulator.publish(
            "devices/AHU-204/telemetry",
            json.dumps(
                {
                    "deviceId": "AHU-204",
                    "timestamp": "2026-10-02T08:00:00Z",
                    "temperature": 21.4,
                    "pressure": 7.2,
                    "power": 3.2,
                    "connected": True,
                }
            ),
            qos=1,
        ).wait_for_publish()
        _wait_until(lambda: len(history.samples) == 1)

        command = command_service.create("AHU-204", CommandKind.START, None)
        _wait_until(lambda: command_service.get(command.id).status is CommandStatus.ACKNOWLEDGED)

        assert state.samples["AHU-204"].pressure == 7.2
        assert command_service.get(command.id).acknowledged_at == NOW
    finally:
        simulator.disconnect()
        simulator.loop_stop()
        runtime.stop()


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        sleep(0.05)
    pytest.fail("condition did not become true before timeout")
