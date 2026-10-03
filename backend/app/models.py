from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain.commands import CommandEvent, CommandKind, CommandStatus


class AlarmStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CLOSED = "CLOSED"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    equipment_type: Mapped[str] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceTelemetryCursor(Base):
    __tablename__ = "device_telemetry_cursors"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    latest_timestamp_us: Mapped[int] = mapped_column(BigInteger)
    latest_event_id: Mapped[str] = mapped_column(String(64))


class TelemetryInbox(Base):
    __tablename__ = "telemetry_inbox"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    topic: Mapped[str] = mapped_column(String(160))
    payload: Mapped[str] = mapped_column(Text)
    sample_timestamp_us: Mapped[int] = mapped_column(BigInteger, index=True)
    history_written: Mapped[bool] = mapped_column(Boolean, default=False)
    operational_done: Mapped[bool] = mapped_column(Boolean, default=False)
    operational_current: Mapped[bool] = mapped_column(Boolean, default=False)
    alarm_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alarm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    rule_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[AlarmStatus] = mapped_column(Enum(AlarmStatus), default=AlarmStatus.ACTIVE)
    severity: Mapped[str] = mapped_column(String(32), default="WARNING")
    message: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    kind: Mapped[CommandKind] = mapped_column(Enum(CommandKind))
    value: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[CommandStatus] = mapped_column(Enum(CommandStatus), default=CommandStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandEventRecord(Base):
    __tablename__ = "command_event_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id"), index=True)
    event: Mapped[CommandEvent] = mapped_column(Enum(CommandEvent))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
