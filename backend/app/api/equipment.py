from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.clickhouse_store import TelemetryPoint
from app.adapters.redis_state import DeviceState
from app.models import Device


class DeviceStateReader(Protocol):
    def get(self, device_id: str) -> DeviceState | None: ...


class TelemetryHistoryReader(Protocol):
    def history(
        self, device_id: str, start: datetime, end: datetime, max_points: int
    ) -> list[TelemetryPoint]: ...


class EquipmentNotFoundError(LookupError):
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"Equipment '{device_id}' does not exist")


class InvalidHistoryRangeError(ValueError):
    pass


class OperationalStatus(StrEnum):
    RUNNING = "RUNNING"
    OFFLINE = "OFFLINE"
    STALE = "STALE"
    NO_DATA = "NO_DATA"


class StateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(serialization_alias="deviceId")
    timestamp: datetime
    temperature: float
    pressure: float
    power: float
    connected: bool


class TelemetryPointResponse(BaseModel):
    timestamp: datetime
    temperature: float
    pressure: float
    power: float
    connected: bool


class EquipmentSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    equipment_type: str = Field(serialization_alias="equipmentType")
    location: str | None
    enabled: bool
    operational_status: OperationalStatus = Field(serialization_alias="operationalStatus")
    state: StateResponse | None


class EquipmentDetail(EquipmentSummary):
    history: list[TelemetryPointResponse]


def create_equipment_router(
    session_factory: sessionmaker[Session],
    state_store: DeviceStateReader,
    history_store: TelemetryHistoryReader,
    clock: Callable[[], datetime] | None = None,
    history_max_span: timedelta = timedelta(days=7),
    history_point_budget: int = 600,
    stale_after: timedelta = timedelta(seconds=30),
) -> APIRouter:
    now = clock or (lambda: datetime.now(UTC))
    router = APIRouter(prefix="/api/equipment", tags=["equipment"])

    @router.get("", response_model=list[EquipmentSummary])
    def list_equipment() -> list[EquipmentSummary]:
        with session_factory() as session:
            devices = session.scalars(select(Device).order_by(Device.id)).all()
            observed_at = now()
            return [
                _summary(device, state_store.get(device.id), observed_at, stale_after)
                for device in devices
            ]

    @router.get("/{device_id}", response_model=EquipmentDetail)
    def equipment_detail(
        device_id: str,
        start: datetime | None = Query(default=None),
        end: datetime | None = Query(default=None),
    ) -> EquipmentDetail:
        if (start is None) != (end is None):
            raise InvalidHistoryRangeError(
                "Both start and end are required when requesting history"
            )
        if start is not None and end is not None and start > end:
            raise InvalidHistoryRangeError("History start must be before or equal to end")
        if start is not None and end is not None and end - start > history_max_span:
            days = int(history_max_span.total_seconds() // (24 * 60 * 60))
            raise InvalidHistoryRangeError(f"History range cannot exceed {days} days")
        with session_factory() as session:
            device = session.get(Device, device_id)
            if device is None:
                raise EquipmentNotFoundError(device_id)
            summary = _summary(device, state_store.get(device.id), now(), stale_after)
        points = (
            history_store.history(device_id, start, end, history_point_budget)
            if start and end
            else []
        )
        return EquipmentDetail(
            **summary.model_dump(),
            history=[_history_point(point) for point in points],
        )

    return router


def _summary(
    device: Device,
    state: DeviceState | None,
    now: datetime,
    stale_after: timedelta,
) -> EquipmentSummary:
    return EquipmentSummary(
        id=device.id,
        name=device.name,
        equipment_type=device.equipment_type,
        location=device.location,
        enabled=device.enabled,
        operational_status=_operational_status(state, now, stale_after),
        state=_state(state) if state else None,
    )


def _operational_status(
    state: DeviceState | None,
    now: datetime,
    stale_after: timedelta,
) -> OperationalStatus:
    if state is None:
        return OperationalStatus.NO_DATA
    if not state.connected:
        return OperationalStatus.OFFLINE
    if _utc(now) - _utc(state.timestamp) > stale_after:
        return OperationalStatus.STALE
    return OperationalStatus.RUNNING


def _state(state: DeviceState) -> StateResponse:
    return StateResponse(
        device_id=state.device_id,
        timestamp=state.timestamp,
        temperature=state.temperature,
        pressure=state.pressure,
        power=state.power,
        connected=state.connected,
    )


def _history_point(point: TelemetryPoint) -> TelemetryPointResponse:
    return TelemetryPointResponse(
        timestamp=point.timestamp,
        temperature=point.temperature,
        pressure=point.pressure,
        power=point.power,
        connected=point.connected,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
