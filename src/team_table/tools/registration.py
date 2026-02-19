"""Registration tools: register, deregister, list_members, heartbeat."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.notify import set_current_agent, with_notification
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def register(name: str, role: str = "agent", capabilities: str = "[]") -> str:
        """Join the team table. Capabilities is a JSON array of strings."""
        try:
            caps = json.loads(capabilities)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": f"Invalid capabilities JSON: {capabilities!r}"})
        if not isinstance(caps, list):
            return json.dumps({"error": "Capabilities must be a JSON array"})
        try:
            result = db.register(name, role, caps)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        set_current_agent(name)
        return with_notification(db, json.dumps(result))

    @mcp.tool()
    def deregister(name: str) -> str:
        """Leave the team table."""
        success = db.deregister(name)
        if success:
            return json.dumps({"status": "deregistered", "name": name})
        return json.dumps({"error": f"Member '{name}' not found"})

    @mcp.tool()
    def list_members(include_inactive: bool = False) -> str:
        """See who's at the team table."""
        members = db.list_members(include_inactive)
        return with_notification(db, json.dumps(members))

    @mcp.tool()
    def heartbeat(name: str) -> str:
        """Update last-seen timestamp for an agent."""
        set_current_agent(name)
        success = db.heartbeat(name)
        if success:
            return with_notification(db, json.dumps({"status": "ok", "name": name}))
        return json.dumps({"error": f"Member '{name}' not found"})
