from dataclasses import dataclass
from datetime import datetime
from math import isfinite


class TelemetryValidationError(ValueError):
    """Raised when a telemetry payload cannot be converted into a sample."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    device_id: str
    timestamp: datetime
    temperature: float
    pressure: float
    power: float
    connected: bool

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TelemetrySample":
        try:
            device_id = _string(payload, "deviceId")
            timestamp = _timestamp(payload, "timestamp")
            temperature = _number(payload, "temperature")
            pressure = _number(payload, "pressure")
            power = _number(payload, "power")
            connected = _boolean(payload, "connected")
        except TelemetryValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise TelemetryValidationError("invalid_payload", str(error)) from error
        sample = cls(
            device_id=device_id,
            timestamp=timestamp,
            temperature=temperature,
            pressure=pressure,
            power=power,
            connected=connected,
        )
        _validate_measurements(sample)
        return sample


def _required(payload: dict[str, object], name: str) -> object:
    if name not in payload:
        raise ValueError(f"missing required field: {name}")
    return payload[name]


def _string(payload: dict[str, object], name: str) -> str:
    value = _required(payload, name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _number(payload: dict[str, object], name: str) -> float:
    value = _required(payload, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _boolean(payload: dict[str, object], name: str) -> bool:
    value = _required(payload, name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _timestamp(payload: dict[str, object], name: str) -> datetime:
    value = _string(payload, name)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _validate_measurements(sample: TelemetrySample) -> None:
    measurements = {
        "temperature": sample.temperature,
        "pressure": sample.pressure,
        "power": sample.power,
    }
    if any(not isfinite(value) for value in measurements.values()):
        raise TelemetryValidationError("non_finite_value", "telemetry values must be finite")
    bounds = {
        "temperature": (-50.0, 100.0),
        "pressure": (0.0, 20.0),
        "power": (0.0, 1_000.0),
    }
    for name, value in measurements.items():
        minimum, maximum = bounds[name]
        if not minimum <= value <= maximum:
            raise TelemetryValidationError(
                "value_out_of_bounds",
                f"{name} must be between {minimum:g} and {maximum:g}",
            )
