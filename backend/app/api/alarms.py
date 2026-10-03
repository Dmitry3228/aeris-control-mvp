from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import Alarm, AlarmStatus


class AlarmNotFoundError(LookupError):
    def __init__(self, alarm_id: UUID) -> None:
        self.alarm_id = alarm_id
        super().__init__(f"Alarm '{alarm_id}' does not exist")


class AlarmResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    device_id: str = Field(serialization_alias="deviceId")
    rule_key: str = Field(serialization_alias="ruleKey")
    status: AlarmStatus
    severity: str
    message: str
    opened_at: datetime = Field(serialization_alias="openedAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    acknowledged_at: datetime | None = Field(serialization_alias="acknowledgedAt")
    closed_at: datetime | None = Field(serialization_alias="closedAt")


def create_alarms_router(
    session_factory: sessionmaker[Session], clock: Callable[[], datetime]
) -> APIRouter:
    router = APIRouter(prefix="/api/alarms", tags=["alarms"])

    @router.get("", response_model=list[AlarmResponse])
    def list_alarms() -> list[AlarmResponse]:
        with session_factory() as session:
            alarms = session.scalars(select(Alarm).order_by(Alarm.opened_at.desc())).all()
            return [_view(alarm) for alarm in alarms]

    @router.post("/{alarm_id}/ack", response_model=AlarmResponse)
    def acknowledge_alarm(alarm_id: UUID) -> AlarmResponse:
        with session_factory.begin() as session:
            acknowledged_at = clock()
            session.execute(
                update(Alarm)
                .where(Alarm.id == alarm_id, Alarm.status == AlarmStatus.ACTIVE)
                .values(
                    status=AlarmStatus.ACKNOWLEDGED,
                    acknowledged_at=acknowledged_at,
                    updated_at=acknowledged_at,
                )
            )
            alarm = session.get(Alarm, alarm_id)
            if alarm is None:
                raise AlarmNotFoundError(alarm_id)
            session.refresh(alarm)
            return _view(alarm)

    return router


def _view(alarm: Alarm) -> AlarmResponse:
    return AlarmResponse(
        id=alarm.id,
        device_id=alarm.device_id,
        rule_key=alarm.rule_key,
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
