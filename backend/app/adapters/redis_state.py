import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from redis import Redis

from app.domain.telemetry import TelemetrySample


PUT_IF_NEWER = """
local current = redis.call('HGET', KEYS[1], 'timestamp')
if not current or tonumber(ARGV[1]) > tonumber(current) then
    redis.call('HSET', KEYS[1], 'timestamp', ARGV[1], 'sample', ARGV[2])
    return 1
end
return 0
"""


@dataclass(frozen=True, slots=True)
class DeviceState:
    device_id: str
    timestamp: datetime
    temperature: float
    pressure: float
    power: float
    connected: bool

    @classmethod
    def from_sample(cls, sample: TelemetrySample) -> "DeviceState":
        return cls(
            device_id=sample.device_id,
            timestamp=sample.timestamp,
            temperature=sample.temperature,
            pressure=sample.pressure,
            power=sample.power,
            connected=sample.connected,
        )


class RedisDeviceState:
    def __init__(self, url: str = "redis://localhost:6379/0", key_prefix: str = "aeris:device:") -> None:
        self._redis: Redis = Redis.from_url(url, decode_responses=True)
        self._key_prefix = key_prefix

    def put_if_newer(self, sample: TelemetrySample) -> bool:
        state = DeviceState.from_sample(sample)
        written = self._redis.eval(
            PUT_IF_NEWER,
            1,
            self._key(sample.device_id),
            str(_timestamp_score(sample.timestamp)),
            json.dumps(_state_payload(state)),
        )
        return bool(written)

    def get(self, device_id: str) -> DeviceState | None:
        encoded = self._redis.hget(self._key(device_id), "sample")
        if not isinstance(encoded, str):
            return None
        payload = json.loads(encoded)
        sample = TelemetrySample.from_payload(payload)
        return DeviceState.from_sample(sample)

    def is_healthy(self) -> bool:
        return bool(self._redis.ping())

    def close(self) -> None:
        self._redis.close()

    def _key(self, device_id: str) -> str:
        return f"{self._key_prefix}{device_id}"


def _state_payload(state: DeviceState) -> dict[str, object]:
    payload = asdict(state)
    return {
        "deviceId": payload["device_id"],
        "timestamp": state.timestamp.isoformat(),
        "temperature": payload["temperature"],
        "pressure": payload["pressure"],
        "power": payload["power"],
        "connected": payload["connected"],
    }


def _timestamp_score(timestamp: datetime) -> int:
    utc_timestamp = timestamp.astimezone(UTC)
    return int(utc_timestamp.timestamp()) * 1_000_000 + utc_timestamp.microsecond
