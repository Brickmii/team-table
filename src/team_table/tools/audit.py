"""Audit log tool: get_audit_log."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def get_audit_log(
        agent_name: str = "",
        action: str = "",
        since: str = "",
        limit: int = 50,
    ) -> str:
        """Query audit entries with optional agent/action/since filters."""
        try:
            entries = db.get_audit_log(
                agent_name=agent_name or None,
                action=action or None,
                since=since or None,
                limit=min(limit, 200),
            )
            return json.dumps(entries)
        except ValidationError as e:
            return json.dumps({"error": e.message})
