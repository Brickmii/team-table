# Team Table Roadmap

> Agreed upon by claude-code and claude-opus — 2026-02-19

**North star:** `pip install team-table` → instant multi-agent coordination, zero infrastructure.

---

## Phase 1 — Message Management (Quick Wins)

- [ ] Add `archived_at` nullable timestamp column to messages table
- [ ] `delete_message(id, agent_name)` — soft delete (sets `archived_at`), ownership check (sender or recipient only)
- [ ] `archive_message(id, agent_name)` — alias for soft delete, also marks as `read=true`
- [ ] `clear_inbox(agent_name, before_date?, sender?)` — bulk cleanup with optional filters
- [ ] `purge_messages(agent_name, before_date)` — hard delete, admin/lead role only
- [ ] Update `get_messages` to exclude archived by default, add `include_archived` flag
- [ ] Archived messages excluded from unread notification counts
- [ ] Role-based access: admin/lead can delete any message, regular agents only their own

## Phase 2 — Real-time Notifications (SSE Push)

- [ ] Define `NotificationBackend` protocol with `notify(agent_name, event)` method
- [ ] Implement `SSENotificationBackend` with per-agent `asyncio.Queue`
- [ ] Implement `NoOpNotificationBackend` for STDIO transport (no-op, clients poll)
- [ ] Module-level `configure_notifications(backend)` pattern for backend selection
- [ ] SSE endpoint at `GET /events/{agent_name}`
  - [ ] Verify agent is registered (check members table) before allowing connection
  - [ ] Send `connected` event immediately on stream open
  - [ ] 30s heartbeat events to keep connection alive
  - [ ] Graceful disconnect handling (remove queue from `_connections`)
- [ ] Wire `notify()` calls into `send_message` and `broadcast` tool handlers
- [ ] Poll daemon remains as fallback for STDIO transport
- [ ] Network transport startup calls `configure_notifications(SSENotificationBackend())`

## Phase 3 — Task-Message Integration

- [ ] Add optional `task_id` field to messages table
- [ ] `task_comment(task_id, agent_name, content, type?)` tool
  - Types: `comment`, `status_change`, `blocker`, `resolution`
- [ ] `get_task_detail(task_id)` — returns task + all comments + linked messages in chronological order
- [ ] Auto-update task `updated_at` on any linked message or comment activity
- [ ] Rich activity feed per task:
  ```
  [status_change] claude-code claimed task — 19:00
  [comment] claude-code: Starting on the SSE endpoint — 19:05
  [blocker] claude-opus: Need to decide on auth approach — 19:10
  [resolution] claude-code: Going with registered-agent check — 19:15
  [status_change] claude-code completed task — 19:30
  ```

## Phase 4 — Platform Evolution

- [ ] **Declarative agent roles/templates** — JSON/YAML files defining capabilities, default tools, behavioral guidelines. Agents adopt a role at `register()`, server enforces permissions.
- [ ] **Event-driven workflow automation** — simple trigger → condition → action rules:
  - `on task.all_subtasks_complete → set task.status = complete`
  - `on task.status = blocked → notify task.assignee`
  - `on agent.idle > 5min → assign next pending task`
- [ ] **Namespaced shared context** — evolve `share_context` with namespaces, TTL, and versioning:
  - `share_context(ns="project-alpha", key="api-spec", value=..., ttl=3600)`
  - Agents on the same project share a namespace, old context expires automatically
- [ ] **Append-only audit log** — every action (message sent, task claimed, context shared) logged to a separate table for debugging multi-agent workflows

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