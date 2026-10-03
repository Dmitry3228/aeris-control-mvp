from dataclasses import dataclass
from datetime import timedelta
from os import getenv


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    redis_url: str
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_username: str
    clickhouse_password: str
    mqtt_host: str
    mqtt_port: int
    stale_after_seconds: int
    history_max_span_days: int
    history_point_budget: int
    telemetry_max_past_age: timedelta
    telemetry_max_future_skew: timedelta


def load_settings() -> Settings:
    return Settings(
        database_url=getenv("AERIS_DATABASE_URL", "sqlite:///./aeris.db"),
        redis_url=getenv("REDIS_URL", "redis://localhost:6379/0"),
        clickhouse_host=getenv("CLICKHOUSE_HOST", "localhost"),
        clickhouse_port=int(getenv("CLICKHOUSE_PORT", "8124")),
        clickhouse_username=getenv("CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=getenv("CLICKHOUSE_PASSWORD", ""),
        mqtt_host=getenv("MQTT_HOST", "localhost"),
        mqtt_port=int(getenv("MQTT_PORT", "1883")),
        stale_after_seconds=int(getenv("AERIS_STALE_AFTER_SECONDS", "30")),
        history_max_span_days=int(getenv("AERIS_HISTORY_MAX_SPAN_DAYS", "7")),
        history_point_budget=int(getenv("AERIS_HISTORY_POINT_BUDGET", "600")),
        telemetry_max_past_age=timedelta(
            seconds=int(getenv("AERIS_TELEMETRY_MAX_PAST_AGE_SECONDS", "604800"))
        ),
        telemetry_max_future_skew=timedelta(
            seconds=int(getenv("AERIS_TELEMETRY_MAX_FUTURE_SKEW_SECONDS", "30"))
        ),
    )


settings = load_settings()
