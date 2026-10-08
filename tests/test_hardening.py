"""Security hardening (2026-10-08): each test tries one way in and checks it's closed."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from team_table import admin
from team_table.config import Config
from team_table.db import Database
from team_table.notify import with_notification
from team_table.validation import ValidationError


class FakeMCP:
    """Collects the functions the tool modules register, so the tools can be called directly."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def tools(db: Database) -> dict:
    from team_table.tools import context, messaging, registration, tasks
    mcp = FakeMCP()
    for module in (registration, messaging, tasks, context):
        module.register_tools(mcp, db)
    return mcp.tools


def call(fn, **kw) -> dict:
    return json.loads(fn(**kw))


# -- identity and roles ----------------------------------------------------------------------------

def test_an_active_name_cant_be_taken_over(tmp_db: Database) -> None:
    t = tools(tmp_db)
    alice = call(t["register"], name="alice")
    # no token: refused, and no new token handed out
    taken = call(t["register"], name="alice", role="coder")
    assert "error" in taken and "token" not in taken
    again = call(t["register"], name="alice", role="coder", token=alice["token"])  # the owner may
    assert again["role"] == "coder" and again["token"]


def test_privileged_roles_are_never_self_assigned(tmp_db: Database) -> None:
    t = tools(tmp_db)
    assert "error" in call(t["register"], name="mallory", role="admin")
    bob = call(t["register"], name="bob")
    assert "error" in call(t["register"], name="bob", role="lead", token=bob["token"])
    assert tmp_db.get_member_role("bob") == "agent"


def test_configured_admins_and_the_operator_can_grant(tmp_db: Database, capsys) -> None:
    tmp_db.config.admins = frozenset({"ian"})
    t = tools(tmp_db)
    assert call(t["register"], name="ian", role="admin")["role"] == "admin"
    call(t["register"], name="carol")
    os.environ["TEAM_TABLE_DB"] = str(tmp_db.config.db_path)
    try:
        assert admin.main(["grant", "carol", "lead"]) == 0
    finally:
        del os.environ["TEAM_TABLE_DB"]
    assert tmp_db.get_member_role("carol") == "lead"
    carol_token = tmp_db.issue_token("carol")
    # re-registering doesn't quietly drop a granted role
    assert call(t["register"], name="carol", token=carol_token)["role"] == "lead"


def test_without_tokens_an_active_name_still_cant_be_taken(tmp_db: Database) -> None:
    tmp_db.config.require_tokens = False
    t = tools(tmp_db)
    call(t["register"], name="alice")
    assert "error" in call(t["register"], name="alice", role="coder")


# -- one agent's data never reaches another --------------------------------------------------------

def test_the_unread_badge_belongs_to_the_caller(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("bob", "alice", "private for alice")
    from team_table import notify
    notify.set_current_agent("alice")  # a process-wide "current agent" must not matter
    out = with_notification(tmp_db, json.dumps({"ok": True}), "bob")
    assert "private for alice" not in out
    assert "private for alice" in with_notification(tmp_db, json.dumps({"ok": True}), "alice")


def test_archiving_a_broadcast_hides_it_for_that_agent_only(tmp_db: Database) -> None:
    for n in ("alice", "bob", "carol"):
        tmp_db.register(n)
    b = tmp_db.broadcast("alice", "hello all")
    assert tmp_db.archive_message(b["id"], "bob")["hidden_for"] == "bob"
    assert tmp_db.delete_message(b["id"], "carol")["hidden_for"] == "carol"
    assert tmp_db.get_messages("bob", include_read=True) == []
    others = tmp_db.get_messages("dave_reader", include_read=True)
    assert [m["content"] for m in others] == ["hello all"]
    # the sender removes it for everyone
    assert tmp_db.archive_message(b["id"], "alice").get("hidden_for") is None
    assert tmp_db.get_messages("dave_reader", include_read=True) == []


# -- tasks, context, limits ------------------------------------------------------------------------

def test_the_task_tool_always_checks_who_is_updating(tmp_db: Database) -> None:
    tmp_db.config.require_tokens = False
    t = tools(tmp_db)
    tmp_db.register("alice")
    tmp_db.register("mallory")
    task = tmp_db.create_task("Fix bug", "alice")
    update = t["update_task"]
    assert "error" in call(update, task_id=task["id"], status="done", agent_name="", token="")
    assert "error" in call(
        update, task_id=task["id"], status="done", agent_name="mallory", token=""
    )
    assert tmp_db.list_tasks()[0]["status"] == "pending"


def test_owner_namespaced_context_keys(tmp_db: Database) -> None:
    for n in ("alice", "bob"):
        tmp_db.register(n)
    tmp_db.share_context("alice/plan", "v1", "alice")
    with pytest.raises(ValidationError):
        tmp_db.share_context("alice/plan", "v2 by bob", "bob")
    tmp_db.share_context("shared-notes", "v1", "alice")
    tmp_db.share_context("shared-notes", "v2", "bob")  # an open key stays a shared board
    audit = tmp_db.get_audit_log(action="share_context", limit=5)
    assert '"previous_set_by": "alice"' in audit[0]["details"]


def test_tasks_and_context_are_rate_limited(tmp_db: Database) -> None:
    tmp_db.register("alice")
    with pytest.raises(ValidationError):
        for i in range(100):
            tmp_db.create_task(f"t{i}", "alice")


def test_the_audit_log_limit_is_clamped(tmp_db: Database) -> None:
    tmp_db.register("alice")
    assert len(tmp_db.get_audit_log(limit=-1)) == 1
    assert len(tmp_db.get_audit_log(limit=10**9)) <= 200


# -- files and network -----------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_table_is_readable_by_its_owner_only(tmp_path) -> None:
    # tmp_path: a real POSIX folder (a Windows drive mounted into Linux ignores chmod)
    db = Database(Config(db_path=tmp_path / "table" / "team_table.db"))
    db.register("alice")
    assert stat.S_IMODE(os.stat(db.config.db_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(db.config.db_path.parent).st_mode) == 0o700
    db.close()


def test_no_open_table_on_the_network(monkeypatch) -> None:
    from team_table import server
    monkeypatch.setattr(server, "config", Config(transport="sse", require_tokens=False))
    with pytest.raises(SystemExit):
        server.main()
