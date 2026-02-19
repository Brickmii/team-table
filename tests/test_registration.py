"""Tests for registration tools."""

from __future__ import annotations

from team_table.db import Database


def test_register_new_member(tmp_db: Database) -> None:
    result = tmp_db.register("alice", "coder", ["python", "rust"])
    assert result["name"] == "alice"
    assert result["role"] == "coder"
    assert result["capabilities"] == ["python", "rust"]
    assert result["status"] == "active"


def test_register_reregisters(tmp_db: Database) -> None:
    tmp_db.register("alice", "coder")
    tmp_db.deregister("alice")
    result = tmp_db.register("alice", "reviewer")
    assert result["role"] == "reviewer"
    assert result["status"] == "active"


def test_deregister(tmp_db: Database) -> None:
    tmp_db.register("alice")
    assert tmp_db.deregister("alice") is True
    members = tmp_db.list_members(include_inactive=False)
    assert len(members) == 0


def test_deregister_nonexistent(tmp_db: Database) -> None:
    assert tmp_db.deregister("nobody") is False


def test_list_members_active_only(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.deregister("bob")
    members = tmp_db.list_members(include_inactive=False)
    assert len(members) == 1
    assert members[0]["name"] == "alice"


def test_list_members_include_inactive(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.deregister("bob")
    members = tmp_db.list_members(include_inactive=True)
    assert len(members) == 2


def test_heartbeat(tmp_db: Database) -> None:
    tmp_db.register("alice")
    assert tmp_db.heartbeat("alice") is True


def test_heartbeat_nonexistent(tmp_db: Database) -> None:
    assert tmp_db.heartbeat("nobody") is False


def test_heartbeat_inactive_agent(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.deregister("alice")
    assert tmp_db.heartbeat("alice") is False
    members = tmp_db.list_members(include_inactive=True)
    assert members[0]["status"] == "inactive"
