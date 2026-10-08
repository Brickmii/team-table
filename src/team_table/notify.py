"""Notification badge system — appends unread message info to tool responses."""

from __future__ import annotations

import json

from team_table.db import Database

# Track which agent name this server process is using
_current_agent: str | None = None


def set_current_agent(name: str) -> None:
    global _current_agent
    _current_agent = name


def get_current_agent() -> str | None:
    return _current_agent


def with_notification(db: Database, response: str, agent: str | None = None) -> str:
    """Append the caller's unread-message badge to a tool response.

    The agent is the authenticated caller, passed in. A process-wide "current agent" (still kept
    for the STDIO transport's own bookkeeping) is never used here: with several clients on one
    network server it pointed at whoever called last, and one agent's response carried another
    agent's unread previews."""
    if not agent:
        return response
    count = db.unread_count(agent)
    if count == 0:
        return response
    previews = db.unread_preview(agent, limit=2)
    badge = {
        "_notification": {
            "unread_messages": count,
            "preview": [
                f"{p['sender']}: {p['content']}" for p in previews
            ],
            "action": (
                f"You have {count} unread message(s). "
                f'Call get_messages(agent_name="{agent}") to read and respond to them.'
            ),
        }
    }
    # Merge notification into the response
    try:
        data = json.loads(response)
        if isinstance(data, dict):
            data.update(badge)
            return json.dumps(data)
        elif isinstance(data, list):
            return json.dumps({"result": data, **badge})
    except (json.JSONDecodeError, TypeError):
        pass
    return response + "\n\n" + json.dumps(badge)
