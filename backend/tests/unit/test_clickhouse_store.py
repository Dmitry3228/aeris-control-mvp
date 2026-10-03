from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from time import sleep
from typing import Any

import pytest

from app.adapters.clickhouse_store import ClickHouseTelemetryStore
from app.domain.telemetry import TelemetrySample


class QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class RecordingClient:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[object]], list[str]]] = []
        self.queries: list[tuple[str, dict[str, object]]] = []

    def command(self, statement: str, **kwargs: Any) -> None:
        del statement, kwargs

    def query(self, statement: str, **kwargs: Any) -> QueryResult:
        parameters = kwargs.get("parameters", {})
        assert isinstance(parameters, dict)
        self.queries.append((" ".join(statement.split()), parameters))
        start = datetime(2026, 10, 2, 8, tzinfo=UTC)
        return QueryResult(
            [
                ("AHU-204", start + timedelta(minutes=index), 21.0, 7.0 + index / 10, 3.0, True)
                for index in range(4)
            ]
        )

    def insert(
        self,
        table: str,
        rows: list[list[object]],
        column_names: list[str],
    ) -> None:
        self.inserts.append((table, rows, column_names))

    def close(self) -> None:
        return None


class ConcurrentDetectingClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self._gate = Lock()
        self._active = False

    def _begin(self) -> None:
        with self._gate:
            if self._active:
                raise RuntimeError("concurrent ClickHouse session use")
            self._active = True
            self.entered.set()

    def _end(self) -> None:
        with self._gate:
            self._active = False

    def query(self, statement: str, **kwargs: Any) -> QueryResult:
        self._begin()
        try:
            sleep(0.05)
            return QueryResult([])
        finally:
            self._end()

    def insert(
        self,
        table: str,
        rows: list[list[object]],
        column_names: list[str],
    ) -> None:
        del table, rows, column_names
        self._begin()
        try:
            sleep(0.01)
        finally:
            self._end()


def test_clickhouse_append_is_keyed_and_history_is_server_downsampled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    monkeypatch.setattr("app.adapters.clickhouse_store.clickhouse_connect.get_client", lambda **_: client)
    monkeypatch.setattr("app.adapters.clickhouse_store.apply_clickhouse_migrations", lambda _: None)
    store = ClickHouseTelemetryStore()
    sample = TelemetrySample.from_payload(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.8,
            "power": 3.2,
            "connected": True,
        }
    )
    start = sample.timestamp
    end = start + timedelta(days=7)

    store.append(sample, "event-204")
    points = store.history("AHU-204", start, end, max_points=4)

    table, rows, columns = client.inserts[0]
    assert table == "telemetry_events"
    assert columns[0] == "event_id"
    assert rows[0][0] == "event-204"
    statement, parameters = client.queries[0]
    assert "FROM telemetry_events FINAL" in statement
    assert "GROUP BY" in statement
    assert "LIMIT {max_points:UInt32}" in statement
    assert parameters["max_points"] == 4
    assert parameters["bucket_us"] >= 24 * 60 * 60 * 1_000_000
    assert len(points) == 4


def test_shared_clickhouse_client_serializes_api_reads_and_mqtt_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ConcurrentDetectingClient()
    monkeypatch.setattr("app.adapters.clickhouse_store.clickhouse_connect.get_client", lambda **_: client)
    monkeypatch.setattr("app.adapters.clickhouse_store.apply_clickhouse_migrations", lambda _: None)
    store = ClickHouseTelemetryStore()
    sample = TelemetrySample.from_payload(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.8,
            "power": 3.2,
            "connected": True,
        }
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        history = executor.submit(
            store.history,
            "AHU-204",
            sample.timestamp,
            sample.timestamp + timedelta(hours=1),
            600,
        )
        assert client.entered.wait(timeout=1)
        append = executor.submit(store.append, sample, "event-204")
        assert history.result(timeout=1) == []
        append.result(timeout=1)
