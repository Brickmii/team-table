"""Push notification backends for real-time agent event delivery.

Two backends:
- SSENotificationBackend: queues events for SSE streaming (network transports)
- NoOpNotificationBackend: silent no-op (STDIO transport, clients poll instead)

Usage:
    from team_table.notifications import configure_notifications, notify

    configure_notifications(SSENotificationBackend())
    notify("alice", {"event": "message", "data": {...}})
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# -- Event types --

EVENT_MESSAGE = "message"
EVENT_BROADCAST = "broadcast"
EVENT_TASK_ASSIGNED = "task_assigned"
EVENT_TASK_UPDATED = "task_updated"
EVENT_CONNECTED = "connected"
EVENT_HEARTBEAT = "heartbeat"


def make_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized notification event."""
    return {"event": event_type, "data": json.dumps(data)}


# -- Backend protocol --


@runtime_checkable
class NotificationBackend(Protocol):
    """Protocol for notification delivery backends."""

    def notify(self, agent_name: str, event: dict[str, Any]) -> None:
        """Send a notification event to a specific agent."""
        ...

    def notify_all(self, event: dict[str, Any], exclude: str | None = None) -> None:
        """Send a notification event to all connected agents."""
        ...

    def subscribe(self, agent_name: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe an agent and return their event queue."""
        ...

    def unsubscribe(self, agent_name: str) -> None:
        """Remove an agent's subscription."""
        ...

    def is_connected(self, agent_name: str) -> bool:
        """Check if an agent has an active subscription."""
        ...


# -- SSE backend --


class SSENotificationBackend:
    """Notification backend that queues events for SSE streaming."""

    def __init__(self, max_queue_size: int = 100) -> None:
        self._connections: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._max_queue_size = max_queue_size

    def notify(self, agent_name: str, event: dict[str, Any]) -> None:
        """Send event to a specific agent's queue."""
        queue = self._connections.get(agent_name)
        if queue is None:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for agent %s, dropping event", agent_name)

    def notify_all(self, event: dict[str, Any], exclude: str | None = None) -> None:
        """Send event to all connected agents."""
        for name, queue in self._connections.items():
            if name == exclude:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full for agent %s, dropping event", name)

    def subscribe(self, agent_name: str) -> asyncio.Queue[dict[str, Any]]:
        """Create a queue for an agent and return it."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._max_queue_size
        )
        self._connections[agent_name] = queue
        return queue

    def unsubscribe(self, agent_name: str) -> None:
        """Remove an agent's queue."""
        self._connections.pop(agent_name, None)

    def is_connected(self, agent_name: str) -> bool:
        return agent_name in self._connections


# -- NoOp backend --


class NoOpNotificationBackend:
    """Silent backend for STDIO transport — all operations are no-ops."""

    def notify(self, agent_name: str, event: dict[str, Any]) -> None:
        del agent_name, event

    def notify_all(self, event: dict[str, Any], exclude: str | None = None) -> None:
        del event, exclude

    def subscribe(self, agent_name: str) -> asyncio.Queue[dict[str, Any]]:
        return asyncio.Queue()

    def unsubscribe(self, agent_name: str) -> None:
        pass

    def is_connected(self, agent_name: str) -> bool:
        return False


# -- Module-level configuration --

_backend: NotificationBackend = NoOpNotificationBackend()


def configure_notifications(backend: NotificationBackend) -> None:
    """Set the active notification backend. Call during server startup."""
    global _backend
    _backend = backend


def get_backend() -> NotificationBackend:
    """Return the active notification backend."""
    return _backend


def notify(agent_name: str, event: dict[str, Any]) -> None:
    """Send a notification to a specific agent via the active backend."""
    _backend.notify(agent_name, event)


def notify_all(event: dict[str, Any], exclude: str | None = None) -> None:
    """Send a notification to all connected agents via the active backend."""
    _backend.notify_all(event, exclude=exclude)
