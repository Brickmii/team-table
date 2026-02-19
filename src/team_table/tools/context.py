"""Shared context tools: share_context, get_shared_context."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def share_context(agent_name: str, key: str, value: str) -> str:
        """Store a key-value pair in shared context. Value should be a JSON string."""
        try:
            result = db.share_context(key, value, agent_name)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def get_shared_context(key: str = "") -> str:
        """Retrieve shared context. Returns all entries if no key specified."""
        result = db.get_shared_context(key or None)
        return json.dumps(result)
