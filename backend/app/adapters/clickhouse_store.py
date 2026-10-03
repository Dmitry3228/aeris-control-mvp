import re
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from threading import Lock
from typing import Any

import clickhouse_connect

from app.domain.telemetry import TelemetrySample
from app.migrations import apply_clickhouse_migrations


SAFE_TABLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    device_id: str
    timestamp: datetime
    temperature: float
    pressure: float
    power: float
    connected: bool


class ClickHouseTelemetryStore:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8124,
        username: str = "default",
        password: str = "",
        table: str = "telemetry_events",
    ) -> None:
        if SAFE_TABLE_IDENTIFIER.fullmatch(table) is None:
            raise ValueError("table must be a safe SQL identifier")
        self._client: Any = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password
        )
        self._client_lock = Lock()
        self._table = table
        apply_clickhouse_migrations(self._client)
        if self._table != "telemetry_events":
            self._client.command(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    event_id String,
                    device_id String,
                    timestamp DateTime64(6, 'UTC'),
                    temperature Float64,
                    pressure Float64,
                    power Float64,
                    connected Bool
                ) ENGINE = ReplacingMergeTree
                ORDER BY (device_id, timestamp, event_id)
                """
            )

    def append(self, sample: TelemetrySample, event_id: str) -> None:
        with self._client_lock:
            self._client.insert(
                self._table,
                [[
                    event_id,
                    sample.device_id,
                    sample.timestamp,
                    sample.temperature,
                    sample.pressure,
                    sample.power,
                    sample.connected,
                ]],
                column_names=[
                    "event_id",
                    "device_id",
                    "timestamp",
                    "temperature",
                    "pressure",
                    "power",
                    "connected",
                ],
            )

    def history(
        self,
        device_id: str,
        start: datetime,
        end: datetime,
        max_points: int = 600,
    ) -> list[TelemetryPoint]:
        if max_points < 1:
            raise ValueError("max_points must be at least 1")
        duration_us = max(1, int((end - start).total_seconds() * 1_000_000) + 1)
        bucket_us = max(1, ceil(duration_us / max_points))
        with self._client_lock:
            result = self._client.query(
                f"""
            SELECT
                device_id,
                max(timestamp) AS sampled_at,
                argMax(temperature, timestamp),
                argMax(pressure, timestamp),
                argMax(power, timestamp),
                argMax(connected, timestamp)
            FROM {self._table} FINAL
            WHERE device_id = {{device_id:String}}
              AND timestamp >= {{start:DateTime64(6, 'UTC')}}
              AND timestamp <= {{end:DateTime64(6, 'UTC')}}
            GROUP BY
                device_id,
                intDiv(
                    toUnixTimestamp64Micro(timestamp) - {{start_us:Int64}},
                    {{bucket_us:Int64}}
                )
            ORDER BY sampled_at
            LIMIT {{max_points:UInt32}}
                """,
                parameters={
                    "device_id": device_id,
                    "start": start,
                    "end": end,
                    "start_us": _timestamp_us(start),
                    "bucket_us": bucket_us,
                    "max_points": max_points,
                },
            )
        return [
            TelemetryPoint(
                device_id=str(row[0]),
                timestamp=_utc_datetime(row[1]),
                temperature=float(row[2]),
                pressure=float(row[3]),
                power=float(row[4]),
                connected=bool(row[5]),
            )
            for row in result.result_rows
        ]

    def is_healthy(self) -> bool:
        with self._client_lock:
            self._client.command("SELECT 1")
        return True

    def close(self) -> None:
        with self._client_lock:
            self._client.close()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timestamp_us(value: datetime) -> int:
    timestamp = value.astimezone(UTC)
    return int(timestamp.timestamp()) * 1_000_000 + timestamp.microsecond
