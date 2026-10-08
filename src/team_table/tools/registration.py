"""Registration tools: register, deregister, list_members, heartbeat."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import PRIVILEGED_ROLES, Database
from team_table.notify import set_current_agent, with_notification
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def register(name: str, role: str = "agent", capabilities: str = "[]", token: str = "") -> str:
        """Join the team table. Capabilities is a JSON array of strings. Re-registering a name
        that is already at the table needs that name's token."""
        try:
            caps = json.loads(capabilities)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": f"Invalid capabilities JSON: {capabilities!r}"})
        if not isinstance(caps, list):
            return json.dumps({"error": "Capabilities must be a JSON array"})
        current = db.get_member_role(name)
        # an active name is that agent's: taking it over used to hand out a fresh token for it,
        # with any role
        if current is not None:
            if not db.config.require_tokens:
                return json.dumps({"error": f"'{name}' is already at the table"})
            if not db.validate_token(name, token):
                return json.dumps(
                    {"error": f"'{name}' is already at the table; re-registering needs its token"}
                )
        # privileged roles come from the operator, never from the caller
        if role in PRIVILEGED_ROLES and current != role and name not in db.config.admins:
            return json.dumps({
                "error": f"The {role} role is granted by the operator "
                "(python -m team_table.admin grant NAME ROLE), not self-assigned"
            })
        if current in PRIVILEGED_ROLES and role not in PRIVILEGED_ROLES:
            role = current  # re-registering doesn't quietly drop a granted role
        try:
            result = db.register(name, role, caps)
            token = db.issue_token(name)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        set_current_agent(name)
        return with_notification(db, json.dumps({**result, "token": token}), name)

    @mcp.tool()
    def deregister(name: str, token: str) -> str:
        """Leave the team table."""
        try:
            db.require_token(name, token)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        success = db.deregister(name)
        if success:
            return json.dumps({"status": "deregistered", "name": name})
        return json.dumps({"error": f"Member '{name}' not found"})

    @mcp.tool()
    def list_members(agent_name: str, token: str, include_inactive: bool = False) -> str:
        """See who's at the team table."""
        try:
            db.require_token(agent_name, token)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        members = db.list_members(include_inactive)
        return with_notification(db, json.dumps(members), agent_name)

    @mcp.tool()
    def heartbeat(name: str, token: str) -> str:
        """Update last-seen timestamp for an agent."""
        set_current_agent(name)
        try:
            db.require_token(name, token)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        success = db.heartbeat(name)
        if success:
            return with_notification(db, json.dumps({"status": "ok", "name": name}), name)
        return json.dumps({"error": f"Member '{name}' not found"})
