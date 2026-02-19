"""SQLite database layer with WAL mode and thread-local connections."""

from __future__ import annotations

import json
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
    validate_context_value,
    validate_iso_date,
    validate_message_content,
    validate_priority,
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

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);
"""

# -- Rate limiting --
# In-memory rate limiter: tracks (sender -> list of timestamps)
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_MESSAGES = 30  # max messages per window


class Database:
    """Thread-safe SQLite database wrapper using thread-local connections."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._ensure_dir()
        self._init_schema()

    def _ensure_dir(self) -> None:
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)

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

    def _migrate_schema(self) -> None:
        """Apply incremental schema migrations for existing databases."""
        conn = self._get_conn()
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN archived_at TEXT")
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
        params.append(limit)
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
        self, name: str, role: str = "agent", capabilities: list[str] | None = None
    ) -> dict:
        validate_agent_name(name)
        validate_role(role)
        caps_list = capabilities or []
        validate_capabilities(caps_list)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        caps = json.dumps(caps_list)
        conn.execute(
            """INSERT INTO members (name, role, capabilities, status, registered_at, last_heartbeat)
               VALUES (?, ?, ?, 'active', ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   role=excluded.role,
                   capabilities=excluded.capabilities,
                   status='active',
                   last_heartbeat=excluded.last_heartbeat""",
            (name, role, caps, now, now),
        )
        self.log_action(name, "register", "member", name, {"role": role})
        conn.commit()
        return {"name": name, "role": role, "capabilities": caps_list, "status": "active"}

    def deregister(self, name: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE members SET status='inactive' WHERE name=?", (name,)
        )
        if cursor.rowcount > 0:
            self.log_action(name, "deregister", "member", name)
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
        is_privileged = role in ("admin", "lead")
        is_broadcast = row["recipient"] == "*"
        is_owner = row["sender"] == agent_name or row["recipient"] == agent_name
        if not is_privileged and not is_broadcast and not is_owner:
            return {
                "error": f"Agent '{agent_name}' is not authorized to delete message {message_id}"
            }
        now = datetime.now(UTC).isoformat()
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
        is_privileged = role in ("admin", "lead")
        is_broadcast = row["recipient"] == "*"
        is_owner = row["sender"] == agent_name or row["recipient"] == agent_name
        if not is_privileged and not is_broadcast and not is_owner:
            return {
                "error": f"Agent '{agent_name}' is not authorized to archive message {message_id}"
            }
        now = datetime.now(UTC).isoformat()
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
        conn.execute(
            """DELETE FROM broadcast_reads
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
        archive_filter = "" if include_archived else " AND archived_at IS NULL"
        if include_read:
            rows = conn.execute(
                "SELECT * FROM messages WHERE (recipient=? OR recipient='*')"
                f"{archive_filter} ORDER BY created_at",
                (agent_name,),
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
                (agent_name, agent_name),
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
    ) -> dict:
        validate_task_title(title)
        validate_task_description(description)
        validate_priority(priority)
        validate_agent_name(creator)
        if assignee:
            validate_agent_name(assignee)
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """INSERT INTO tasks (title, description, status, priority, creator, assignee,
                                  created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (title, description, priority, creator, assignee, now, now),
        )
        self.log_action(
            creator,
            "create_task",
            "task",
            str(cursor.lastrowid),
            {"title": title, "priority": priority},
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "title": title,
            "description": description,
            "status": "pending",
            "priority": priority,
            "creator": creator,
            "assignee": assignee,
            "created_at": now,
            "updated_at": now,
        }

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
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "status": r["status"],
                "priority": r["priority"],
                "creator": r["creator"],
                "assignee": r["assignee"],
                "result": r["result"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

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
        conn.execute(
            """UPDATE tasks SET assignee=?, status='in_progress', updated_at=?
               WHERE id=?""",
            (agent_name, now, task_id),
        )
        self.log_action(agent_name, "claim_task", "task", str(task_id))
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "creator": row["creator"],
            "assignee": row["assignee"],
            "result": row["result"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

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
            is_privileged = role in ("admin", "lead")
            if not is_creator and not is_assignee and not is_privileged:
                return {
                    "error": f"Agent '{agent_name}' is not authorized to update task {task_id}. "
                    "Only the creator, assignee, admin, or lead can update it."
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
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "creator": row["creator"],
            "assignee": row["assignee"],
            "result": row["result"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- Shared Context --

    def share_context(self, key: str, value: str, set_by: str) -> dict:
        validate_context_key(key)
        validate_context_value(value)
        validate_agent_name(set_by)
        conn = self._get_conn()
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
        self.log_action(set_by, "share_context", "context", key)
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
