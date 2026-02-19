"""Messaging tools for direct messages, broadcasts, and message management."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def send_message(sender: str, recipient: str, content: str) -> str:
        """Send a direct message to another agent."""
        try:
            result = db.send_message(sender, recipient, content)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def get_messages(
        agent_name: str, include_read: bool = False, include_archived: bool = False
    ) -> str:
        """Check inbox. Marks direct messages as read."""
        messages = db.get_messages(agent_name, include_read, include_archived)
        return json.dumps(messages)

    @mcp.tool()
    def broadcast(sender: str, content: str) -> str:
        """Send a message to all agents."""
        try:
            result = db.broadcast(sender, content)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def delete_message(message_id: int, agent_name: str) -> str:
        """Soft-delete a message. Owner or admin/lead can delete."""
        result = db.delete_message(message_id, agent_name)
        if result is None:
            return json.dumps({"error": f"Message {message_id} not found"})
        return json.dumps(result)

    @mcp.tool()
    def archive_message(message_id: int, agent_name: str) -> str:
        """Archive a message: soft-delete and mark as read."""
        result = db.archive_message(message_id, agent_name)
        if result is None:
            return json.dumps({"error": f"Message {message_id} not found"})
        return json.dumps(result)

    @mcp.tool()
    def clear_inbox(agent_name: str, before_date: str = "", sender: str = "") -> str:
        """Bulk archive messages. Filters: before_date (ISO), sender."""
        try:
            result = db.clear_inbox(agent_name, before_date or None, sender or None)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def purge_messages(agent_name: str, before_date: str) -> str:
        """Hard-delete messages older than before_date. Requires admin or lead role."""
        try:
            result = db.purge_messages(agent_name, before_date)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})
