from datetime import timedelta

from app.config import load_settings


def test_runtime_safety_limits_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("AERIS_STALE_AFTER_SECONDS", "45")
    monkeypatch.setenv("AERIS_HISTORY_MAX_SPAN_DAYS", "5")
    monkeypatch.setenv("AERIS_HISTORY_POINT_BUDGET", "480")
    monkeypatch.setenv("AERIS_TELEMETRY_MAX_PAST_AGE_SECONDS", "7200")
    monkeypatch.setenv("AERIS_TELEMETRY_MAX_FUTURE_SKEW_SECONDS", "15")

    configured = load_settings()

    assert configured.stale_after_seconds == 45
    assert configured.history_max_span_days == 5
    assert configured.history_point_budget == 480
    assert configured.telemetry_max_past_age == timedelta(hours=2)
    assert configured.telemetry_max_future_skew == timedelta(seconds=15)
