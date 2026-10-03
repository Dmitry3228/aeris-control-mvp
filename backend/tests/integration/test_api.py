from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock, get_ident
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.clickhouse_store import TelemetryPoint
from app.adapters.redis_state import DeviceState
from app.db import Base
from app.main import create_app
from app.models import Alarm, AlarmStatus, Device
from app.services.command_service import CommandView


NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)


class InMemoryState:
    def get(self, device_id: str) -> DeviceState | None:
        if device_id != "AHU-204":
            return None
        return DeviceState(
            device_id=device_id,
            timestamp=NOW,
            temperature=21.4,
            pressure=7.8,
            power=3.2,
            connected=True,
        )


class InMemoryHistory:
    def history(
        self, device_id: str, start: datetime, end: datetime, max_points: int = 600
    ) -> list[TelemetryPoint]:
        del max_points
        del start, end
        if device_id != "AHU-204":
            return []
        return [TelemetryPoint("AHU-204", NOW, 21.4, 7.8, 3.2, True)]


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[CommandView] = []

    def publish_command(self, command: CommandView) -> None:
        self.published.append(command)


class FailingPublisher:
    def publish_command(self, command: CommandView) -> None:
        del command
        raise RuntimeError("broker password super-secret")


@pytest.fixture
def sessions() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
        session.add(
            Alarm(
                id=uuid4(),
                device_id="AHU-204",
                rule_key="pressure_high",
                status=AlarmStatus.ACTIVE,
                severity="WARNING",
                message="Pressure high",
                opened_at=NOW,
                updated_at=NOW,
            )
        )
    yield factory
    engine.dispose()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def client(
    sessions: sessionmaker[Session], publisher: RecordingPublisher
) -> Generator[TestClient, None, None]:
    app = create_app(
        session_factory=sessions,
        state_store=InMemoryState(),
        history_store=InMemoryHistory(),
        command_publisher=publisher,
        dependency_checks={
            "database": lambda: True,
            "redis": lambda: False,
            "clickhouse": lambda: True,
        },
        clock=lambda: NOW,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_unknown_equipment_returns_problem_detail(client: TestClient) -> None:
    response = client.get("/api/equipment/MISSING")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": "Equipment not found",
        "status": 404,
        "detail": "Equipment 'MISSING' does not exist",
        "instance": "/api/equipment/MISSING",
    }


