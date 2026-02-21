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
| `TEAM_TABLE_REQUIRE_TOKENS` | `true` | Require auth tokens for tool calls and SSE |

## Architecture

Each Claude Code instance spawns its own STDIO MCP server process. All processes share one SQLite database (`~/.team-table/team_table.db`) using WAL mode for concurrent access. Alternatively, a single server can be run in network mode (SSE or streamable-http) to serve multiple clients over the LAN.

## Tools (13)

- **Registration**: `register`, `deregister`, `list_members`, `heartbeat`
- **Messaging**: `send_message`, `get_messages`, `broadcast`
- **Task Board**: `create_task`, `list_tasks`, `claim_task`, `update_task`
- **Shared Context**: `share_context`, `get_shared_context`

### Auth Tokens

`register` returns a per-agent token. All tool calls (and the SSE event stream) require
the token unless `TEAM_TABLE_REQUIRE_TOKENS=false`.

## Poll Daemon (Auto-Messaging)

By default, agents must manually check for messages. The poll daemon automates this — it monitors an agent's inbox and auto-responds, only escalating to the user when needed.

### How It Works

1. Polls the database every 30 seconds for unread messages
2. Sends an acknowledgement reply to each incoming message
3. **Escalates to the user** (stops auto-replying) when:
   - The total auto-reply count exceeds the limit (default: 13)
   - A message contains a question or decision request (e.g. "should we…?", "please approve", "what do you think")
4. Notifies the sender with an `[AUTO]` message explaining the escalation

### Usage

```bash
# Start polling for an agent (default: 30s interval, 13 message max)
python scripts/poll_daemon.py claude-opus

# Custom interval and message limit
python scripts/poll_daemon.py claude-opus --interval 15 --max-messages 13

# With a custom database path
TEAM_TABLE_DB=/path/to/db python scripts/poll_daemon.py claude-opus
```

### Safety

- **Hard message cap** prevents runaway agent-to-agent loops
- **Question detection** forces human review on decisions
- **Pull-based** — no exposed network endpoints
- **Graceful shutdown** via Ctrl-C or SIGTERM
- All activity is logged to the terminal with timestamps

## Development

```bash
pytest          # run tests
ruff check .    # lint
```

### Recommended Workflow (PyCharm + OAuth)

For day-to-day development, use a JetBrains-first flow:

1. Run and debug code/tests in PyCharm.
2. Use browser-based OAuth sign-in from PyCharm for provider access.
3. Keep `team-table` auth-token enforcement enabled (`TEAM_TABLE_REQUIRE_TOKENS=true`).
4. Treat each agent as a distinct identity with its own token and role.

Token guidance for the table process:

- Register each agent once, capture its returned token, and store it in IDE run configs or environment variables.
- Rotate/revoke tokens when an agent is repurposed or a workstation changes ownership.
- Never share one token across multiple agents; tokens are agent-scoped.

Future multi-agent expansion:

- Add agents incrementally (coding, review, QA, design, ops) with explicit roles and capabilities.
- Keep one shared database (`TEAM_TABLE_DB`) for coordination and audit history.
- Use network mode (`sse` or `streamable-http`) when agents run across multiple machines.

## License

GPL-3.0-or-later
