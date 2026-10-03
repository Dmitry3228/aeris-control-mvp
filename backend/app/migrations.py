from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    Alarm,
    Command,
    CommandEventRecord,
    Device,
    DeviceTelemetryCursor,
    TelemetryInbox,
)


SQLITE_VERSION_TABLE = "schema_migrations"
CLICKHOUSE_VERSION_TABLE = "aeris_schema_migrations"


def apply_sqlite_migrations(session_factory: sessionmaker[Session]) -> None:
    engine = cast(Engine, session_factory.kw["bind"])
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {SQLITE_VERSION_TABLE} (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        versions = set(
            connection.execute(
                text(f"SELECT version FROM {SQLITE_VERSION_TABLE}")
            ).scalars()
        )
        if 1 not in versions and inspect(connection).has_table("devices"):
            _record_sqlite_version(connection, 1)
            versions.add(1)

    migrations: tuple[tuple[int, Callable[[Engine], None]], ...] = (
        (1, _sqlite_v1_operational_schema),
        (2, _sqlite_v2_durable_telemetry),
    )
    for version, migration in migrations:
        if version in versions:
            continue
        migration(engine)
        with engine.begin() as connection:
            _record_sqlite_version(connection, version)
        versions.add(version)


def apply_clickhouse_migrations(client: Any) -> None:
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {CLICKHOUSE_VERSION_TABLE} (
            version UInt32,
            applied_at DateTime64(6, 'UTC')
        ) ENGINE = MergeTree
        ORDER BY version
        """
    )
    versions = {
        int(row[0])
        for row in client.query(
            f"SELECT version FROM {CLICKHOUSE_VERSION_TABLE} ORDER BY version"
        ).result_rows
    }
    migrations = (
        (1, _clickhouse_v1_legacy_history),
        (2, _clickhouse_v2_idempotent_events),
    )
    for version, migration in migrations:
        if version in versions:
            continue
        for statement in migration():
            client.command(statement)
        client.command(
            f"INSERT INTO {CLICKHOUSE_VERSION_TABLE} VALUES "
            f"({version}, now64(6, 'UTC'))"
        )
        versions.add(version)


def _record_sqlite_version(connection: Any, version: int) -> None:
    connection.execute(
        text(
            f"INSERT INTO {SQLITE_VERSION_TABLE} (version, applied_at) "
            "VALUES (:version, :applied_at)"
        ),
        {"version": version, "applied_at": datetime.now(UTC).isoformat()},
    )


def _sqlite_v1_operational_schema(engine: Engine) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            cast(Table, Device.__table__),
            cast(Table, Alarm.__table__),
            cast(Table, Command.__table__),
            cast(Table, CommandEventRecord.__table__),
        ],
    )


def _sqlite_v2_durable_telemetry(engine: Engine) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            cast(Table, DeviceTelemetryCursor.__table__),
            cast(Table, TelemetryInbox.__table__),
        ],
    )


def _clickhouse_v1_legacy_history() -> tuple[str, ...]:
    return (
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            device_id String,
            timestamp DateTime64(6, 'UTC'),
            temperature Float64,
            pressure Float64,
            power Float64,
            connected Bool
        ) ENGINE = MergeTree
        ORDER BY (device_id, timestamp)
        """,
    )


def _clickhouse_v2_idempotent_events() -> tuple[str, ...]:
    return (
        """
        CREATE TABLE IF NOT EXISTS telemetry_events (
            event_id String,
            device_id String,
            timestamp DateTime64(6, 'UTC'),
            temperature Float64,
            pressure Float64,
            power Float64,
            connected Bool
        ) ENGINE = ReplacingMergeTree
        ORDER BY (device_id, timestamp, event_id)
        """,
        """
        INSERT INTO telemetry_events
        SELECT
            lower(hex(SHA256(concat(
                device_id, '|', toString(timestamp), '|',
                toString(temperature), '|', toString(pressure), '|',
                toString(power), '|', toString(connected)
            )))) AS event_id,
            device_id,
            timestamp,
            temperature,
            pressure,
            power,
            connected
        FROM telemetry
        """,
    )
