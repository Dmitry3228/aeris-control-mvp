import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.clickhouse_store import ClickHouseTelemetryStore, TelemetryPoint
from app.adapters.mqtt_client import MqttRuntime
from app.adapters.redis_state import DeviceState, RedisDeviceState
from app.bootstrap import initialize_database
from app.api.alarms import AlarmNotFoundError, create_alarms_router
from app.api.commands import create_commands_router
from app.api.equipment import (
    DeviceStateReader,
    EquipmentNotFoundError,
    InvalidHistoryRangeError,
    TelemetryHistoryReader,
    create_equipment_router,
)
from app.api.health import DependencyCheck, MetricsProvider, create_health_router
from app.api.websocket import TelemetryStream, create_websocket_router
from app.config import Settings, load_settings
from app.db import SessionLocal
from app.logging_config import begin_correlation, correlation_id
from app.services.command_service import (
    CommandNotFoundError,
    CommandPublishError,
    CommandPublisher,
    CommandService,
    CommandView,
    UnknownDeviceError,
)
from app.services.ingestion import TelemetryIngestionService


logger = logging.getLogger(__name__)


class UnavailableStateStore:
    def get(self, device_id: str) -> DeviceState | None:
        del device_id
        return None


class UnavailableHistoryStore:
    def history(
        self, device_id: str, start: datetime, end: datetime, max_points: int
    ) -> list[TelemetryPoint]:
        del device_id, start, end, max_points
        return []


class UnconfiguredCommandPublisher:
    def publish_command(self, command: CommandView) -> None:
        del command
        raise RuntimeError("command publisher is not configured")


class DeferredStateStore(UnavailableStateStore):
    def __init__(self) -> None:
        self.target: RedisDeviceState | None = None

    def get(self, device_id: str) -> DeviceState | None:
        if self.target is None:
            return None
        return self.target.get(device_id)

    def is_healthy(self) -> bool:
        return self.target is not None and self.target.is_healthy()


class DeferredHistoryStore(UnavailableHistoryStore):
    def __init__(self) -> None:
        self.target: ClickHouseTelemetryStore | None = None

    def history(
        self, device_id: str, start: datetime, end: datetime, max_points: int
    ) -> list[TelemetryPoint]:
        if self.target is None:
            return []
        return self.target.history(device_id, start, end, max_points)

    def is_healthy(self) -> bool:
        return self.target is not None and self.target.is_healthy()


class DeferredCommandPublisher(UnconfiguredCommandPublisher):
    def __init__(self) -> None:
        self.target: MqttRuntime | None = None

    def publish_command(self, command: CommandView) -> None:
        if self.target is None:
            super().publish_command(command)
        else:
            self.target.publish_command(command)

    def is_healthy(self) -> bool:
        return self.target is not None and self.target.is_healthy()

    def metrics(self) -> Mapping[str, object]:
        return self.target.metrics() if self.target is not None else {}


class ProductionRuntime:
    def __init__(
        self,
        mqtt_runtime: MqttRuntime,
        state_store: RedisDeviceState,
        history_store: ClickHouseTelemetryStore,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._mqtt_runtime = mqtt_runtime
        self._state_store = state_store
        self._history_store = history_store
        self._session_factory = session_factory

    def start(self) -> None:
        initialize_database(self._session_factory)
        self._mqtt_runtime.start()

    def stop(self) -> None:
        try:
            self._mqtt_runtime.stop()
        finally:
            try:
                self._state_store.close()
            finally:
                self._history_store.close()


class RuntimeLifecycle(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


RuntimeFactory = Callable[[CommandService, TelemetryStream], RuntimeLifecycle]


def create_app(
    session_factory: sessionmaker[Session] = SessionLocal,
    state_store: DeviceStateReader | None = None,
    history_store: TelemetryHistoryReader | None = None,
    command_publisher: CommandPublisher | None = None,
    dependency_checks: Mapping[str, DependencyCheck] | None = None,
    clock: Callable[[], datetime] | None = None,
    telemetry_stream: TelemetryStream | None = None,
    runtime_factory: RuntimeFactory | None = None,
    expiry_interval: float = 1.0,
    history_max_span_days: int = 7,
    history_point_budget: int = 600,
    stale_after_seconds: int = 30,
    runtime_metrics: MetricsProvider | None = None,
) -> FastAPI:
    now = clock or (lambda: datetime.now(UTC))
    state = state_store or UnavailableStateStore()
    history = history_store or UnavailableHistoryStore()
    publisher = command_publisher or UnconfiguredCommandPublisher()
    command_service = CommandService(session_factory, publisher, now)
    stream = telemetry_stream or TelemetryStream()
    checks = dependency_checks or {
        "database": lambda: _database_available(session_factory),
        "redis": lambda: False,
        "clickhouse": lambda: False,
        "mqtt": lambda: False,
    }

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime = runtime_factory(command_service, stream) if runtime_factory else None
        expiry_task: asyncio.Task[None] | None = None
        try:
            if runtime is not None:
                await asyncio.to_thread(runtime.start)
            expiry_task = asyncio.create_task(
                _expire_commands(command_service, now, expiry_interval),
                name="command-expiry",
            )
            yield
        finally:
            if expiry_task is not None:
                expiry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await expiry_task
            if runtime is not None:
                await asyncio.to_thread(runtime.stop)

    application = FastAPI(title="Aeris Control API", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Any]) -> Any:
        request_id, token = begin_correlation(request.headers.get("x-correlation-id"))
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        else:
            response.headers["X-Correlation-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((monotonic() - started) * 1000, 2),
                },
            )
            return response
        finally:
            correlation_id.reset(token)

    application.include_router(create_health_router(checks, runtime_metrics))
    application.include_router(
        create_equipment_router(
            session_factory,
            state,
            history,
            clock=now,
            history_max_span=timedelta(days=history_max_span_days),
            history_point_budget=history_point_budget,
            stale_after=timedelta(seconds=stale_after_seconds),
        )
    )
    application.include_router(create_alarms_router(session_factory, now))
    application.include_router(create_commands_router(command_service))
    application.include_router(create_websocket_router(stream))
    _install_problem_handlers(application)
    return application