def test_duplicate_alarm_acknowledgement_is_idempotent(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    alarm_id = client.get("/api/alarms").json()[0]["id"]

    first = client.post(f"/api/alarms/{alarm_id}/ack")
    second = client.post(f"/api/alarms/{alarm_id}/ack")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "ACKNOWLEDGED"
    assert second.json()["status"] == "ACKNOWLEDGED"
    assert second.json()["acknowledgedAt"] == first.json()["acknowledgedAt"]
    with sessions() as session:
        alarm = session.get(Alarm, UUID(alarm_id))
        assert alarm is not None
        assert alarm.status is AlarmStatus.ACKNOWLEDGED


def test_health_reports_each_dependency_status(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "dependencies": {
            "database": "ok",
            "redis": "unavailable",
            "clickhouse": "ok",
        },
    }


def test_health_surfaces_structured_runtime_rejection_counters(
    sessions: sessionmaker[Session],
) -> None:
    app = create_app(
        session_factory=sessions,
        state_store=InMemoryState(),
        history_store=InMemoryHistory(),
        command_publisher=RecordingPublisher(),
        dependency_checks={"database": lambda: True},
        runtime_metrics=lambda: {
            "telemetry": {
                "accepted": 4,
                "duplicates": 1,
                "processingFailures": 0,
                "rejected": {"topic_device_mismatch": 2},
            },
            "acknowledgements": {"accepted": 1, "rejected": {"device_mismatch": 1}},
        },
    )
    with TestClient(app) as test_client:
        response = test_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["metrics"] == {
        "telemetry": {
            "accepted": 4,
            "duplicates": 1,
            "processingFailures": 0,
            "rejected": {"topic_device_mismatch": 2},
        },
        "acknowledgements": {"accepted": 1, "rejected": {"device_mismatch": 1}},
    }


def test_equipment_routes_include_current_state_and_history(client: TestClient) -> None:
    listing = client.get("/api/equipment")
    detail = client.get(
        "/api/equipment/AHU-204",
        params={"start": "2026-10-02T07:00:00Z", "end": "2026-10-02T09:00:00Z"},
    )

    assert listing.status_code == 200
    assert listing.json()[0]["state"]["pressure"] == 7.8
    assert detail.status_code == 200
    assert detail.json()["id"] == "AHU-204"
    assert detail.json()["history"][0]["timestamp"] == "2026-10-02T08:00:00Z"


def test_stale_status_is_computed_consistently_with_controlled_clock(
    sessions: sessionmaker[Session],
) -> None:
    class StaleState:
        def get(self, device_id: str) -> DeviceState | None:
            if device_id != "AHU-204":
                return None
            return DeviceState(
                device_id=device_id,
                timestamp=NOW - timedelta(seconds=31),
                temperature=21.4,
                pressure=7.2,
                power=3.2,
                connected=True,
            )

    app = create_app(
        session_factory=sessions,
        state_store=StaleState(),
        history_store=InMemoryHistory(),
        command_publisher=RecordingPublisher(),
        dependency_checks={"database": lambda: True},
        clock=lambda: NOW,
        stale_after_seconds=30,
    )
    with TestClient(app) as test_client:
        listing = test_client.get("/api/equipment").json()
        detail = test_client.get("/api/equipment/AHU-204").json()

    assert listing[0]["operationalStatus"] == "STALE"
    assert detail["operationalStatus"] == "STALE"


def test_running_offline_and_no_data_statuses_are_truthful(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        session.add(Device(id="AHU-205", name="Offline", equipment_type="AHU"))
        session.add(Device(id="AHU-206", name="No data", equipment_type="AHU"))

    class MixedState:
        def get(self, device_id: str) -> DeviceState | None:
            if device_id == "AHU-204":
                return DeviceState(device_id, NOW, 21.4, 7.2, 3.2, True)
            if device_id == "AHU-205":
                return DeviceState(device_id, NOW, 21.4, 7.2, 3.2, False)
            return None

    app = create_app(
        session_factory=sessions,
        state_store=MixedState(),
        history_store=InMemoryHistory(),
        command_publisher=RecordingPublisher(),
        dependency_checks={"database": lambda: True},
        clock=lambda: NOW,
        stale_after_seconds=30,
    )
    with TestClient(app) as test_client:
        by_id = {item["id"]: item for item in test_client.get("/api/equipment").json()}

    assert by_id["AHU-204"]["operationalStatus"] == "RUNNING"
    assert by_id["AHU-205"]["operationalStatus"] == "OFFLINE"
    assert by_id["AHU-206"]["operationalStatus"] == "NO_DATA"


def test_command_route_publishes_pending_command(
    client: TestClient, publisher: RecordingPublisher
) -> None:
    response = client.post(
        "/api/commands", json={"deviceId": "AHU-204", "kind": "SETPOINT", "value": 21.5}
    )

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING"
    assert len(publisher.published) == 1
    commands = client.get("/api/commands", params={"deviceId": "AHU-204"})
    assert commands.status_code == 200
    assert commands.json()[0]["id"] == response.json()["id"]


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-10-02T07:00:00Z"},
        {"end": "2026-10-02T09:00:00Z"},
        {"start": "2026-10-02T09:00:00Z", "end": "2026-10-02T07:00:00Z"},
    ],
)
def test_invalid_history_range_returns_validation_problem(
    client: TestClient, params: dict[str, str]
) -> None:
    response = client.get("/api/equipment/AHU-204", params=params)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Invalid history range"
    assert response.json()["instance"] == "/api/equipment/AHU-204"


