from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock, get_ident

import pytest
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domain.commands import CommandKind, CommandStatus
from app.models import Command, CommandEventRecord, Device
from app.services.command_service import CommandService, CommandView, UnknownDeviceError


NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self.published: list[CommandView] = []
        self.was_persisted_before_publish = False

    def publish_command(self, command: CommandView) -> None:
        with self._sessions() as session:
            self.was_persisted_before_publish = session.get(Command, command.id) is not None
        self.published.append(command)


class FailingPublisher:
    def publish_command(self, command: CommandView) -> None:
        del command
        raise RuntimeError("broker unavailable")


@pytest.fixture
def sessions() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
    yield factory
    engine.dispose()


def test_create_persists_pending_command_before_publish(
    sessions: sessionmaker[Session],
) -> None:
    publisher = RecordingPublisher(sessions)
    service = CommandService(sessions, publisher, clock=lambda: NOW)

    command = service.create("AHU-204", CommandKind.SETPOINT, 21.5)

    assert publisher.was_persisted_before_publish
    assert publisher.published == [command]
    assert command.status is CommandStatus.PENDING
    assert command.timeout_at == NOW + timedelta(seconds=10)


def test_command_timeout_transition(sessions: sessionmaker[Session]) -> None:
    service = CommandService(sessions, RecordingPublisher(sessions), clock=lambda: NOW)
    command = service.create("AHU-204", CommandKind.START, None)

    expired_count = service.expire(NOW + timedelta(seconds=10))

    assert expired_count == 1
    expired = service.get(command.id)
    assert expired.status is CommandStatus.TIMED_OUT
    with sessions() as session:
        event_count = session.scalar(
            select(func.count()).select_from(CommandEventRecord).where(
                CommandEventRecord.command_id == command.id
            )
        )
    assert event_count == 1


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [(True, CommandStatus.ACKNOWLEDGED), (False, CommandStatus.REJECTED)],
)
def test_acknowledge_exposes_terminal_status(
    sessions: sessionmaker[Session], accepted: bool, expected: CommandStatus
) -> None:
    service = CommandService(sessions, RecordingPublisher(sessions), clock=lambda: NOW)
    pending = service.create("AHU-204", CommandKind.STOP, None)

    acknowledged = service.acknowledge(pending.id, accepted)

    assert acknowledged.status is expected
    assert acknowledged.acknowledged_at == NOW


def test_unknown_device_is_rejected_without_publish(sessions: sessionmaker[Session]) -> None:
    publisher = RecordingPublisher(sessions)
    service = CommandService(sessions, publisher, clock=lambda: NOW)

    with pytest.raises(UnknownDeviceError):
        service.create("MISSING", CommandKind.START, None)

    assert publisher.published == []


def test_publish_failure_leaves_pending_command_queryable(
    sessions: sessionmaker[Session],
) -> None:
    service = CommandService(sessions, FailingPublisher(), clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        service.create("AHU-204", CommandKind.START, None)

    pending = service.list(device_id="AHU-204")
    assert len(pending) == 1
    assert pending[0].status is CommandStatus.PENDING


def test_expire_does_not_change_acknowledged_command(sessions: sessionmaker[Session]) -> None:
    service = CommandService(sessions, RecordingPublisher(sessions), clock=lambda: NOW)
    pending = service.create("AHU-204", CommandKind.START, None)
    service.acknowledge(pending.id, accepted=True)

    assert service.expire(NOW + timedelta(minutes=1)) == 0
    assert service.get(pending.id).status is CommandStatus.ACKNOWLEDGED


def test_late_acknowledgement_atomically_times_out_command(
    sessions: sessionmaker[Session],
) -> None:
    creator = CommandService(sessions, RecordingPublisher(sessions), clock=lambda: NOW)
    pending = creator.create("AHU-204", CommandKind.START, None)
    late_service = CommandService(
        sessions,
        RecordingPublisher(sessions),
        clock=lambda: NOW + timedelta(seconds=11),
    )

    resolved = late_service.acknowledge(pending.id, accepted=True)

    assert resolved.status is CommandStatus.TIMED_OUT
    assert resolved.acknowledged_at is None
    with sessions() as session:
        events = session.scalars(
            select(CommandEventRecord).where(CommandEventRecord.command_id == pending.id)
        ).all()
    assert [event.event.value for event in events] == ["TIMEOUT"]


def test_competing_acknowledgements_create_only_winning_event(tmp_path: Path) -> None:
    engine, concurrent_sessions = _file_sessions(tmp_path, "acknowledge.db")
    service = CommandService(
        concurrent_sessions, RecordingPublisher(concurrent_sessions), clock=lambda: NOW
    )
    pending = service.create("AHU-204", CommandKind.START, None)
    _synchronize_first_updates(engine, "commands")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.acknowledge, pending.id, True),
            executor.submit(service.acknowledge, pending.id, False),
        ]
        results = [future.result(timeout=5) for future in futures]

    persisted = service.get(pending.id)
    assert {result.status for result in results} == {persisted.status}
    assert persisted.status in {CommandStatus.ACKNOWLEDGED, CommandStatus.REJECTED}
    with concurrent_sessions() as session:
        event_count = session.scalar(
            select(func.count()).select_from(CommandEventRecord).where(
                CommandEventRecord.command_id == pending.id
            )
        )
    assert event_count == 1
    engine.dispose()


def test_competing_expiry_workers_create_one_timeout_event(tmp_path: Path) -> None:
    engine, concurrent_sessions = _file_sessions(tmp_path, "expire.db")
    service = CommandService(
        concurrent_sessions, RecordingPublisher(concurrent_sessions), clock=lambda: NOW
    )
    pending = service.create("AHU-204", CommandKind.START, None)
    _synchronize_first_updates(engine, "commands")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.expire, NOW + timedelta(seconds=10)),
            executor.submit(service.expire, NOW + timedelta(seconds=10)),
        ]
        counts = [future.result(timeout=5) for future in futures]

    assert sorted(counts) == [0, 1]
    assert service.get(pending.id).status is CommandStatus.TIMED_OUT
    with concurrent_sessions() as session:
        event_count = session.scalar(
            select(func.count()).select_from(CommandEventRecord).where(
                CommandEventRecord.command_id == pending.id
            )
        )
    assert event_count == 1
    engine.dispose()


def _file_sessions(tmp_path: Path, name: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Device(id="AHU-204", name="Air handler", equipment_type="AHU"))
    return engine, factory


def _synchronize_first_updates(engine: Engine, table: str) -> None:
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
