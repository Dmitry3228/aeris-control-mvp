import json
import os
from datetime import UTC, datetime, timedelta
from time import sleep

import clickhouse_connect
import pytest
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.clickhouse_store import ClickHouseTelemetryStore
from app.adapters.redis_state import RedisDeviceState
from app.db import Base
from app.models import Device
from app.services.ingestion import TelemetryIngestionService


TEST_DEVICE_ID = "AHU-204-INGESTION-TEST"
TEST_TIMESTAMP = datetime(2026, 10, 2, 8, tzinfo=UTC)


def wait_for_services() -> None:
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    for _ in range(20):
        try:
            clickhouse = clickhouse_connect.get_client(
                host=os.getenv("CLICKHOUSE_HOST", "localhost"),
                port=int(os.getenv("CLICKHOUSE_PORT", "8124")),
                password=os.getenv("CLICKHOUSE_PASSWORD", "aeris-test"),
            )
            clickhouse.command("SELECT 1")
            redis.ping()
            return
        except Exception:  # pragma: no cover - exercised only while containers start
            sleep(0.5)
    pytest.fail("ClickHouse and Redis did not become available")


@pytest.mark.skipif(
    os.getenv("AERIS_RUN_INTEGRATION") != "1",
    reason="requires the Compose-network backend test runner",
)
def test_telemetry_pipeline_round_trips_clickhouse_history_and_redis_state() -> None:
    wait_for_services()
    device_id = TEST_DEVICE_ID
    newest_at = TEST_TIMESTAMP
    older_at = newest_at - timedelta(minutes=1)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(Device(id=device_id, name="Integration handler", equipment_type="AHU"))

    history = ClickHouseTelemetryStore(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8124")),
        password=os.getenv("CLICKHOUSE_PASSWORD", "aeris-test"),
    )
    state = RedisDeviceState(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    service = TelemetryIngestionService(history, state, sessions)
    clickhouse = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8124")),
        password=os.getenv("CLICKHOUSE_PASSWORD", "aeris-test"),
    )
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

    def payload(timestamp: datetime, pressure: float) -> bytes:
        return json.dumps(
            {
                "deviceId": device_id,
                "timestamp": timestamp.isoformat(),
                "temperature": 21.4,
                "pressure": pressure,
                "power": 3.2,
                "connected": True,
            }
        ).encode()

    _clear_test_data(clickhouse, redis)
    try:
        assert service.process(f"devices/{device_id}/telemetry", payload(newest_at, 7.8)).state_updated
        assert not service.process(f"devices/{device_id}/telemetry", payload(older_at, 7.2)).state_updated

        points = history.history(device_id, older_at - timedelta(seconds=1), newest_at + timedelta(seconds=1))
        current = state.get(device_id)

        assert [point.timestamp for point in points] == [older_at, newest_at]
        assert current is not None
        assert current.timestamp == newest_at
        assert current.pressure == 7.8
    finally:
        _clear_test_data(clickhouse, redis)
        engine.dispose()


def _clear_test_data(clickhouse: object, redis: Redis) -> None:
    clickhouse.command(
        "ALTER TABLE telemetry_events DELETE WHERE device_id = "
        "'AHU-204-INGESTION-TEST' SETTINGS mutations_sync = 1"
    )
    redis.delete(f"aeris:device:{TEST_DEVICE_ID}")
