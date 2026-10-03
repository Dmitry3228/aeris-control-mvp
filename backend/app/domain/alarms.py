from dataclasses import dataclass
from enum import StrEnum

from app.domain.telemetry import TelemetrySample
from app.models import Alarm, AlarmStatus


PRESSURE_THRESHOLD_BAR = 7.5
PRESSURE_HIGH_RULE_KEY = "pressure_high"


class AlarmAction(StrEnum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    CLOSE = "CLOSE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class AlarmDecision:
    action: AlarmAction
    rule_key: str | None
    message: str | None


def evaluate_alarm(sample: TelemetrySample, active: Alarm | None) -> AlarmDecision:
    violating = sample.pressure >= PRESSURE_THRESHOLD_BAR
    matching_active = (
        active is not None
        and active.device_id == sample.device_id
        and active.rule_key == PRESSURE_HIGH_RULE_KEY
        and active.status is not AlarmStatus.CLOSED
    )

    if violating:
        action = AlarmAction.UPDATE if matching_active else AlarmAction.OPEN
        return AlarmDecision(
            action=action,
            rule_key=PRESSURE_HIGH_RULE_KEY,
            message=(
                f"High discharge pressure: {sample.pressure:.1f} bar meets or exceeds "
                f"{PRESSURE_THRESHOLD_BAR:.1f} bar"
            ),
        )
    if matching_active:
        return AlarmDecision(
            action=AlarmAction.CLOSE,
            rule_key=PRESSURE_HIGH_RULE_KEY,
            message="Pressure recovered below threshold",
        )
    return AlarmDecision(action=AlarmAction.NONE, rule_key=None, message=None)
