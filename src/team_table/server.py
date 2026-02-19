"""FastMCP server entry point for Team Table."""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from team_table.config import Config
from team_table.db import Database
from team_table.notifications import (
    EVENT_CONNECTED,
    EVENT_HEARTBEAT,
    SSENotificationBackend,
    configure_notifications,
    get_backend,
    make_event,
)
from team_table.tools import audit, context, messaging, registration, tasks

config = Config.from_env()
mcp = FastMCP("team-table", host=config.host, port=config.port)
db = Database(config)

registration.register_tools(mcp, db)
messaging.register_tools(mcp, db)
tasks.register_tools(mcp, db)
context.register_tools(mcp, db)
audit.register_tools(mcp, db)


# -- SSE push notification endpoint --

HEARTBEAT_INTERVAL = 30  # seconds


@mcp.custom_route("/events/{agent_name}", methods=["GET"])
async def stream_agent_events(request: Request):  # type: ignore[no-untyped-def]
    """SSE endpoint for real-time agent push notifications."""
    from sse_starlette import EventSourceResponse

    agent_name = request.path_params.get("agent_name", "")
    if not agent_name:
        return JSONResponse({"error": "agent_name required"}, status_code=400)

    # Verify agent is registered
    role = db.get_member_role(agent_name)
    if role is None:
        return JSONResponse(
            {"error": f"Agent '{agent_name}' is not registered or inactive"},
            status_code=403,
        )

    backend = get_backend()
    if not isinstance(backend, SSENotificationBackend):
        return JSONResponse(
            {"error": "Push notifications not available on this transport"},
            status_code=503,
        )

    queue = backend.subscribe(agent_name)

    async def event_stream():  # type: ignore[no-untyped-def]
        try:
            # Send connection confirmation
            yield make_event(EVENT_CONNECTED, {"agent": agent_name})

            while True:
                try:
                    # Wait for events with heartbeat timeout
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                    yield event
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield make_event(EVENT_HEARTBEAT, {})
                except asyncio.CancelledError:
                    break
        finally:
            backend.unsubscribe(agent_name)

    return EventSourceResponse(event_stream())


# -- Server startup --


def main() -> None:
    # Configure notification backend based on transport
    if config.transport in ("sse", "streamable-http"):
        configure_notifications(SSENotificationBackend())
    # STDIO uses the default NoOpNotificationBackend

    mcp.run(transport=config.transport)


if __name__ == "__main__":
    main()
