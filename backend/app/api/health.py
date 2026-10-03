from collections.abc import Callable, Mapping

from fastapi import APIRouter


DependencyCheck = Callable[[], bool]
MetricsProvider = Callable[[], Mapping[str, object]]


def create_health_router(
    checks: Mapping[str, DependencyCheck],
    metrics: MetricsProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["health"])

    @router.get("/health")
    def health() -> dict[str, object]:
        dependencies = {name: _status(check) for name, check in checks.items()}
        overall = "ok" if all(status == "ok" for status in dependencies.values()) else "degraded"
        payload: dict[str, object] = {"status": overall, "dependencies": dependencies}
        if metrics is not None:
            payload["metrics"] = dict(metrics())
        return payload

    return router


def _status(check: DependencyCheck) -> str:
    try:
        return "ok" if check() else "unavailable"
    except Exception:
        return "unavailable"