async def _expire_commands(
    service: CommandService,
    clock: Callable[[], datetime],
    interval: float,
) -> None:
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(service.expire, clock())


def create_production_app(
    configuration: Settings | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> FastAPI:
    configured = configuration or load_settings()
    state = DeferredStateStore()
    history = DeferredHistoryStore()
    publisher = DeferredCommandPublisher()
    stream = TelemetryStream()

    def runtime_factory(
        command_service: CommandService, telemetry_stream: TelemetryStream
    ) -> RuntimeLifecycle:
        state_store = RedisDeviceState(configured.redis_url)
        try:
            history_store = ClickHouseTelemetryStore(
                host=configured.clickhouse_host,
                port=configured.clickhouse_port,
                username=configured.clickhouse_username,
                password=configured.clickhouse_password,
            )
        except Exception:
            state_store.close()
            raise
        try:
            mqtt_runtime = MqttRuntime(
                host=configured.mqtt_host,
                port=configured.mqtt_port,
                ingestion=TelemetryIngestionService(
                    history_store,
                    state_store,
                    session_factory,
                    maximum_past_age=configured.telemetry_max_past_age,
                    maximum_future_skew=configured.telemetry_max_future_skew,
                ),
                command_service=command_service,
                telemetry_stream=telemetry_stream,
            )
        except Exception:
            state_store.close()
            history_store.close()
            raise
        state.target = state_store
        history.target = history_store
        publisher.target = mqtt_runtime
        return ProductionRuntime(mqtt_runtime, state_store, history_store, session_factory)

    return create_app(
        session_factory=session_factory,
        state_store=state,
        history_store=history,
        command_publisher=publisher,
        dependency_checks={
            "database": lambda: _database_available(session_factory),
            "redis": state.is_healthy,
            "clickhouse": history.is_healthy,
            "mqtt": publisher.is_healthy,
        },
        telemetry_stream=stream,
        runtime_factory=runtime_factory,
        runtime_metrics=publisher.metrics,
        history_max_span_days=configured.history_max_span_days,
        history_point_budget=configured.history_point_budget,
        stale_after_seconds=configured.stale_after_seconds,
    )


def _database_available(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return True


def _install_problem_handlers(application: FastAPI) -> None:
    @application.exception_handler(EquipmentNotFoundError)
    async def equipment_not_found(
        request: Request, error: EquipmentNotFoundError
    ) -> JSONResponse:
        return _problem(request, 404, "Equipment not found", str(error))

    @application.exception_handler(UnknownDeviceError)
    async def command_equipment_not_found(
        request: Request, error: UnknownDeviceError
    ) -> JSONResponse:
        return _problem(request, 404, "Equipment not found", str(error))

    @application.exception_handler(AlarmNotFoundError)
    async def alarm_not_found(request: Request, error: AlarmNotFoundError) -> JSONResponse:
        return _problem(request, 404, "Alarm not found", str(error))

    @application.exception_handler(CommandNotFoundError)
    async def command_not_found(request: Request, error: CommandNotFoundError) -> JSONResponse:
        return _problem(request, 404, "Command not found", str(error))

    @application.exception_handler(CommandPublishError)
    async def command_publish_failed(request: Request, error: CommandPublishError) -> JSONResponse:
        detail = f"Command '{error.command_id}' was persisted but could not be published"
        return _problem(request, 503, "Command publication unavailable", detail)

    @application.exception_handler(InvalidHistoryRangeError)
    async def invalid_history_range(
        request: Request, error: InvalidHistoryRangeError
    ) -> JSONResponse:
        return _problem(request, 422, "Invalid history range", str(error))

    @application.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, error: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            422,
            "Request validation failed",
            "The request did not match the expected schema",
            errors=jsonable_encoder(error.errors()),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        detail = error.detail if isinstance(error.detail, str) else "The request could not be completed"
        return _problem(request, error.status_code, "HTTP error", detail)


def _problem(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    **extensions: Any,
) -> JSONResponse:
    content: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        **extensions,
    }
    return JSONResponse(content=content, status_code=status_code, media_type="application/problem+json")


app = create_production_app()
