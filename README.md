# Team Table

Multi-model AI team coordination via MCP (Model Context Protocol).

An MCP server that lets multiple AI instances discover each other, coordinate tasks, and communicate through a shared SQLite database.

## Quick Start

```bash
pip install -e ".[dev]"
```

### Register as MCP server in Claude Code

```bash
claude mcp add --transport stdio --scope user team-table -- \
  path/to/.venv/Scripts/python.exe -m team_table.server
```

## Architecture

Each Claude Code instance spawns its own STDIO MCP server process. All processes share one SQLite database (`~/.team-table/team_table.db`) using WAL mode for concurrent access.

## Tools (13)

- **Registration**: `register`, `deregister`, `list_members`, `heartbeat`
- **Messaging**: `send_message`, `get_messages`, `broadcast`
- **Task Board**: `create_task`, `list_tasks`, `claim_task`, `update_task`
- **Shared Context**: `share_context`, `get_shared_context`

## Development

```bash
pytest          # run tests
ruff check .    # lint
```

## License

GPL-3.0-or-later
