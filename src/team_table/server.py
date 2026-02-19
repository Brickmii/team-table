"""FastMCP server entry point for Team Table."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from team_table.config import Config
from team_table.db import Database
from team_table.tools import context, messaging, registration, tasks

mcp = FastMCP("team-table")
config = Config.from_env()
db = Database(config)

registration.register_tools(mcp, db)
messaging.register_tools(mcp, db)
tasks.register_tools(mcp, db)
context.register_tools(mcp, db)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
