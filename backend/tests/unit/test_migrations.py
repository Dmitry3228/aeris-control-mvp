from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import FLEET, initialize_database
from app.db import Base
from app.migrations import apply_clickhouse_migrations
from app.models import Alarm, Command, CommandEventRecord, Device


def test_sqlite_migrations_install_fresh_schema_with_ordered_versions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    initialize_database(sessions)

    with engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars().all()
    tables = set(inspect(engine).get_table_names())
    with sessions() as session:
        devices = session.scalars(select(Device).order_by(Device.id)).all()

    assert versions == [1, 2]
    assert {"telemetry_inbox", "device_telemetry_cursors"} <= tables
    assert [device.id for device in devices] == [seed.id for seed in FLEET]
    engine.dispose()


def test_sqlite_migrations_upgrade_legacy_schema_without_losing_data(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            Device.__table__,
            Alarm.__table__,
            Command.__table__,
            CommandEventRecord.__table__,
        ],
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(Device(id="LEGACY-001", name="Legacy handler", equipment_type="AHU"))

    initialize_database(sessions)

    with engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars().all()
    with sessions() as session:
        legacy = session.get(Device, "LEGACY-001")

    assert versions == [1, 2]
    assert legacy is not None
    assert legacy.name == "Legacy handler"
    assert {"telemetry_inbox", "device_telemetry_cursors"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()


@dataclass
class _Rows:
    result_rows: list[tuple[int]]


class RecordingClickHouse:
    def __init__(self) -> None:
        self.versions: set[int] = set()
        self.commands: list[str] = []

    def query(self, statement: str, **kwargs: Any) -> _Rows:
        del statement, kwargs
        return _Rows([(version,) for version in sorted(self.versions)])

    def command(self, statement: str, **kwargs: Any) -> None:
        del kwargs
        normalized = " ".join(statement.split())
        self.commands.append(normalized)
        marker = "INSERT INTO aeris_schema_migrations VALUES ("
        if marker in normalized:
            self.versions.add(int(normalized.split(marker, 1)[1].split(",", 1)[0]))


def test_clickhouse_migrations_apply_idempotent_event_upgrade_once() -> None:
    client = RecordingClickHouse()

    apply_clickhouse_migrations(client)
    first_run = list(client.commands)
    apply_clickhouse_migrations(client)

    assert client.versions == {1, 2}
    assert len(client.commands) == len(first_run) + 1
    assert any("CREATE TABLE IF NOT EXISTS telemetry_events" in command for command in first_run)
    assert any(
        "INSERT INTO telemetry_events" in command and "FROM telemetry" in command
        for command in first_run
    )
    assert any("ReplacingMergeTree" in command for command in first_run)
