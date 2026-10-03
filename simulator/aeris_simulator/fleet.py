import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


Message = tuple[str, bytes]


@dataclass(frozen=True, slots=True)
class SimulatedDevice:
    device_id: str
    seed: int
    index: int

    def telemetry(self, sequence: int, timestamp: datetime) -> Message:
        pressure = (
            (8.1 + sequence % 3 * 0.05 if sequence % 10 < 8 else 7.1)
            if self.device_id == "AHU-204"
            else 5.0 + (self.seed % 5) * 0.05 + self.index * 0.08 + sequence % 3 * 0.03
        )
        payload = {
            "deviceId": self.device_id,
            "timestamp": _timestamp(timestamp),
            "temperature": round(18.0 + (self.seed % 30) / 10 + self.index * 0.4 + sequence % 5 * 0.1, 1),
            "pressure": round(pressure, 2),
            "power": round(2.0 + (self.seed % 6) * 0.1 + self.index * 0.2 + sequence % 4 * 0.1, 1),
            "connected": True,
        }
        return f"devices/{self.device_id}/telemetry", _encode(payload)


@dataclass(frozen=True, slots=True)
class Fleet:
    devices: tuple[SimulatedDevice, ...]

    def telemetry_messages(self, sequence: int, timestamp: datetime) -> list[Message]:
        return [device.telemetry(sequence, timestamp) for device in self.devices]

    def acknowledge(self, topic: str, payload: bytes) -> Message | None:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "devices" or parts[2] != "commands":
            return None
        device_id = parts[1]
        if not any(device.device_id == device_id for device in self.devices):
            return None
        try:
            command = json.loads(payload)
            command_id = str(UUID(command["commandId"]))
            kind = command["kind"]
            value = command.get("value")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        accepted = kind in {"START", "STOP"} or (
            kind == "SETPOINT" and isinstance(value, (int, float)) and not isinstance(value, bool) and 16 <= value <= 30
        )
        return (
            f"devices/{device_id}/command-acks",
            _encode({"commandId": command_id, "accepted": accepted}),
        )


def create_fleet(count: int, seed: int = 17) -> Fleet:
    if count < 1:
        raise ValueError("devices must be at least 1")
    return Fleet(
        tuple(
            SimulatedDevice(device_id=f"AHU-{index + 201:03d}", seed=seed, index=index)
            for index in range(count)
        )
    )


def _encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
