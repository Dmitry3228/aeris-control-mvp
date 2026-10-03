import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.clickhouse_store import ClickHouseTelemetryStore
from app.adapters.redis_state import DeviceState
from app.db import Base
from app.domain.alarms import AlarmAction
from app.domain.telemetry import TelemetrySample
from app.models import Alarm, AlarmStatus, Device
from app.services.ingestion import TelemetryIngestionService


class RecordingHistoryStore:
    def __init__(self) -> None:
        self.samples: list[TelemetrySample] = []

    def append(self, sample: TelemetrySample, event_id: str) -> None:
        del event_id
        self.samples.append(sample)


class DurableFaultHistory:
    def __init__(self) -> None:
        self.attempts = 0
        self.events: dict[str, TelemetrySample] = {}

    def append(self, sample: TelemetrySample, event_id: str) -> None:
        self.attempts += 1
        self.events.setdefault(event_id, sample)
        if self.attempts == 1:
            raise RuntimeError("clickhouse response lost after insert")


class InMemoryDeviceState:
    def __init__(self) -> None:
        self.states: dict[str, DeviceState] = {}

    def put_if_newer(self, sample: TelemetrySample) -> bool:
        current = self.states.get(sample.device_id)
        if current is not None and current.timestamp >= sample.timestamp:
            return False
        self.states[sample.device_id] = DeviceState.from_sample(sample)
        return True

    def get(self, device_id: str) -> DeviceState | None:
        return self.states.get(device_id)


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
    yield factory
    engine.dispose()


@pytest.fixture
def history() -> RecordingHistoryStore:
    return RecordingHistoryStore()


@pytest.fixture
def state() -> InMemoryDeviceState:
    return InMemoryDeviceState()


@pytest.fixture
def service(
    session_factory: sessionmaker[Session], history: RecordingHistoryStore, state: InMemoryDeviceState
) -> TelemetryIngestionService:
    return TelemetryIngestionService(history, state, session_factory)


def payload_for(
    device_id: str = "AHU-204", timestamp: datetime = datetime(2026, 10, 2, 8, tzinfo=UTC)
) -> bytes:
    return json.dumps(
        {
            "deviceId": device_id,
            "timestamp": timestamp.isoformat(),
            "temperature": 21.4,
            "pressure": 7.8,
            "power": 3.2,
            "connected": True,
        }
    ).encode()


def test_rejects_invalid_or_unknown_payload(
    service: TelemetryIngestionService, history: RecordingHistoryStore, state: InMemoryDeviceState
) -> None:
    malformed = service.process("devices/AHU-204/telemetry", b"not-json")
    unknown = service.process("devices/MISSING/telemetry", payload_for("MISSING"))

    assert not malformed.accepted
    assert malformed.reason == "invalid_payload"
    assert not unknown.accepted
    assert unknown.reason == "unknown_device"
    assert history.samples == []
    assert state.get("AHU-204") is None


def test_out_of_order_sample_preserves_latest_state(
    service: TelemetryIngestionService, history: RecordingHistoryStore, state: InMemoryDeviceState
) -> None:
    newest_at = datetime(2026, 10, 2, 8, tzinfo=UTC)
    older_at = newest_at - timedelta(minutes=1)

    newest = service.process("devices/AHU-204/telemetry", payload_for(timestamp=newest_at))
    older = service.process("devices/AHU-204/telemetry", payload_for(timestamp=older_at))

    assert newest.state_updated
    assert not older.state_updated
    assert [sample.timestamp for sample in history.samples] == [newest_at, older_at]
    latest = state.get("AHU-204")
    assert latest is not None
    assert latest.timestamp == newest_at


def test_older_recovery_cannot_close_a_newer_high_pressure_alarm(
    service: TelemetryIngestionService,
    session_factory: sessionmaker[Session],
) -> None:
    newest_at = datetime(2026, 10, 2, 8, tzinfo=UTC)
    older_at = newest_at - timedelta(minutes=1)

    service.process(
        "devices/AHU-204/telemetry",
        payload_for(timestamp=newest_at),
    )
    service.process(
        "devices/AHU-204/telemetry",
        json.dumps(
            {
                "deviceId": "AHU-204",
                "timestamp": older_at.isoformat(),
                "temperature": 21.4,
                "pressure": 7.2,
                "power": 3.2,
                "connected": True,
            }
        ).encode(),
    )

    with session_factory() as session:
        alarms = session.scalars(select(Alarm).order_by(Alarm.opened_at)).all()
    assert len(alarms) == 1
    assert alarms[0].status is AlarmStatus.ACTIVE
    assert alarms[0].opened_at.replace(tzinfo=UTC) == newest_at


