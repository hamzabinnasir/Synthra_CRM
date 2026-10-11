"""Background worker: claims queued webhook events and runs the handler registered for their channel.

Handlers are registered per channel by the bot engine. A channel with no handler is never claimed,
so its events simply wait in the queue instead of being dropped.

Logging rule: never log payloads, tokens or exception messages (they can contain URLs with tokens).
Only the event id, channel and exception class name are logged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional

from .queue import EventQueue, QueuedEvent

log = logging.getLogger("app.worker")

Handler = Callable[[QueuedEvent], Awaitable[None]]


class PermanentError(Exception):
    """Raise from a handler when retrying cannot help (for example a deleted account). Buries the event."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Worker:
    def __init__(self, queue: EventQueue, handlers: Dict[str, Handler], *, concurrency: int = 8,
                 per_tenant: int = 3, poll_seconds: float = 1.0, worker_id: Optional[str] = None) -> None:
        self.queue, self.handlers = queue, handlers
        self.concurrency, self.per_tenant, self.poll = concurrency, per_tenant, poll_seconds
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task] = set()

    async def _run_one(self, event: QueuedEvent) -> None:
        try:
            await self.handlers[event.channel](event)
        except PermanentError as exc:
            await self.queue.fail(event.id, event.attempts, type(exc).__name__ + ": " + str(exc)[:200], _now(), permanent=True)
            log.warning("event_dead", extra={"event_id": event.id, "channel": event.channel})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # retried with backoff; only the class name is stored and logged
            status = await self.queue.fail(event.id, event.attempts, type(exc).__name__, _now())
            log.warning("event_failed", extra={"event_id": event.id, "channel": event.channel,
                                               "error": type(exc).__name__, "next": status})
        else:
            await self.queue.complete(event.id, _now())
        finally:
            self._slots.release()

    async def run_once(self) -> int:
        """Claim up to the free capacity and start handlers. Returns how many events were claimed."""
        free = self.concurrency - len(self._tasks)
        if free <= 0 or not self.handlers:
            return 0
        events = await self.queue.claim(self.worker_id, list(self.handlers), _now(), limit=free, per_tenant=self.per_tenant)
        for event in events:
            await self._slots.acquire()
            task = asyncio.create_task(self._run_one(event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return len(events)

    async def drain(self) -> None:
        """Wait for in-flight handlers (used by tests and on shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def run(self, stop: asyncio.Event) -> None:
        last_housekeeping = 0.0
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            try:
                if loop.time() - last_housekeeping > 60:
                    last_housekeeping = loop.time()
                    revived = await self.queue.reclaim_stale(_now())
                    await self.queue.purge(_now())
                    if revived:
                        log.warning("events_reclaimed", extra={"count": revived})
                claimed = await self.run_once()
            except Exception as exc:
                log.error("worker_loop_error", extra={"error": type(exc).__name__})
                claimed = 0
            if not claimed:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll)
                except asyncio.TimeoutError:
                    pass
        await self.drain()
