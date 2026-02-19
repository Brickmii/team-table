"""Tests for real-time notification backends."""

from __future__ import annotations

import json

from team_table.notifications import (
    EVENT_MESSAGE,
    NoOpNotificationBackend,
    SSENotificationBackend,
    configure_notifications,
    make_event,
    notify,
    notify_all,
)


def test_sse_notify_specific_agent() -> None:
    backend = SSENotificationBackend()
    queue = backend.subscribe("alice")
    event = make_event(EVENT_MESSAGE, {"id": 1})

    backend.notify("alice", event)

    received = queue.get_nowait()
    assert received["event"] == EVENT_MESSAGE
    assert json.loads(received["data"])["id"] == 1


def test_sse_notify_all_with_exclude() -> None:
    backend = SSENotificationBackend()
    alice_q = backend.subscribe("alice")
    bob_q = backend.subscribe("bob")
    event = make_event(EVENT_MESSAGE, {"id": 2})

    backend.notify_all(event, exclude="alice")

    assert bob_q.get_nowait()["event"] == EVENT_MESSAGE
    assert alice_q.empty()


def test_configure_backend_delegates_notify_calls() -> None:
    configure_notifications(NoOpNotificationBackend())
    backend = SSENotificationBackend()
    queue = backend.subscribe("alice")
    configure_notifications(backend)

    event = make_event(EVENT_MESSAGE, {"id": 3})
    notify("alice", event)
    notify_all(event, exclude="alice")

    assert queue.get_nowait()["event"] == EVENT_MESSAGE
    assert queue.empty()

    configure_notifications(NoOpNotificationBackend())


def test_noop_backend_is_safe() -> None:
    backend = NoOpNotificationBackend()
    backend.notify("alice", make_event(EVENT_MESSAGE, {"id": 4}))
    backend.notify_all(make_event(EVENT_MESSAGE, {"id": 5}))
    q = backend.subscribe("alice")
    assert q.empty()
    assert backend.is_connected("alice") is False
