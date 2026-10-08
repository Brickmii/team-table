"""SQLite database layer with WAL mode and thread-local connections."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime

from team_table.config import Config
from team_table.validation import (
    ValidationError,
    validate_agent_name,
    validate_capabilities,
    validate_context_key,
    validate_context_tokens,
    validate_context_value,
    validate_iso_date,
    validate_message_content,
    validate_priority,
    validate_refs,
    validate_role,
    validate_task_description,
    validate_task_result,
    validate_task_status,
    validate_task_title,
)

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'agent',
    capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'medium',
    creator TEXT NOT NULL,
    assignee TEXT,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_context (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    set_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broadcast_reads (
    agent_name TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (agent_name, message_id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS broadcast_hidden (
    agent_name TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (agent_name, message_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
"""

# -- Rate limiting --
# In-memory rate limiter: tracks (sender -> list of timestamps)
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_MESSAGES = 30  # max messages per window (tasks and shared context count too)
PRIVILEGED_ROLES = ("admin", "lead")
# roles only the operator grants: the privileged ones, and "person" (their requests go first)
OPERATOR_ROLES = ("admin", "lead", "person")

# -- Turn tokens: task trees, budgets and the size cap --
DEFAULT_BUDGET_TASKS = 40   # tasks one request may spawn, all levels together
DEFAULT_BUDGET_DEPTH = 4    # how deep hand-offs may go
MAX_BUDGET_TASKS = 500
MAX_BUDGET_DEPTH = 20
TOKEN_SHARE = 0.05          # a task for a member is at most 5 % of its context
CHARS_PER_TOKEN = 3         # a conservative estimate for English and code
FINISHED = ("done", "cancelled")
TASK_COLUMNS = (
    ("parent_id", "INTEGER"), ("root_id", "INTEGER"), ("depth", "INTEGER NOT NULL DEFAULT 0"),
    ("kind", "TEXT NOT NULL DEFAULT ''"), ("refs", "TEXT NOT NULL DEFAULT '[]'"),
    ("reviewer", "TEXT"), ("origin", "TEXT NOT NULL DEFAULT 'agent'"),
    ("budget_tasks", "INTEGER"), ("budget_depth", "INTEGER"),
)
_PRIORITY_ORDER = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
AUDIT_LIMIT_MAX = 200


