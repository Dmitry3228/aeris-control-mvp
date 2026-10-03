import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


StreamMessage = dict[str, object]


@dataclass(frozen=True, slots=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[StreamMessage]


@dataclass(frozen=True, slots=True)
class TelemetrySubscription:
    epoch: str
    sequence: int
    snapshot: tuple[StreamMessage, ...]
    live: asyncio.Queue[StreamMessage]


class TelemetryStream:
    def __init__(self, queue_size: int = 256, epoch: str | None = None) -> None:
        self._queue_size = queue_size
        self._epoch = epoch or str(uuid4())
        self._sequence = 0
        self._latest: dict[str, StreamMessage] = {}
        self._subscribers: dict[UUID, _Subscriber] = {}
        self._lock = Lock()

    def publish(self, device_id: str, payload: Mapping[str, Any]) -> StreamMessage:
        return self._publish("telemetry", device_id, payload, retain=True)

    def publish_alarm(self, device_id: str, payload: Mapping[str, Any]) -> StreamMessage:
        return self._publish("alarm", device_id, payload, retain=False)

    def publish_command(self, device_id: str, payload: Mapping[str, Any]) -> StreamMessage:
        return self._publish("command", device_id, payload, retain=False)

    def _publish(
        self,
        event_type: str,
        device_id: str,
        payload: Mapping[str, Any],
        *,
        retain: bool,
    ) -> StreamMessage:
        with self._lock:
            self._sequence += 1
            message: StreamMessage = {
                "type": event_type,
                "epoch": self._epoch,
                "sequence": self._sequence,
                "deviceId": device_id,
                "payload": dict(payload),
            }
            if retain:
                self._latest[device_id] = message
            subscribers = tuple(self._subscribers.values())
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, message)
        return message

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[TelemetrySubscription]:
        token = uuid4()
        queue: asyncio.Queue[StreamMessage] = asyncio.Queue(maxsize=self._queue_size)
        subscriber = _Subscriber(asyncio.get_running_loop(), queue)
        with self._lock:
            snapshot = tuple(sorted(self._latest.values(), key=_message_sequence))
            epoch = self._epoch
            sequence = self._sequence
            self._subscribers[token] = subscriber
        try:
            yield TelemetrySubscription(
                epoch=epoch,
                sequence=sequence,
                snapshot=snapshot,
                live=queue,
            )
        finally:
            with self._lock:
                self._subscribers.pop(token, None)


def create_websocket_router(stream: TelemetryStream) -> APIRouter:
    router = APIRouter(prefix="/api/ws", tags=["telemetry"])

    @router.websocket("/telemetry")
    async def telemetry(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            async with stream.subscribe() as subscription:
                await websocket.send_json(
                    {
                        "type": "session",
                        "epoch": subscription.epoch,
                        "sequence": subscription.sequence,
                    }
                )
                for message in subscription.snapshot:
                    await websocket.send_json(message)
                while True:
                    await websocket.send_json(await subscription.live.get())
        except WebSocketDisconnect:
            return

    return router


def _offer(queue: asyncio.Queue[StreamMessage], message: StreamMessage) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(message)


def _message_sequence(message: StreamMessage) -> int:
    sequence = message["sequence"]
    return sequence if isinstance(sequence, int) else 0
