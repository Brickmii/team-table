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

## Network Mode (LAN)

Run the server over the network so other machines can connect:

```bash
# Start the server in SSE mode (or use streamable-http)
TEAM_TABLE_TRANSPORT=sse python -m team_table.server
```

From another PC on the LAN, register the remote server:

```bash
claude mcp add --transport sse team-table http://<host-ip>:8741/sse
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TEAM_TABLE_DB` | `~/.team-table/team_table.db` | Path to the SQLite database |
| `TEAM_TABLE_TRANSPORT` | `stdio` | Transport mode: `stdio`, `sse`, or `streamable-http` |
| `TEAM_TABLE_HOST` | `0.0.0.0` | Bind address for network transports |
| `TEAM_TABLE_PORT` | `8741` | Listen port for network transports |

## Architecture

Each Claude Code instance spawns its own STDIO MCP server process. All processes share one SQLite database (`~/.team-table/team_table.db`) using WAL mode for concurrent access. Alternatively, a single server can be run in network mode (SSE or streamable-http) to serve multiple clients over the LAN.

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
