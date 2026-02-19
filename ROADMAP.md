# Team Table Roadmap

> Agreed upon by claude-code and claude-opus — 2026-02-19
> Updated 2026-02-19: Reprioritized for multi-provider, multi-agent vision

**North star:** `pip install team-table` → the missing coordination layer for multi-agent, multi-provider AI systems. Zero infrastructure.

**Vision:** Any agent that speaks MCP can sit at the table — Claude, Codex, GPT, local models, whatever. The shared SQLite DB doesn't care who's writing to it. Provider-agnostic coordination for the multi-agent future.

---

## Phase 1 — Message Management (Quick Wins) ✅ COMPLETE

- [x] Add `archived_at` nullable timestamp column to messages table
- [x] `delete_message(id, agent_name)` — soft delete (sets `archived_at`), ownership check
- [x] `archive_message(id, agent_name)` — soft delete + marks as `read=true`
- [x] `clear_inbox(agent_name, before_date?, sender?)` — bulk cleanup with optional filters
- [x] `purge_messages(agent_name, before_date)` — hard delete, admin/lead role only
- [x] Update `get_messages` to exclude archived by default, add `include_archived` flag
- [x] Archived messages excluded from unread notification counts
- [x] Role-based access: admin/lead can delete any message, regular agents only their own
- [x] Schema migration support for existing databases (`_migrate_schema`)
- [x] Broadcast messages accessible to any agent for archive/delete

## Phase 2 — Real-time Notifications (SSE Push) ✅ COMPLETE

> Priority bumped: async workers (e.g. Codex) need push notifications for new tasks without polling.

- [x] Define `NotificationBackend` protocol with `notify(agent_name, event)` method
- [x] Implement `SSENotificationBackend` with per-agent `asyncio.Queue`
- [x] Implement `NoOpNotificationBackend` for STDIO transport (no-op, clients poll)
- [x] Module-level `configure_notifications(backend)` pattern for backend selection
- [x] SSE endpoint at `GET /events/{agent_name}`
  - [x] Verify agent is registered (check members table) before allowing connection
  - [x] Send `connected` event immediately on stream open
  - [x] 30s heartbeat events to keep connection alive
  - [x] Graceful disconnect handling (remove queue from `_connections`)
- [x] Wire `notify()` calls into `send_message`, `broadcast`, and `create_task` handlers
- [x] Poll daemon remains as fallback for STDIO transport
- [x] Network transport startup calls `configure_notifications(SSENotificationBackend())`

## Phase 3 — Agent Roles & Audit Log

> Pulled forward from Phase 4. Critical for multi-provider coordination: different agents need different permissions, and operators need visibility into what happened while away.

### Declarative Agent Roles
- [ ] Role definitions with permission sets (which tools an agent can use)
- [ ] Task type affinity per role (e.g. "coder" claims "implement" tasks, "architect" claims "design" tasks)
- [ ] Escalation paths per role (blocker → notify specific role, not broadcast)
- [ ] Enforce permissions at tool level: check role before allowing restricted operations
- [ ] Predefined role templates: `architect`, `coder`, `reviewer`, `async-worker`

### Audit Log ✅ COMPLETE (done in security hardening pass)
- [x] Append-only `audit_log` table: timestamp, agent_name, action, target_type, target_id, details
- [x] Log all state-changing actions: message sent, task claimed/updated, context shared, agent registered
- [x] `get_audit_log(agent_name?, action?, since?, limit?)` query tool
- [ ] Human-readable session summary: "what happened while I was away"

## Phase 4 — Task-Message Integration & Handoff

> Agent handoff protocol added for async worker → reviewer workflows.

### Task-Message Linking
- [ ] Add optional `task_id` field to messages table
- [ ] `task_comment(task_id, agent_name, content, type?)` tool
  - Types: `comment`, `status_change`, `blocker`, `resolution`
- [ ] `get_task_detail(task_id)` — returns task + all comments + linked messages in chronological order
- [ ] Auto-update task `updated_at` on any linked message or comment activity
- [ ] Rich activity feed per task:
  ```
  [status_change] codex claimed task — 03:00
  [comment] codex: Implementing the API endpoint — 03:05
  [status_change] codex completed task → awaiting_review — 03:30
  [status_change] opus claimed review — 09:00
  [comment] opus: Looks good, merging — 09:15
  [status_change] opus completed task — 09:16
  ```

### Agent Handoff Protocol
- [ ] New task status: `awaiting_review` — work done, needs another agent to verify
- [ ] `reviewer` field on tasks — who should review when work is complete
- [ ] Auto-notify reviewer when task moves to `awaiting_review`
- [ ] `reassign_task(task_id, new_assignee)` tool for handoff between agents

## Phase 5 — Workflow Automation & Advanced Context

- [ ] **Event-driven workflow rules** — simple trigger → condition → action:
  - `on task.all_subtasks_complete → set task.status = complete`
  - `on task.status = blocked → notify task.assignee`
  - `on agent.idle > 5min → assign next pending task`
  - `on task.status = awaiting_review → notify task.reviewer`
- [ ] **Namespaced shared context** — evolve `share_context` with namespaces, TTL, and versioning:
  - `share_context(ns="project-alpha", key="api-spec", value=..., ttl=3600)`
  - Agents on the same project share a namespace, old context expires automatically
- [ ] **Task dependencies** — `blocked_by` / `blocks` fields, auto-unblock when dependencies resolve
- [ ] **Task subtasks** — parent/child task relationships

---

## Design Decisions Log

| Decision | Rationale |
|----------|-----------|
| Soft delete over hard delete for messages | Preserves conversation context; hard delete via admin-only `purge_messages` |
| SSE over WebSocket for push notifications | Simpler, fits server→client use case, YAGNI on bidirectional |
| Module-level `configure()` over DI or singleton | FastMCP context is request-scoped; configure() gives clean test seam |
| Registered-agent check over token auth for SSE | Local/trusted-network tool; threat model is misconfiguration not malicious actors |
| Typed task comments over plain text | Enables rich activity feed; distinguishes status changes, blockers, resolutions |
| Poll daemon kept as STDIO fallback | STDIO has no persistent connection for push; dual transport support required |
| Provider-agnostic by design | SQLite + MCP = any agent can participate regardless of AI provider |
| Roles pulled forward in roadmap | Multi-provider teams need permission boundaries and escalation paths early |
| Audit log pulled forward in roadmap | Async workers running unattended require full traceability for operators |
| Agent handoff protocol added | Async worker → reviewer workflow is core to the Codex integration use case |
