import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from uuid import uuid4


correlation_id: ContextVar[str] = ContextVar("correlation_id", default="system")


def begin_correlation(value: str | None = None) -> tuple[str, Token[str]]:
    request_id = value or str(uuid4())
    return request_id, correlation_id.set(request_id)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": correlation_id.get(),
        }
        for key in (
            "method",
            "path",
            "status_code",
            "duration_ms",
            "topic",
            "command_id",
            "device_id",
            "event_id",
            "reason",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)
