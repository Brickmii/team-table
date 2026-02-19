"""Messaging tools: send_message, get_messages, broadcast."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def send_message(sender: str, recipient: str, content: str) -> str:
        """Send a direct message to another agent."""
        result = db.send_message(sender, recipient, content)
        return json.dumps(result)

    @mcp.tool()
    def get_messages(agent_name: str, include_read: bool = False) -> str:
        """Check inbox. Marks direct messages as read."""
        messages = db.get_messages(agent_name, include_read)
        return json.dumps(messages)

    @mcp.tool()
    def broadcast(sender: str, content: str) -> str:
        """Send a message to all agents."""
        result = db.broadcast(sender, content)
        return json.dumps(result)
