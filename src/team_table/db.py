"""SQLite database layer with WAL mode and thread-local connections."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime

from team_table.config import Config

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
    read INTEGER NOT NULL DEFAULT 0
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
"""


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

    def close(self) -> None:
        """Close the thread-local connection if open."""
        if hasattr(_local, "conn") and _local.conn is not None:
            _local.conn.close()
            _local.conn = None

    # -- Registration --

    def register(
        self, name: str, role: str = "agent", capabilities: list[str] | None = None
    ) -> dict:
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        caps = json.dumps(capabilities or [])
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
        conn.commit()
        return {"name": name, "role": role, "capabilities": capabilities or [], "status": "active"}

    def deregister(self, name: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE members SET status='inactive' WHERE name=?", (name,)
        )
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

    def unread_count(self, agent_name: str) -> int:
        """Return count of unread messages for an agent."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM messages
               WHERE (recipient=? OR recipient='*') AND read=0
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
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "INSERT INTO messages (sender, recipient, content, created_at) VALUES (?, ?, ?, ?)",
            (sender, recipient, content, now),
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
        return self.send_message(sender, "*", content)

    def get_messages(self, agent_name: str, include_read: bool = False) -> list[dict]:
        conn = self._get_conn()
        if include_read:
            rows = conn.execute(
                "SELECT * FROM messages WHERE recipient=? OR recipient='*' ORDER BY created_at",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE (recipient=? OR recipient='*') AND read=0
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
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """INSERT INTO tasks (title, description, status, priority, creator, assignee,
                                  created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (title, description, priority, creator, assignee, now, now),
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
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """UPDATE tasks SET assignee=?, status='in_progress', updated_at=?
               WHERE id=? AND status='pending'""",
            (agent_name, now, task_id),
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

    def update_task(
        self, task_id: int, status: str, result: str | None = None
    ) -> dict | None:
        conn = self._get_conn()
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