class Database:
    """Thread-safe SQLite database wrapper using thread-local connections."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._ensure_dir()
        self._init_schema()

    def _ensure_dir(self) -> None:
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._private(self.config.db_path.parent, 0o700)

    @staticmethod
    def _private(path, mode: int) -> None:
        """The table holds every message, task and token hash: owner-only (POSIX)."""
        if os.name == "posix":
            try:
                os.chmod(path, mode)
            except OSError:
                pass

    def _private_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            p = str(self.config.db_path) + suffix
            if os.path.exists(p):
                self._private(p, 0o600)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(_local, "conn") or _local.conn is None or _local.db_path != str(
            self.config.db_path
        ):
            conn = sqlite3.connect(
                str(self.config.db_path),
                timeout=self.config.busy_timeout_ms / 1000,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={self.config.busy_timeout_ms}")
            conn.row_factory = sqlite3.Row
            _local.conn = conn
            _local.db_path = str(self.config.db_path)
        return _local.conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(SCHEMA)
        conn.commit()
        self._migrate_schema()
        self._private_files()

    def _migrate_schema(self) -> None:
        """Apply incremental schema migrations for existing databases."""
        conn = self._get_conn()
        columns = [("messages", "archived_at", "TEXT"), ("members", "context_tokens", "INTEGER")]
        columns += [("tasks", name, kind) for name, kind in TASK_COLUMNS]
        for table, name, kind in columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

    # -- Audit logging --

    def log_action(
        self,
        agent_name: str,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        details: str | dict | None = None,
    ) -> None:
        """Append an entry to the audit log."""
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        if details is None:
            details_json = "{}"
        elif isinstance(details, str):
            details_json = details
        else:
            details_json = json.dumps(details)
        conn.execute(
            (
                "INSERT INTO audit_log "
                "(timestamp, agent_name, action, target_type, target_id, details) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (now, agent_name, action, target_type, target_id, details_json),
        )
        # Committed by the caller's transaction

    # -- Auth tokens --

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_token(self, agent_name: str) -> str:
        """Create and persist a new auth token for an active agent."""
        validate_agent_name(agent_name)
        if self.get_member_role(agent_name) is None:
            raise ValidationError(f"Agent '{agent_name}' is not registered or inactive")
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO auth_tokens (agent_name, token_hash, created_at)
               VALUES (?, ?, ?)""",
            (agent_name, token_hash, now),
        )
        self.log_action(agent_name, "issue_token", "token", None)
        conn.commit()
        return token

    def revoke_tokens(self, agent_name: str) -> int:
        """Revoke all active tokens for an agent. Returns count revoked."""
        validate_agent_name(agent_name)
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE auth_tokens SET revoked_at=? WHERE agent_name=? AND revoked_at IS NULL",
            (now, agent_name),
        )
        if cursor.rowcount > 0:
            self.log_action(agent_name, "revoke_tokens", "token", None)
        conn.commit()
        return cursor.rowcount

    def validate_token(self, agent_name: str, token: str) -> bool:
        """Return True if token is valid and active for the given agent."""
        validate_agent_name(agent_name)
        if not token:
            return False
        if self.get_member_role(agent_name) is None:
            return False
        token_hash = self._hash_token(token)
        conn = self._get_conn()
        row = conn.execute(
            """SELECT id FROM auth_tokens
               WHERE agent_name=? AND token_hash=? AND revoked_at IS NULL""",
            (agent_name, token_hash),
        ).fetchone()
        if row is None:
            return False
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE auth_tokens SET last_used_at=? WHERE id=?",
            (now, row["id"]),
        )
        conn.commit()
        return True

    def require_token(self, agent_name: str, token: str) -> None:
        """Require a valid token if configured; raise ValidationError on failure."""
        if not self.config.require_tokens:
            return
        if not self.validate_token(agent_name, token):
            raise ValidationError("Invalid or missing auth token")

    def get_audit_log(
        self,
        agent_name: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query the audit log with optional filters."""
        conn = self._get_conn()
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if agent_name:
            query += " AND agent_name=?"
            params.append(agent_name)
        if action:
            query += " AND action=?"
            params.append(action)
        if since:
            validate_iso_date(since)
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), AUDIT_LIMIT_MAX)))
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "agent_name": r["agent_name"],
                "action": r["action"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "details": r["details"],
            }
            for r in rows
        ]

    # -- Rate limiting --

    def _check_rate_limit(self, sender: str) -> None:
        """Check if sender is within the message rate limit."""
        now = time.monotonic()
        with _rate_lock:
            timestamps = _rate_buckets.get(sender, [])
            # Prune old entries outside the window
            cutoff = now - RATE_LIMIT_WINDOW
            timestamps = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= RATE_LIMIT_MAX_MESSAGES:
                raise ValidationError(
                    f"Rate limit exceeded: max {RATE_LIMIT_MAX_MESSAGES} messages "
                    f"per {RATE_LIMIT_WINDOW}s. Try again later."
                )
            timestamps.append(now)
            _rate_buckets[sender] = timestamps

    @staticmethod
    def reset_rate_limits() -> None:
        """Clear rate limit buckets. Useful for testing."""
        with _rate_lock:
            _rate_buckets.clear()

    def close(self) -> None:
        """Close the thread-local connection if open."""
        if hasattr(_local, "conn") and _local.conn is not None:
            _local.conn.close()
            _local.conn = None

    # -- Registration --

    def register(
        self, name: str, role: str = "agent", capabilities: list[str] | None = None,
        context_tokens: int = 0,
    ) -> dict:
        """context_tokens: the member's context size (a model's window); tasks for it are capped
        at 5 % of it. 0 = not given, no cap."""
        validate_agent_name(name)
        validate_role(role)
        caps_list = capabilities or []
        validate_capabilities(caps_list)
        validate_context_tokens(context_tokens)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        caps = json.dumps(caps_list)
        conn.execute(
            """INSERT INTO members (name, role, capabilities, status, registered_at,
                                    last_heartbeat, context_tokens)
               VALUES (?, ?, ?, 'active', ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   role=excluded.role,
                   capabilities=excluded.capabilities,
                   status='active',
                   last_heartbeat=excluded.last_heartbeat,
                   context_tokens=excluded.context_tokens""",
            (name, role, caps, now, now, context_tokens or None),
        )
        self.log_action(name, "register", "member", name, {"role": role})
        conn.commit()
        return {"name": name, "role": role, "capabilities": caps_list, "status": "active",
                "context_tokens": context_tokens}

    def deregister(self, name: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE members SET status='inactive' WHERE name=?", (name,)
        )
        if cursor.rowcount > 0:
            self.log_action(name, "deregister", "member", name)
            self.revoke_tokens(name)
        conn.commit()
        return cursor.rowcount > 0

    def list_members(self, include_inactive: bool = False) -> list[dict]:
        conn = self._get_conn()
        if include_inactive:
            rows = conn.execute("SELECT * FROM members").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM members WHERE status='active'"
            ).fetchall()
        return [
            {
                "name": r["name"],
                "role": r["role"],
                "capabilities": json.loads(r["capabilities"]),
                "status": r["status"],
                "registered_at": r["registered_at"],
                "last_heartbeat": r["last_heartbeat"],
            }
            for r in rows
        ]

    def heartbeat(self, name: str) -> bool:
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "UPDATE members SET last_heartbeat=? WHERE name=? AND status='active'",
            (now, name),
        )
        conn.commit()
        return cursor.rowcount > 0

    def member_exists(self, name: str) -> bool:
        """Registered, active or not."""
        row = self._get_conn().execute("SELECT 1 FROM members WHERE name=?", (name,)).fetchone()
        return row is not None

    def get_member_role(self, agent_name: str) -> str | None:
        """Return the role of a registered active agent, or None if not found."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT role FROM members WHERE name=? AND status='active'",
            (agent_name,),
        ).fetchone()
        return row["role"] if row else None

    def unread_count(self, agent_name: str) -> int:
        """Return count of unread messages for an agent."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM messages
               WHERE (recipient=? OR recipient='*') AND read=0
               AND archived_at IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM broadcast_reads br
                   WHERE br.message_id=messages.id AND br.agent_name=?
               )""",
            (agent_name, agent_name),
        ).fetchone()
        return row["cnt"]

    def unread_preview(self, agent_name: str, limit: int = 3) -> list[dict]:
        """Return a preview of unread messages (without marking them read)."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT sender, content, created_at FROM messages
               WHERE (recipient=? OR recipient='*') AND read=0
               AND archived_at IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM broadcast_reads br
                   WHERE br.message_id=messages.id AND br.agent_name=?
               )
               ORDER BY created_at DESC LIMIT ?""",
            (agent_name, agent_name, limit),
        ).fetchall()
        return [
            {"sender": r["sender"], "content": r["content"][:100], "created_at": r["created_at"]}
            for r in rows
        ]

    # -- Messaging --

    def send_message(self, sender: str, recipient: str, content: str) -> dict:
        validate_agent_name(sender)
        if recipient != "*":
            validate_agent_name(recipient)
        validate_message_content(content)
        self._check_rate_limit(sender)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "INSERT INTO messages (sender, recipient, content, created_at) VALUES (?, ?, ?, ?)",
            (sender, recipient, content, now),
        )
        self.log_action(
            sender,
            "send_message",
            "message",
            str(cursor.lastrowid),
            {"recipient": recipient},
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "sender": sender,
            "recipient": recipient,
            "content": content,
            "created_at": now,
        }

    def broadcast(self, sender: str, content: str) -> dict:
        validate_agent_name(sender)
        validate_message_content(content)
        self._check_rate_limit(sender)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "INSERT INTO messages (sender, recipient, content, created_at) VALUES (?, ?, ?, ?)",
            (sender, "*", content, now),
        )
        self.log_action(sender, "broadcast", "message", str(cursor.lastrowid))
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "sender": sender,
            "recipient": "*",
            "content": content,
            "created_at": now,
        }

    def delete_message(self, message_id: int, agent_name: str) -> dict | None:
        """Soft-delete a message (set archived_at). Ownership check enforced."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            return None
        role = self.get_member_role(agent_name)
        is_privileged = role in PRIVILEGED_ROLES
        is_broadcast = row["recipient"] == "*"
        is_owner = row["sender"] == agent_name or row["recipient"] == agent_name
        if not is_privileged and not is_broadcast and not is_owner:
            return {
                "error": f"Agent '{agent_name}' is not authorized to delete message {message_id}"
            }
        now = datetime.now(UTC).isoformat()
        if is_broadcast and not is_privileged and row["sender"] != agent_name:
            self._hide_broadcast(conn, agent_name, message_id)  # gone from this agent's inbox only
            self.log_action(
                agent_name, "delete_message", "message", str(message_id), {"scope": "own inbox"}
            )
            conn.commit()
            return {"id": row["id"], "sender": row["sender"], "recipient": "*", "archived_at": now,
                    "hidden_for": agent_name}
        conn.execute("UPDATE messages SET archived_at=? WHERE id=?", (now, message_id))
        self.log_action(agent_name, "delete_message", "message", str(message_id))
        conn.commit()
        return {
            "id": row["id"],
            "sender": row["sender"],
            "recipient": row["recipient"],
            "archived_at": now,
        }

    def archive_message(self, message_id: int, agent_name: str) -> dict | None:
        """Archive a message: soft-delete + mark as read. Ownership check enforced."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            return None
        role = self.get_member_role(agent_name)
        is_privileged = role in PRIVILEGED_ROLES
        is_broadcast = row["recipient"] == "*"
        is_owner = row["sender"] == agent_name or row["recipient"] == agent_name
        if not is_privileged and not is_broadcast and not is_owner:
            return {
                "error": f"Agent '{agent_name}' is not authorized to archive message {message_id}"
            }
        now = datetime.now(UTC).isoformat()
        if is_broadcast and not is_privileged and row["sender"] != agent_name:
            # one agent archiving a broadcast used to archive it for everyone
            self._hide_broadcast(conn, agent_name, message_id)
            self.log_action(
                agent_name, "archive_message", "message", str(message_id), {"scope": "own inbox"}
            )
            conn.commit()
            return {"id": row["id"], "sender": row["sender"], "recipient": "*", "archived_at": now,
                    "read": True, "hidden_for": agent_name}
        conn.execute("UPDATE messages SET archived_at=?, read=1 WHERE id=?", (now, message_id))
        if row["recipient"] == "*":
            conn.execute(
                "INSERT OR IGNORE INTO broadcast_reads (agent_name, message_id) VALUES (?, ?)",
                (agent_name, message_id),
            )
        self.log_action(agent_name, "archive_message", "message", str(message_id))
        conn.commit()
        return {
            "id": row["id"],
            "sender": row["sender"],
            "recipient": row["recipient"],
            "archived_at": now,
            "read": True,
        }

    @staticmethod
    def _hide_broadcast(conn, agent_name: str, message_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO broadcast_hidden (agent_name, message_id) VALUES (?, ?)",
            (agent_name, message_id),
        )
        conn.execute("INSERT OR IGNORE INTO broadcast_reads (agent_name, message_id) VALUES (?, ?)",
                     (agent_name, message_id))

    def clear_inbox(
        self, agent_name: str, before_date: str | None = None, sender: str | None = None
    ) -> dict:
        """Bulk archive messages in an agent's inbox with optional filters."""
        if before_date:
            validate_iso_date(before_date)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        query = """UPDATE messages SET archived_at=?, read=1
                   WHERE (recipient=? OR recipient='*') AND archived_at IS NULL"""
        params: list[str] = [now, agent_name]
        if before_date:
            query += " AND created_at < ?"
            params.append(before_date)
        if sender:
            query += " AND sender=?"
            params.append(sender)
        cursor = conn.execute(query, params)
        self.log_action(
            agent_name,
            "clear_inbox",
            "messages",
            None,
            {"archived_count": cursor.rowcount},
        )
        conn.commit()
        return {"archived_count": cursor.rowcount, "agent_name": agent_name}

    def purge_messages(self, agent_name: str, before_date: str) -> dict:
        """Hard-delete messages older than before_date. Admin/lead role required."""
        validate_iso_date(before_date)
        role = self.get_member_role(agent_name)
        if role not in ("admin", "lead"):
            return {
                "error": (
                    f"Agent '{agent_name}' does not have permission to purge"
                    " messages (requires admin or lead role)"
                )
            }
        conn = self._get_conn()
        for table in ("broadcast_reads", "broadcast_hidden"):
            conn.execute(
                f"""DELETE FROM {table}
                   WHERE message_id IN (SELECT id FROM messages WHERE created_at < ?)""",
                (before_date,),
            )
        cursor = conn.execute("DELETE FROM messages WHERE created_at < ?", (before_date,))
        self.log_action(
            agent_name,
            "purge_messages",
            "messages",
            None,
            {"purged_count": cursor.rowcount, "before_date": before_date},
        )
        conn.commit()
        return {"purged_count": cursor.rowcount, "before_date": before_date}

    def get_messages(
        self, agent_name: str, include_read: bool = False, include_archived: bool = False
    ) -> list[dict]:
        conn = self._get_conn()
        archive_filter = "" if include_archived else (
            " AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM broadcast_hidden bh"
            " WHERE bh.message_id=messages.id AND bh.agent_name=?)"
        )
        hidden = () if include_archived else (agent_name,)
        if include_read:
            rows = conn.execute(
                "SELECT * FROM messages WHERE (recipient=? OR recipient='*')"
                f"{archive_filter} ORDER BY created_at",
                (agent_name, *hidden),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM messages
                   WHERE (recipient=? OR recipient='*') AND read=0{archive_filter}
                   AND NOT EXISTS (
                       SELECT 1 FROM broadcast_reads br
                       WHERE br.message_id=messages.id AND br.agent_name=?
                   )
                   ORDER BY created_at""",
                (agent_name, *hidden, agent_name),
            ).fetchall()
        # Mark direct messages as read
        msg_ids = [r["id"] for r in rows if r["recipient"] != "*"]
        if msg_ids:
            placeholders = ",".join("?" * len(msg_ids))
            conn.execute(
                f"UPDATE messages SET read=1 WHERE id IN ({placeholders})", msg_ids
            )
        # Track broadcast reads per agent
        broadcast_ids = [r["id"] for r in rows if r["recipient"] == "*"]
        for bid in broadcast_ids:
            conn.execute(
                "INSERT OR IGNORE INTO broadcast_reads (agent_name, message_id) VALUES (?, ?)",
                (agent_name, bid),
            )
        if msg_ids or broadcast_ids:
            conn.commit()
        return [
            {
                "id": r["id"],
                "sender": r["sender"],
                "recipient": r["recipient"],
                "content": r["content"],
                "created_at": r["created_at"],
                "read": bool(r["read"]),
                "archived_at": r["archived_at"],
            }
            for r in rows
        ]

    # -- Tasks --

    def create_task(
        self,
        title: str,
        creator: str,
        description: str = "",
        assignee: str | None = None,
        priority: str = "medium",
        *,
        parent_id: int | None = None,
        kind: str = "",
        refs: list[str] | None = None,
        reviewer: str | None = None,
        origin: str | None = None,
        budget_tasks: int | None = None,
        budget_depth: int | None = None,
    ) -> dict:
        """A task — a turn token. With parent_id it joins that request's tree (its budgets apply);
        without, it starts a new request, whose budgets it may set. refs point to the bulk (files,
        shared-context keys) instead of carrying it: a task for a member is at most 5 % of the
        member's context. origin "person" (the human's own request, served first) is only for
        members with the person, admin or lead role."""
        validate_task_title(title)
        validate_task_description(description)
        validate_priority(priority)
        validate_agent_name(creator)
        refs = list(refs or [])
        validate_refs(refs)
        if len(kind) > 64:
            raise ValidationError("Task kind too long (max 64 chars)")
        for name in (assignee, reviewer):
            if name:
                validate_agent_name(name)
        if reviewer and reviewer == assignee:
            raise ValidationError("The reviewer must be a different member than the assignee")
        self._check_rate_limit(creator)
        conn = self._get_conn()
        self._check_size(conn, assignee, title, description)
        parent = None
        if parent_id:
            parent = conn.execute("SELECT * FROM tasks WHERE id=?", (parent_id,)).fetchone()
            if parent is None:
                raise ValidationError(f"Parent task {parent_id} not found")
            if parent["status"] in FINISHED:
                raise ValidationError(f"Parent task {parent_id} is {parent['status']}")
        origin = origin or (parent["origin"] if parent is not None else "agent")
        if origin not in ("agent", "person"):
            raise ValidationError(f"Invalid origin: {origin!r}")
        if origin == "person" and (parent is None or parent["origin"] != "person") \
                and self.get_member_role(creator) not in OPERATOR_ROLES:
            raise ValidationError("Only the person (or admin/lead) can start a person request")
        depth, root_id = 0, None
        if parent is not None:
            root_id = parent["root_id"] or parent["id"]
            depth = (parent["depth"] or 0) + 1
            root = conn.execute("SELECT * FROM tasks WHERE id=?", (root_id,)).fetchone()
            max_tasks = root["budget_tasks"] or DEFAULT_BUDGET_TASKS
            max_depth = root["budget_depth"] or DEFAULT_BUDGET_DEPTH
            spent = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE root_id=?",
                                 (root_id,)).fetchone()["n"]
            if spent >= max_tasks:
                raise ValidationError(
                    f"This request's budget is spent ({spent} of {max_tasks} tasks). Stop and "
                    "tell the person where it stands."
                )
            if depth > max_depth:
                raise ValidationError(
                    f"Hand-offs this deep aren't allowed (depth {depth}, max {max_depth}). Do "
                    "it here, or tell the person where it stands."
                )
            budget_tasks = budget_depth = None  # budgets belong to the request's first task
        for value, top in ((budget_tasks, MAX_BUDGET_TASKS), (budget_depth, MAX_BUDGET_DEPTH)):
            if value is not None and not (1 <= int(value) <= top):
                raise ValidationError(f"Budget out of range (1-{top}): {value}")
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """INSERT INTO tasks (title, description, status, priority, creator, assignee,
                                  created_at, updated_at, parent_id, root_id, depth, kind, refs,
                                  reviewer, origin, budget_tasks, budget_depth)
               VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, priority, creator, assignee, now, now, parent_id or None,
             root_id, depth, kind, json.dumps(refs), reviewer, origin, budget_tasks,
             budget_depth),
        )
        task_id = cursor.lastrowid
        if root_id is None:  # a new request: its own root
            conn.execute("UPDATE tasks SET root_id=? WHERE id=?", (task_id, task_id))
        self.log_action(
            creator,
            "create_task",
            "task",
            str(task_id),
            {"title": title, "priority": priority, "parent_id": parent_id or None,
             "origin": origin},
        )
        conn.commit()
        return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def _check_size(self, conn, assignee: str | None, title: str, description: str) -> None:
        """5 % of the receiving member's context, in characters (~3 a token)."""
        if not assignee:
            return
        row = conn.execute("SELECT context_tokens FROM members WHERE name=?",
                           (assignee,)).fetchone()
        tokens = row["context_tokens"] if row is not None else None
        if not tokens:
            return
        cap = int(tokens * TOKEN_SHARE * CHARS_PER_TOKEN)
        size = len(title) + len(description)
        if size > cap:
            raise ValidationError(
                f"Too big for {assignee}: {size} characters; a task for it is at most {cap} "
                f"(5 % of its {tokens}-token context). Put the bulk in a file or in shared "
                "context and list it in refs."
            )

    @staticmethod
    def _task(r) -> dict:
        keys = r.keys()
        get = lambda k, d=None: r[k] if k in keys else d  # noqa: E731
        return {
            "id": r["id"], "title": r["title"], "description": r["description"],
            "status": r["status"], "priority": r["priority"], "creator": r["creator"],
            "assignee": r["assignee"], "result": r["result"], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "parent_id": get("parent_id"),
            "root_id": get("root_id"), "depth": get("depth", 0) or 0, "kind": get("kind", ""),
            "refs": json.loads(get("refs") or "[]"), "reviewer": get("reviewer"),
            "origin": get("origin", "agent") or "agent",
        }

    def next_task(self, agent_name: str) -> dict | None:
        """The agent's queue: claim its next task, atomically — the person's requests first,
        then by priority, then oldest. Tasks assigned to it, or to no one."""
        validate_agent_name(agent_name)
        conn = self._get_conn()
        # a task with unfinished children is being coordinated, not waiting to be done: it waits
        rows = conn.execute(
            f"""SELECT id FROM tasks WHERE status='pending' AND (assignee=? OR assignee IS NULL)
                AND NOT EXISTS (SELECT 1 FROM tasks c WHERE c.parent_id = tasks.id
                                AND c.status NOT IN ('done', 'cancelled'))
                ORDER BY (origin='person') DESC, {_PRIORITY_ORDER}, id LIMIT 20""",
            (agent_name,),
        ).fetchall()
        for row in rows:
            claimed = self.claim_task(row["id"], agent_name)
            if claimed is not None and "error" not in claimed:
                return claimed
        return None

    def cancel_task(self, task_id: int, agent_name: str) -> dict | None:
        """Stop: the task and every task under it that isn't finished. Its creator, the creator
        of its request, the person, or admin/lead may."""
        validate_agent_name(agent_name)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        root = conn.execute("SELECT creator FROM tasks WHERE id=?",
                            (row["root_id"] or row["id"],)).fetchone()
        allowed = agent_name in (row["creator"], root["creator"] if root else None) \
            or self.get_member_role(agent_name) in OPERATOR_ROLES
        if not allowed:
            return {"error": f"Agent '{agent_name}' is not authorized to cancel task {task_id}"}
        now = datetime.now(UTC).isoformat()
        # the tree's ids first (sqlite3's rowcount isn't reliable for an UPDATE led by a WITH)
        ids = [r["id"] for r in conn.execute(
            """WITH RECURSIVE sub(id) AS (
                   SELECT ? UNION ALL SELECT t.id FROM tasks t JOIN sub ON t.parent_id = sub.id)
               SELECT id FROM sub""",
            (task_id,),
        ).fetchall()]
        marks = ",".join("?" * len(ids))
        cursor = conn.execute(
            f"""UPDATE tasks SET status='cancelled', updated_at=?
                WHERE id IN ({marks}) AND status NOT IN ('done', 'cancelled')""",
            (now, *ids),
        )
        self.log_action(agent_name, "cancel_task", "task", str(task_id),
                        {"cancelled": cursor.rowcount})
        conn.commit()
        return {"id": task_id, "cancelled": cursor.rowcount}

    def review_task(self, task_id: int, agent_name: str, approve: bool,
                    note: str = "") -> dict | None:
        """The reviewer's answer to a task awaiting review: approved → done; not → back to its
        assignee (in progress), the note added to its result."""
        validate_agent_name(agent_name)
        if note:
            validate_task_result(note)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        if row["status"] != "awaiting_review":
            return {"error": f"Task {task_id} isn't awaiting review (it's {row['status']})"}
        if agent_name != row["reviewer"] and self.get_member_role(agent_name) \
                not in PRIVILEGED_ROLES:
            return {"error": f"Task {task_id} is for '{row['reviewer']}' to review"}
        result = row["result"] or ""
        if note:
            result = (result + f"\n[review by {agent_name}] {note}").strip()[-5000:]
        status = "done" if approve else "in_progress"
        now = datetime.now(UTC).isoformat()
        conn.execute("UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                     (status, result, now, task_id))
        self.log_action(agent_name, "review_task", "task", str(task_id), {"approved": approve})
        conn.commit()
        return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def task_tree(self, task_id: int) -> dict | None:
        """A request's whole tree, from its first task: each task with its children."""
        conn = self._get_conn()
        row = conn.execute("SELECT root_id, id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        root_id = row["root_id"] or row["id"]
        rows = conn.execute("SELECT * FROM tasks WHERE root_id=? OR id=? ORDER BY id",
                            (root_id, root_id)).fetchall()
        nodes = {r["id"]: {**self._task(r), "children": []} for r in rows}
        top = None
        for node in nodes.values():
            parent = nodes.get(node["parent_id"]) if node["parent_id"] else None
            if parent is not None:
                parent["children"].append(node)
            elif node["id"] == root_id:
                top = node
        return top

    def list_tasks(
        self, status: str | None = None, assignee: str | None = None
    ) -> list[dict]:
        conn = self._get_conn()
        query = "SELECT * FROM tasks WHERE 1=1"
        params: list[str] = []
        if status:
            query += " AND status=?"
            params.append(status)
        if assignee:
            query += " AND assignee=?"
            params.append(assignee)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
        return [self._task(r) for r in rows]

    def claim_task(self, task_id: int, agent_name: str) -> dict | None:
        validate_agent_name(agent_name)
        conn = self._get_conn()
        # Check task exists and is claimable
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        if row["status"] != "pending":
            return {"error": f"Task {task_id} is not in pending status (current: {row['status']})"}
        # If task has a specific assignee set by creator, only that agent can claim it
        if row["assignee"] and row["assignee"] != agent_name:
            role = self.get_member_role(agent_name)
            if role not in ("admin", "lead"):
                return {
                    "error": f"Task {task_id} is assigned to '{row['assignee']}'. "
                    f"Only the assignee, admin, or lead can claim it."
                }
        now = datetime.now(UTC).isoformat()
        # atomic: two agents claiming at once can't both get it
        cursor = conn.execute(
            """UPDATE tasks SET assignee=?, status='in_progress', updated_at=?
               WHERE id=? AND status='pending'""",
            (agent_name, now, task_id),
        )
        if cursor.rowcount == 0:
            conn.commit()
            return {"error": f"Task {task_id} was just claimed by someone else"}
        self.log_action(agent_name, "claim_task", "task", str(task_id))
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row)

    def update_task(
        self, task_id: int, status: str, result: str | None = None,
        agent_name: str | None = None,
    ) -> dict | None:
        validate_task_status(status)
        if result is not None:
            validate_task_result(result)
        conn = self._get_conn()
        # Authorization: only creator, assignee, admin, or lead can update
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        if agent_name:
            is_creator = row["creator"] == agent_name
            is_assignee = row["assignee"] == agent_name
            role = self.get_member_role(agent_name)
            is_privileged = role in PRIVILEGED_ROLES
            if not is_creator and not is_assignee and not is_privileged:
                return {
                    "error": f"Agent '{agent_name}' is not authorized to update task {task_id}. "
                    "Only the creator, assignee, admin, or lead can update it."
                }
        if status == "cancelled":
            return {"error": "Use cancel_task: it stops the task and everything under it"}
        if row["status"] == "cancelled":
            return {"error": f"Task {task_id} was cancelled"}
        if status == "awaiting_review" and not row["reviewer"]:
            return {"error": f"Task {task_id} has no reviewer"}
        if status == "done" and row["reviewer"] and agent_name != row["reviewer"]:
            return {
                "error": f"Task {task_id} is done when '{row['reviewer']}' approves it: set "
                "awaiting_review"
            }
        now = datetime.now(UTC).isoformat()
        if result is not None:
            cursor = conn.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, result, now, task_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, now, task_id),
            )
        self.log_action(
            agent_name or "unknown",
            "update_task",
            "task",
            str(task_id),
            {"status": status},
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row)

    # -- Shared Context --

    def share_context(self, key: str, value: str, set_by: str) -> dict:
        validate_context_key(key)
        validate_context_value(value)
        validate_agent_name(set_by)
        conn = self._get_conn()
        # "owner/…" keys belong to that member: only they (or admin/lead) may change them. Other
        # keys stay a shared board anyone can update; every overwrite records who held it before.
        owner = key.split("/", 1)[0] if "/" in key else ""
        if owner and owner != set_by and self.member_exists(owner) \
                and self.get_member_role(set_by) not in PRIVILEGED_ROLES:
            raise ValidationError(
                f"Context key {key!r} belongs to '{owner}'; only they (or admin/lead) can change it"
            )
        self._check_rate_limit(set_by)
        before = conn.execute("SELECT set_by FROM shared_context WHERE key=?", (key,)).fetchone()
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """INSERT INTO shared_context (key, value, set_by, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   set_by=excluded.set_by,
                   updated_at=excluded.updated_at""",
            (key, value, set_by, now),
        )
        self.log_action(set_by, "share_context", "context", key,
                        {"previous_set_by": before["set_by"]} if before else None)
        conn.commit()
        return {"key": key, "value": value, "set_by": set_by, "updated_at": now}

    def get_shared_context(self, key: str | None = None) -> list[dict] | dict | None:
        conn = self._get_conn()
        if key:
            row = conn.execute(
                "SELECT * FROM shared_context WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            return {
                "key": row["key"],
                "value": row["value"],
                "set_by": row["set_by"],
                "updated_at": row["updated_at"],
            }
        rows = conn.execute("SELECT * FROM shared_context ORDER BY key").fetchall()
        return [
            {
                "key": r["key"],
                "value": r["value"],
                "set_by": r["set_by"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
