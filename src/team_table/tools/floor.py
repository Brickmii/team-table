"""The floor: a table's conversation, one speaker at a time (say, pass_floor, request_floor, take_floor,
floor, conversation)."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    def guarded(agent: str, token: str, act):  # type: ignore[no-untyped-def]
        try:
            db.require_token(agent, token)
            return json.dumps(act())
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def floor(agent_name: str, token: str, channel: str) -> str:
        """Who has the floor at a table, who waits for it, and how long the holder has left."""
        return guarded(agent_name, token, lambda: db.floor(channel))

    @mcp.tool()
    def say(agent_name: str, token: str, channel: str, content: str) -> str:
        """Say something at the table: when you hold the floor, or it's free (you take it)."""
        return guarded(agent_name, token, lambda: db.say(channel, agent_name, content))

    @mcp.tool()
    def pass_floor(agent_name: str, token: str, channel: str, to: str | None = None) -> str:
        """Give the floor: to a named seat, else to whoever waits, else to the next seat; "" leaves it free."""
        return guarded(agent_name, token, lambda: db.pass_floor(channel, agent_name, to))

    @mcp.tool()
    def request_floor(agent_name: str, token: str, channel: str) -> str:
        """Ask to speak: a free floor is yours now; else you wait your turn."""
        return guarded(agent_name, token, lambda: db.request_floor(channel, agent_name))

    @mcp.tool()
    def take_floor(agent_name: str, token: str, channel: str) -> str:
        """The person or the lead seat takes the floor now."""
        return guarded(agent_name, token, lambda: db.take_floor(channel, agent_name))

    @mcp.tool()
    def conversation(agent_name: str, token: str, channel: str, after_id: int = 0,
                     limit: int = 50) -> str:
        """What was said at the table after `after_id`, oldest first."""
        return guarded(agent_name, token, lambda: db.conversation(channel, after_id, limit))