def test_history_range_and_point_budget_are_enforced(
    sessions: sessionmaker[Session],
) -> None:
    calls: list[tuple[datetime, datetime, int]] = []

    class BudgetHistory:
        def history(
            self,
            device_id: str,
            start: datetime,
            end: datetime,
            max_points: int,
        ) -> list[TelemetryPoint]:
            assert device_id == "AHU-204"
            calls.append((start, end, max_points))
            return []

    app = create_app(
        session_factory=sessions,
        state_store=InMemoryState(),
        history_store=BudgetHistory(),
        command_publisher=RecordingPublisher(),
        dependency_checks={"database": lambda: True},
        clock=lambda: NOW,
        history_max_span_days=7,
        history_point_budget=600,
    )
    with TestClient(app) as test_client:
        accepted = test_client.get(
            "/api/equipment/AHU-204",
            params={"start": "2026-09-25T08:00:00Z", "end": "2026-10-02T08:00:00Z"},
        )
        rejected = test_client.get(
            "/api/equipment/AHU-204",
            params={"start": "2026-09-25T07:59:59Z", "end": "2026-10-02T08:00:00Z"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "History range cannot exceed 7 days"
    assert len(calls) == 1
    assert calls[0][2] == 600


def test_publish_failure_returns_safe_problem_and_retains_command(
    sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(
        session_factory=sessions,
        state_store=InMemoryState(),
        history_store=InMemoryHistory(),
        command_publisher=FailingPublisher(),
        dependency_checks={"database": lambda: True},
        clock=lambda: NOW,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/commands", json={"deviceId": "AHU-204", "kind": "START"}
        )
        commands = test_client.get("/api/commands").json()

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == (
        f"Command '{commands[0]['id']}' was persisted but could not be published"
    )
    assert "super-secret" not in response.text
    assert "super-secret" in caplog.text
    assert commands[0]["status"] == "PENDING"


def test_competing_alarm_acknowledgements_return_one_persisted_transition(
    tmp_path: Path,
) -> None:
    engine, concurrent_sessions, alarm_id = _concurrent_alarm_sessions(tmp_path)
    barrier = _synchronize_first_updates(engine, "alarms")
    times = iter([NOW, NOW.replace(second=1)])
    lock = Lock()

    def clock() -> datetime:
        with lock:
            return next(times)

    app = create_app(
        session_factory=concurrent_sessions,
        state_store=InMemoryState(),
        history_store=InMemoryHistory(),
        command_publisher=RecordingPublisher(),
        dependency_checks={"database": lambda: True},
        clock=clock,
    )
    with TestClient(app) as test_client, ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(test_client.post, f"/api/alarms/{alarm_id}/ack"),
            executor.submit(test_client.post, f"/api/alarms/{alarm_id}/ack"),
        ]
        responses = [future.result(timeout=5) for future in futures]

    assert barrier.broken is False
    assert [response.status_code for response in responses] == [200, 200]
    acknowledged_at = {response.json()["acknowledgedAt"] for response in responses}
    assert len(acknowledged_at) == 1
    with concurrent_sessions() as session:
        alarm = session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.status is AlarmStatus.ACKNOWLEDGED
        assert acknowledged_at == {alarm.acknowledged_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")}
    engine.dispose()


def _concurrent_alarm_sessions(
    tmp_path: Path,
) -> tuple[Engine, sessionmaker[Session], UUID]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'alarm-ack.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    alarm_id = uuid4()
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
        session.add(
            Alarm(
                id=alarm_id,
                device_id="AHU-204",
                rule_key="pressure_high",
                status=AlarmStatus.ACTIVE,
                opened_at=NOW,
                updated_at=NOW,
            )
        )
    return engine, factory, alarm_id


def _synchronize_first_updates(engine: Engine, table: str) -> Barrier:
    barrier = Barrier(2)
    seen_threads: set[int] = set()
    lock = Lock()
    prefix = f"UPDATE {table.upper()} "

    @event.listens_for(engine, "before_cursor_execute")
    def wait_for_competitor(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        if not statement.lstrip().upper().startswith(prefix):
            return
        thread_id = get_ident()
        with lock:
            first_update = thread_id not in seen_threads
            seen_threads.add(thread_id)
        if first_update:
            barrier.wait(timeout=5)
    return barrier