def test_older_high_pressure_sample_cannot_open_an_alarm_after_newer_recovery(
    service: TelemetryIngestionService,
    session_factory: sessionmaker[Session],
) -> None:
    newest_at = datetime(2026, 10, 2, 8, tzinfo=UTC)
    older_at = newest_at - timedelta(minutes=1)

    service.process(
        "devices/AHU-204/telemetry",
        json.dumps(
            {
                "deviceId": "AHU-204",
                "timestamp": newest_at.isoformat(),
                "temperature": 21.4,
                "pressure": 7.2,
                "power": 3.2,
                "connected": True,
            }
        ).encode(),
    )
    service.process(
        "devices/AHU-204/telemetry",
        payload_for(timestamp=older_at),
    )

    with session_factory() as session:
        alarms = session.scalars(select(Alarm)).all()
    assert alarms == []


def test_valid_sample_updates_history_and_cache(
    service: TelemetryIngestionService, history: RecordingHistoryStore, state: InMemoryDeviceState
) -> None:
    result = service.process("devices/AHU-204/telemetry", payload_for())

    assert result.accepted
    assert result.state_updated
    assert result.alarm_decision is not None
    assert result.alarm_decision.action is AlarmAction.OPEN
    assert history.samples[0].device_id == "AHU-204"
    cached = state.get("AHU-204")
    assert cached is not None
    assert cached.pressure == 7.8


def test_clickhouse_store_rejects_unsafe_table_name() -> None:
    with pytest.raises(ValueError, match="safe SQL identifier"):
        ClickHouseTelemetryStore(table="telemetry-history")


def test_rejects_topic_identity_non_finite_domain_and_clock_skew(
    session_factory: sessionmaker[Session],
    history: RecordingHistoryStore,
    state: InMemoryDeviceState,
) -> None:
    now = datetime(2026, 10, 2, 8, tzinfo=UTC)
    service = TelemetryIngestionService(
        history,
        state,
        session_factory,
        clock=lambda: now,
        maximum_past_age=timedelta(days=7),
        maximum_future_skew=timedelta(seconds=30),
    )

    mismatched = service.process("devices/AHU-203/telemetry", payload_for(timestamp=now))
    non_finite_payload = json.loads(payload_for(timestamp=now))
    non_finite_payload["pressure"] = float("inf")
    non_finite = service.process(
        "devices/AHU-204/telemetry", json.dumps(non_finite_payload).encode()
    )
    out_of_domain_payload = json.loads(payload_for(timestamp=now))
    out_of_domain_payload["temperature"] = 200
    out_of_domain = service.process(
        "devices/AHU-204/telemetry", json.dumps(out_of_domain_payload).encode()
    )
    future = service.process(
        "devices/AHU-204/telemetry",
        payload_for(timestamp=now + timedelta(seconds=31)),
    )
    expired = service.process(
        "devices/AHU-204/telemetry",
        payload_for(timestamp=now - timedelta(days=7, seconds=1)),
    )

    assert [
        mismatched.reason,
        non_finite.reason,
        out_of_domain.reason,
        future.reason,
        expired.reason,
    ] == [
        "topic_device_mismatch",
        "non_finite_value",
        "value_out_of_bounds",
        "timestamp_out_of_bounds",
        "timestamp_out_of_bounds",
    ]
    assert service.metrics()["rejected"] == {
        "non_finite_value": 1,
        "timestamp_out_of_bounds": 2,
        "topic_device_mismatch": 1,
        "value_out_of_bounds": 1,
    }
    assert history.samples == []
    assert state.get("AHU-204") is None


def test_durable_retry_is_idempotent_after_history_write_response_is_lost(
    session_factory: sessionmaker[Session],
    state: InMemoryDeviceState,
) -> None:
    history = DurableFaultHistory()
    first_worker = TelemetryIngestionService(
        history,
        state,
        session_factory,
        clock=lambda: datetime(2026, 10, 2, 8, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="response lost"):
        first_worker.process("devices/AHU-204/telemetry", payload_for())

    restarted_worker = TelemetryIngestionService(
        history,
        state,
        session_factory,
        clock=lambda: datetime(2026, 10, 2, 8, tzinfo=UTC),
    )
    recovered = restarted_worker.recover_pending()
    duplicate = restarted_worker.process("devices/AHU-204/telemetry", payload_for())

    assert recovered == 1
    assert duplicate.accepted
    assert duplicate.duplicate
    assert len(history.events) == 1
    assert history.attempts == 2
    with session_factory() as session:
        alarms = session.scalars(select(Alarm)).all()
    assert len(alarms) == 1
    assert alarms[0].status is AlarmStatus.ACTIVE
    current = state.get("AHU-204")
    assert current is not None
    assert current.timestamp == datetime(2026, 10, 2, 8, tzinfo=UTC)
