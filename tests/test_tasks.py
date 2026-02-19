"""Tests for task board tools."""

from __future__ import annotations

from team_table.db import Database


def test_create_task(tmp_db: Database) -> None:
    result = tmp_db.create_task("Fix bug", "alice", description="It's broken")
    assert result["id"] == 1
    assert result["title"] == "Fix bug"
    assert result["status"] == "pending"
    assert result["creator"] == "alice"


def test_list_tasks_empty(tmp_db: Database) -> None:
    assert tmp_db.list_tasks() == []


def test_list_tasks_filter_status(tmp_db: Database) -> None:
    tmp_db.create_task("Task 1", "alice")
    tmp_db.create_task("Task 2", "alice")
    task = tmp_db.create_task("Task 3", "alice")
    tmp_db.claim_task(task["id"], "bob")
    pending = tmp_db.list_tasks(status="pending")
    assert len(pending) == 2
    in_progress = tmp_db.list_tasks(status="in_progress")
    assert len(in_progress) == 1


def test_list_tasks_filter_assignee(tmp_db: Database) -> None:
    tmp_db.create_task("Task 1", "alice", assignee="bob")
    tmp_db.create_task("Task 2", "alice", assignee="charlie")
    bob_tasks = tmp_db.list_tasks(assignee="bob")
    assert len(bob_tasks) == 1
    assert bob_tasks[0]["assignee"] == "bob"


def test_claim_task(tmp_db: Database) -> None:
    task = tmp_db.create_task("Fix bug", "alice")
    claimed = tmp_db.claim_task(task["id"], "bob")
    assert claimed is not None
    assert claimed["assignee"] == "bob"
    assert claimed["status"] == "in_progress"


def test_claim_already_claimed(tmp_db: Database) -> None:
    task = tmp_db.create_task("Fix bug", "alice")
    tmp_db.claim_task(task["id"], "bob")
    result = tmp_db.claim_task(task["id"], "charlie")
    assert result is not None
    assert "error" in result


def test_update_task(tmp_db: Database) -> None:
    task = tmp_db.create_task("Fix bug", "alice")
    tmp_db.claim_task(task["id"], "bob")
    updated = tmp_db.update_task(task["id"], "done", result="Fixed it")
    assert updated is not None
    assert updated["status"] == "done"
    assert updated["result"] == "Fixed it"


def test_update_nonexistent(tmp_db: Database) -> None:
    assert tmp_db.update_task(999, "done") is None


def test_create_task_with_priority(tmp_db: Database) -> None:
    result = tmp_db.create_task("Urgent fix", "alice", priority="high")
    assert result["priority"] == "high"
