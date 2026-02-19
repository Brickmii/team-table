"""Tests for shared context tools."""

from __future__ import annotations

import json

from team_table.db import Database


def test_share_and_get(tmp_db: Database) -> None:
    tmp_db.share_context("project_goal", json.dumps("Build MCP server"), "alice")
    result = tmp_db.get_shared_context("project_goal")
    assert result is not None
    assert result["key"] == "project_goal"
    assert json.loads(result["value"]) == "Build MCP server"
    assert result["set_by"] == "alice"


def test_overwrite_context(tmp_db: Database) -> None:
    tmp_db.share_context("key1", json.dumps("v1"), "alice")
    tmp_db.share_context("key1", json.dumps("v2"), "bob")
    result = tmp_db.get_shared_context("key1")
    assert json.loads(result["value"]) == "v2"
    assert result["set_by"] == "bob"


def test_get_all_context(tmp_db: Database) -> None:
    tmp_db.share_context("a", json.dumps(1), "alice")
    tmp_db.share_context("b", json.dumps(2), "bob")
    result = tmp_db.get_shared_context()
    assert isinstance(result, list)
    assert len(result) == 2


def test_get_nonexistent_key(tmp_db: Database) -> None:
    result = tmp_db.get_shared_context("nope")
    assert result is None


def test_get_all_empty(tmp_db: Database) -> None:
    result = tmp_db.get_shared_context()
    assert result == []
