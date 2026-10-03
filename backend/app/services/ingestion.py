import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from re import fullmatch
from threading import Lock
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.alarms import PRESSURE_HIGH_RULE_KEY, AlarmAction, AlarmDecision, evaluate_alarm
from app.domain.telemetry import TelemetrySample, TelemetryValidationError
from app.models import (
    Alarm,
    AlarmStatus,
    Device,
    DeviceTelemetryCursor,
    TelemetryInbox,
)


class TelemetryHistoryWriter(Protocol):
    def append(self, sample: TelemetrySample, event_id: str) -> None: ...


class DeviceStateWriter(Protocol):
    def put_if_newer(self, sample: TelemetrySample) -> bool: ...


@dataclass(frozen=True, slots=True)
class AlarmUpdate:
    id: UUID
    device_id: str
    rule_key: str
    action: AlarmAction
    status: AlarmStatus
    severity: str
    message: str
    opened_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    accepted: bool
    reason: str | None
    state_updated: bool
    alarm_decision: AlarmDecision | None
    event_id: str | None = None
    duplicate: bool = False
    alarm_update: AlarmUpdate | None = None


class TelemetryIngestionService:
    def __init__(
        self,
        history_store: TelemetryHistoryWriter,
        state_store: DeviceStateWriter,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] | None = None,
        maximum_past_age: timedelta = timedelta(days=7),
        maximum_future_skew: timedelta = timedelta(seconds=30),
    ) -> None:
        self._history_store = history_store
        self._state_store = state_store
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._maximum_past_age = maximum_past_age
        self._maximum_future_skew = maximum_future_skew
        self._rejections: Counter[str] = Counter()
        self._accepted = 0
        self._duplicates = 0
        self._processing_failures = 0
        self._metrics_lock = Lock()

    def process(self, topic: str, payload: bytes) -> IngestionResult:
        try:
            decoded = json.loads(payload)
            if not isinstance(decoded, dict):
                raise TelemetryValidationError("invalid_payload", "payload must be a JSON object")
            sample = TelemetrySample.from_payload(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._rejected("invalid_payload")
        except TelemetryValidationError as error:
            return self._rejected(error.reason)

        topic_match = fullmatch(r"devices/([^/]+)/telemetry", topic)
        if topic_match is None:
            return self._rejected("invalid_topic")
        if topic_match.group(1) != sample.device_id:
            return self._rejected("topic_device_mismatch")
        now = _utc(self._clock())
        if (
            sample.timestamp < now - self._maximum_past_age
            or sample.timestamp > now + self._maximum_future_skew
        ):
            return self._rejected("timestamp_out_of_bounds")

        return self._process_validated(topic, payload, sample, _event_id(sample), now)

    def recover_pending(self) -> int:
        """Resume every telemetry event accepted before an interrupted processing attempt."""
        with self._session_factory() as session:
            pending = [
                (item.event_id, item.topic, item.payload)
                for item in session.scalars(
                    select(TelemetryInbox)
                    .where(TelemetryInbox.completed.is_(False))
                    .order_by(TelemetryInbox.created_at, TelemetryInbox.event_id)
                )
            ]

        recovered = 0
        for event_id, topic, encoded in pending:
            decoded = json.loads(encoded)
            if not isinstance(decoded, dict):
                raise RuntimeError(f"telemetry inbox event {event_id} has an invalid payload")
            sample = TelemetrySample.from_payload(decoded)
            if _event_id(sample) != event_id:
                raise RuntimeError(f"telemetry inbox event {event_id} failed its integrity check")
            result = self._process_validated(
                topic,
                encoded.encode(),
                sample,
                event_id,
                _utc(self._clock()),
            )
            if result.accepted:
                recovered += 1
        return recovered

    def _process_validated(
        self,
        topic: str,
        payload: bytes,
        sample: TelemetrySample,
        event_id: str,
        now: datetime,
    ) -> IngestionResult:

        with self._session_factory.begin() as session:
            if session.get(Device, sample.device_id) is None:
                return self._rejected("unknown_device")
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is not None and inbox.completed:
                with self._metrics_lock:
                    self._duplicates += 1
                return IngestionResult(
                    True,
                    None,
                    False,
                    None,
                    event_id=event_id,
                    duplicate=True,
                )
            if inbox is None:
                session.add(
                    TelemetryInbox(
                        event_id=event_id,
                        device_id=sample.device_id,
                        topic=topic,
                        payload=payload.decode(),
                        sample_timestamp_us=_timestamp_us(sample),
                    )
                )

        try:
            self._write_history_once(event_id, sample)
            decision, alarm_update = self._apply_operational_once(event_id, sample)
            state_updated = self._write_current_state(event_id, sample)
            self._complete(event_id, now)
        except Exception as error:
            self._record_failure(event_id, error)
            raise

        with self._metrics_lock:
            self._accepted += 1
        return IngestionResult(
            True,
            None,
            state_updated,
            decision,
            event_id=event_id,
            alarm_update=alarm_update,
        )

    def metrics(self) -> dict[str, object]:
        with self._metrics_lock:
            return {
                "accepted": self._accepted,
                "duplicates": self._duplicates,
                "processingFailures": self._processing_failures,
                "rejected": dict(sorted(self._rejections.items())),
            }

    def _rejected(self, reason: str) -> IngestionResult:
        with self._metrics_lock:
            self._rejections[reason] += 1
        return IngestionResult(False, reason, False, None)

    def _write_history_once(self, event_id: str, sample: TelemetrySample) -> None:
        with self._session_factory() as session:
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is None:
                raise RuntimeError(f"telemetry inbox event {event_id} disappeared")
            if inbox.history_written:
                return
        self._history_store.append(sample, event_id)
        with self._session_factory.begin() as session:
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is None:
                raise RuntimeError(f"telemetry inbox event {event_id} disappeared")
            inbox.history_written = True

    def _apply_operational_once(
        self, event_id: str, sample: TelemetrySample
    ) -> tuple[AlarmDecision, AlarmUpdate | None]:
        with self._session_factory.begin() as session:
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is None:
                raise RuntimeError(f"telemetry inbox event {event_id} disappeared")
            if inbox.operational_done:
                return AlarmDecision(AlarmAction.NONE, None, None), self._stored_alarm_update(
                    session, inbox
                )

            timestamp_us = _timestamp_us(sample)
            cursor_write = (
                sqlite_insert(DeviceTelemetryCursor)
                .values(
                    device_id=sample.device_id,
                    latest_timestamp_us=timestamp_us,
                    latest_event_id=event_id,
                )
                .on_conflict_do_update(
                    index_elements=[DeviceTelemetryCursor.device_id],
                    set_={
                        "latest_timestamp_us": timestamp_us,
                        "latest_event_id": event_id,
                    },
                    where=DeviceTelemetryCursor.latest_timestamp_us < timestamp_us,
                )
                .returning(DeviceTelemetryCursor.latest_event_id)
            )
            won = session.scalar(cursor_write) == event_id
            decision = AlarmDecision(AlarmAction.NONE, None, None)
            alarm: Alarm | None = None
            if won:
                active_alarm = session.scalar(
                    select(Alarm)
                    .where(
                        Alarm.device_id == sample.device_id,
                        Alarm.rule_key == PRESSURE_HIGH_RULE_KEY,
                        Alarm.status.in_([AlarmStatus.ACTIVE, AlarmStatus.ACKNOWLEDGED]),
                    )
                    .order_by(Alarm.opened_at.desc())
                )
                decision = evaluate_alarm(sample, active_alarm)
                alarm = self._apply_alarm_decision(session, sample, active_alarm, decision)
                if alarm is not None:
                    session.flush()
                    inbox.alarm_action = decision.action.value
                    inbox.alarm_id = str(alarm.id)
            inbox.operational_done = True
            inbox.operational_current = won
            alarm_update = _alarm_update(alarm, decision.action) if alarm is not None else None
            return decision, alarm_update

    def _write_current_state(self, event_id: str, sample: TelemetrySample) -> bool:
        with self._session_factory() as session:
            inbox = session.get(TelemetryInbox, event_id)
            cursor = session.get(DeviceTelemetryCursor, sample.device_id)
            is_current = (
                inbox is not None
                and inbox.operational_current
                and cursor is not None
                and cursor.latest_event_id == event_id
            )
        if not is_current:
            return False
        return self._state_store.put_if_newer(sample)

    def _complete(self, event_id: str, completed_at: datetime) -> None:
        with self._session_factory.begin() as session:
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is None:
                raise RuntimeError(f"telemetry inbox event {event_id} disappeared")
            inbox.completed = True
            inbox.completed_at = completed_at
            inbox.last_error = None

    def _record_failure(self, event_id: str, error: Exception) -> None:
        with self._metrics_lock:
            self._processing_failures += 1
        with self._session_factory.begin() as session:
            inbox = session.get(TelemetryInbox, event_id)
            if inbox is not None:
                inbox.attempt_count += 1
                inbox.last_error = str(error)[:1_000]

    @staticmethod
    def _stored_alarm_update(session: Session, inbox: TelemetryInbox) -> AlarmUpdate | None:
        if inbox.alarm_id is None or inbox.alarm_action is None:
            return None
        alarm = session.get(Alarm, UUID(inbox.alarm_id))
        if alarm is None:
            return None
        return _alarm_update(alarm, AlarmAction(inbox.alarm_action))

    @staticmethod
    def _apply_alarm_decision(
        session: Session,
        sample: TelemetrySample,
        active_alarm: Alarm | None,
        decision: AlarmDecision,
    ) -> Alarm | None:
        if decision.action is AlarmAction.OPEN:
            alarm = Alarm(
                device_id=sample.device_id,
                rule_key=decision.rule_key or "",
                status=AlarmStatus.ACTIVE,
                message=decision.message or "",
                opened_at=sample.timestamp,
                updated_at=sample.timestamp,
            )
            session.add(alarm)
            return alarm
        if decision.action is AlarmAction.UPDATE and active_alarm is not None:
            active_alarm.message = decision.message or ""
            active_alarm.updated_at = sample.timestamp
            return active_alarm
        if decision.action is AlarmAction.CLOSE and active_alarm is not None:
            active_alarm.status = AlarmStatus.CLOSED
            active_alarm.message = decision.message or ""
            active_alarm.closed_at = sample.timestamp
            active_alarm.updated_at = sample.timestamp
            return active_alarm
        return None


def _timestamp_us(sample: TelemetrySample) -> int:
    timestamp = sample.timestamp.astimezone(UTC)
    return int(timestamp.timestamp()) * 1_000_000 + timestamp.microsecond


def _event_id(sample: TelemetrySample) -> str:
    canonical = json.dumps(
        {
            "connected": sample.connected,
            "deviceId": sample.device_id,
            "power": sample.power,
            "pressure": sample.pressure,
            "temperature": sample.temperature,
            "timestamp": sample.timestamp.astimezone(UTC).isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode()).hexdigest()


def _alarm_update(alarm: Alarm, action: AlarmAction) -> AlarmUpdate:
    return AlarmUpdate(
        id=alarm.id,
        device_id=alarm.device_id,
        rule_key=alarm.rule_key,
        action=action,
        status=alarm.status,
        severity=alarm.severity,
        message=alarm.message,
        opened_at=_utc(alarm.opened_at),
        updated_at=_utc(alarm.updated_at),
        acknowledged_at=_utc(alarm.acknowledged_at) if alarm.acknowledged_at else None,
        closed_at=_utc(alarm.closed_at) if alarm.closed_at else None,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
