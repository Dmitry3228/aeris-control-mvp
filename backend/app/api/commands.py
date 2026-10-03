from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.domain.commands import CommandKind, CommandStatus
from app.services.command_service import CommandService, CommandView


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CommandRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    device_id: str
    kind: CommandKind
    value: float | None = None


class CommandResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    device_id: str = Field(serialization_alias="deviceId")
    kind: CommandKind
    value: float | None
    status: CommandStatus
    created_at: datetime = Field(serialization_alias="createdAt")
    timeout_at: datetime = Field(serialization_alias="timeoutAt")
    acknowledged_at: datetime | None = Field(serialization_alias="acknowledgedAt")


def create_commands_router(service: CommandService) -> APIRouter:
    router = APIRouter(prefix="/api/commands", tags=["commands"])

    @router.get("", response_model=list[CommandResponse])
    def list_commands(device_id: str | None = Query(default=None, alias="deviceId")) -> list[CommandResponse]:
        return [_response(command) for command in service.list(device_id)]

    @router.post("", response_model=CommandResponse, status_code=status.HTTP_201_CREATED)
    def create_command(request: CommandRequest) -> CommandResponse:
        return _response(service.create(request.device_id, request.kind, request.value))

    return router


def _response(command: CommandView) -> CommandResponse:
    return CommandResponse(
        id=command.id,
        device_id=command.device_id,
        kind=command.kind,
        value=command.value,
        status=command.status,
        created_at=command.created_at,
        timeout_at=command.timeout_at,
        acknowledged_at=command.acknowledged_at,
    )
