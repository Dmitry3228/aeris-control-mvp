from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.domain.commands import (
    COMMAND_TIMEOUT_SECONDS,
    CommandEvent,
    CommandKind,
    CommandStatus,
    transition_command,
)
from app.models import Command, CommandEventRecord, Device


logger = logging.getLogger(__name__)


class CommandPublisher(Protocol):
    def publish_command(self, command: "CommandView") -> None: ...


class UnknownDeviceError(LookupError):
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"Equipment '{device_id}' does not exist")


class CommandNotFoundError(LookupError):
    def __init__(self, command_id: UUID) -> None:
        self.command_id = command_id
        super().__init__(f"Command '{command_id}' does not exist")


class CommandPublishError(RuntimeError):
    def __init__(self, command_id: UUID, cause: Exception) -> None:
        self.command_id = command_id
        super().__init__(f"Command '{command_id}' was persisted but could not be published: {cause}")


@dataclass(frozen=True, slots=True)
class CommandView:
    id: UUID
    device_id: str
    kind: CommandKind
    value: float | None
    status: CommandStatus
    created_at: datetime
    timeout_at: datetime
    acknowledged_at: datetime | None


class CommandService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        publisher: CommandPublisher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, device_id: str, kind: CommandKind, value: float | None) -> CommandView:
        now = _utc(self._clock())
        with self._session_factory.begin() as session:
            if session.get(Device, device_id) is None:
                raise UnknownDeviceError(device_id)
            command = Command(
                device_id=device_id,
                kind=kind,
                value=value,
                status=CommandStatus.PENDING,
                created_at=now,
                timeout_at=now + timedelta(seconds=COMMAND_TIMEOUT_SECONDS),
            )
            session.add(command)
            session.flush()
            view = _view(command)

        try:
            self._publisher.publish_command(view)
        except Exception as error:
            logger.exception("Command publication failed", extra={"command_id": str(view.id)})
            raise CommandPublishError(view.id, error) from error
        return view

    def acknowledge(self, command_id: UUID, accepted: bool) -> CommandView:
        now = _utc(self._clock())
        with self._session_factory.begin() as session:
            timed_out = _transition_pending(
                session,
                command_id,
                CommandEvent.TIMEOUT,
                now,
                Command.timeout_at <= now,
            )
            if not timed_out:
                event = CommandEvent.ACKNOWLEDGE if accepted else CommandEvent.REJECT
                _transition_pending(
                    session,
                    command_id,
                    event,
                    now,
                    Command.timeout_at > now,
                )
            command = session.get(Command, command_id)
            if command is None:
                raise CommandNotFoundError(command_id)
            session.refresh(command)
            return _view(command)

    def expire(self, now: datetime) -> int:
        cutoff = _utc(now)
        with self._session_factory() as session:
            command_ids = session.scalars(
                select(Command.id).where(
                    Command.status == CommandStatus.PENDING,
                    Command.timeout_at <= cutoff,
                )
            ).all()
        expired = 0
        for command_id in command_ids:
            with self._session_factory.begin() as session:
                if _transition_pending(
                    session,
                    command_id,
                    CommandEvent.TIMEOUT,
                    cutoff,
                    Command.timeout_at <= cutoff,
                ):
                    expired += 1
        return expired

    def get(self, command_id: UUID) -> CommandView:
        with self._session_factory() as session:
            command = session.get(Command, command_id)
            if command is None:
                raise CommandNotFoundError(command_id)
            return _view(command)

    def list(self, device_id: str | None = None) -> list[CommandView]:
        statement = select(Command).order_by(Command.created_at.desc())
        if device_id is not None:
            statement = statement.where(Command.device_id == device_id)
        with self._session_factory() as session:
            return [_view(command) for command in session.scalars(statement).all()]


def _view(command: Command) -> CommandView:
    return CommandView(
        id=command.id,
        device_id=command.device_id,
        kind=command.kind,
        value=command.value,
        status=command.status,
        created_at=_utc(command.created_at),
        timeout_at=_utc(command.timeout_at),
        acknowledged_at=_utc(command.acknowledged_at) if command.acknowledged_at else None,
    )


def _transition_pending(
    session: Session,
    command_id: UUID,
    event: CommandEvent,
    occurred_at: datetime,
    deadline_condition: ColumnElement[bool],
) -> bool:
    target = transition_command(CommandStatus.PENDING, event)
    acknowledged_at = occurred_at if event is not CommandEvent.TIMEOUT else None
    result = cast(
        CursorResult[Any],
        session.execute(
            update(Command)
            .where(
                Command.id == command_id,
                Command.status == CommandStatus.PENDING,
                deadline_condition,
            )
            .values(status=target, acknowledged_at=acknowledged_at)
        )
    )
    if result.rowcount != 1:
        return False
    session.add(
        CommandEventRecord(
            command_id=command_id,
            event=event,
            occurred_at=occurred_at,
        )
    )
    return True


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
