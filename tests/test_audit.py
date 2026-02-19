"""Tests for audit logging."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from team_table.db import Database


def test_log_action_and_filters(tmp_db: Database) -> None:
    tmp_db.log_action("alice", "custom_action", "thing", "123", {"ok": True})
    tmp_db.log_action("bob", "other_action", "thing", "456")

    by_agent = tmp_db.get_audit_log(agent_name="alice")
    assert len(by_agent) == 1
    assert by_agent[0]["action"] == "custom_action"
    assert json.loads(by_agent[0]["details"]) == {"ok": True}

    by_action = tmp_db.get_audit_log(action="other_action")
    assert len(by_action) == 1
    assert by_action[0]["agent_name"] == "bob"

    since = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    recent = tmp_db.get_audit_log(since=since, limit=10)
    assert len(recent) >= 2


def test_audit_schema_details_default(tmp_db: Database) -> None:
    conn = tmp_db._get_conn()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO audit_log (timestamp, agent_name, action) VALUES (?, ?, ?)",
        (now, "alice", "manual_insert"),
    )
    conn.commit()

    entry = tmp_db.get_audit_log(action="manual_insert", limit=1)[0]
    assert entry["details"] == "{}"


def test_registration_actions_logged(tmp_db: Database) -> None:
    tmp_db.register("alice", role="agent")
    tmp_db.deregister("alice")

    reg = tmp_db.get_audit_log(action="register", limit=1)[0]
    dereg = tmp_db.get_audit_log(action="deregister", limit=1)[0]

    assert reg["agent_name"] == "alice"
    assert reg["target_type"] == "member"
    assert reg["target_id"] == "alice"
    assert dereg["agent_name"] == "alice"


def test_messaging_actions_logged(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("admin", role="admin")

    direct = tmp_db.send_message("alice", "bob", "hello")
    bcast = tmp_db.broadcast("alice", "hello all")
    tmp_db.delete_message(direct["id"], "alice")
    tmp_db.archive_message(bcast["id"], "bob")
    tmp_db.clear_inbox("bob")
    tmp_db.purge_messages("admin", "9999-01-01T00:00:00+00:00")

    expected = {
        "send_message",
        "broadcast",
        "delete_message",
        "archive_message",
        "clear_inbox",
        "purge_messages",
    }
    actions = {row["action"] for row in tmp_db.get_audit_log(limit=200)}
    assert expected.issubset(actions)


def test_task_and_context_actions_logged(tmp_db: Database) -> None:
    task = tmp_db.create_task("Build audit", "alice", assignee="bob")
    tmp_db.claim_task(task["id"], "bob")
    tmp_db.update_task(task["id"], "done", result="done", agent_name="bob")
    tmp_db.share_context("phase", json.dumps("3"), "alice")

    expected = {"create_task", "claim_task", "update_task", "share_context"}
    actions = {row["action"] for row in tmp_db.get_audit_log(limit=100)}
    assert expected.issubset(actions)
