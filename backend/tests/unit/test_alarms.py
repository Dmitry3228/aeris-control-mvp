from datetime import UTC, datetime

from app.domain.alarms import AlarmAction, evaluate_alarm
from app.domain.telemetry import TelemetrySample
from app.models import Alarm, AlarmStatus


def test_alarm_deduplicates_active_violation() -> None:
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
    active = Alarm(
        device_id="AHU-204",
        rule_key="pressure_high",
        status=AlarmStatus.ACTIVE,
        opened_at=datetime(2026, 10, 2, 7, 59, tzinfo=UTC),
    )

    decision = evaluate_alarm(sample, active)

    assert decision.action is AlarmAction.UPDATE
    assert decision.rule_key == "pressure_high"


def test_alarm_closes_after_recovery() -> None:
    sample = TelemetrySample.from_payload(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.2,
            "power": 3.2,
            "connected": True,
        }
    )
    active = Alarm(
        device_id="AHU-204",
        rule_key="pressure_high",
        status=AlarmStatus.ACTIVE,
        opened_at=datetime(2026, 10, 2, 7, 59, tzinfo=UTC),
    )

    decision = evaluate_alarm(sample, active)

    assert decision.action is AlarmAction.CLOSE
    assert decision.rule_key == "pressure_high"


def test_pressure_at_exact_threshold_is_a_violation() -> None:
    sample = TelemetrySample.from_payload(
        {
            "deviceId": "AHU-204",
            "timestamp": "2026-10-02T08:00:00Z",
            "temperature": 21.4,
            "pressure": 7.5,
            "power": 3.2,
            "connected": True,
        }
    )

    decision = evaluate_alarm(sample, None)

    assert decision.action is AlarmAction.OPEN
    assert decision.rule_key == "pressure_high"
