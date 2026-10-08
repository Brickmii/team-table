"""Turn tokens: task trees, budgets, the 5 % size cap, the person's requests first, Stop for a
whole tree, and the reviewer's hand-off."""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from team_table.config import Config
from team_table.db import DEFAULT_BUDGET_TASKS, Database
from team_table.validation import ValidationError
from tests.test_hardening import call, tools


def setup(db: Database) -> None:
    db.register("ian", role="person")  # the operator's grant (the db layer is the operator's)
    db.register("guide", "coder", context_tokens=8192)
    db.register("coder", "coder", context_tokens=32768)
    db.register("checker", "reviewer")


# -- trees -----------------------------------------------------------------------------------------


def test_a_request_and_its_tree(tmp_db: Database) -> None:
    setup(tmp_db)
    root = tmp_db.create_task("Build the inventory tracker", "ian", origin="person")
    assert (root["root_id"], root["depth"], root["origin"]) == (root["id"], 0, "person")
    plan = tmp_db.create_task("Lay out the files", "guide", assignee="guide", parent_id=root["id"])
    code = tmp_db.create_task("Fill in inventory.cpp", "guide", assignee="coder",
                              parent_id=plan["id"], refs=["include/inventory.h"], kind="code")
    assert (code["root_id"], code["depth"]) == (root["id"], 2)
    assert code["refs"] == ["include/inventory.h"]
    assert code["origin"] == "person"  # a person's request stays theirs all the way down
    tree = tmp_db.task_tree(code["id"])
    assert tree["id"] == root["id"]
    assert tree["children"][0]["children"][0]["title"] == "Fill in inventory.cpp"


def test_budgets_stop_a_runaway(tmp_db: Database) -> None:
    setup(tmp_db)
    root = tmp_db.create_task("Request", "guide", budget_tasks=3, budget_depth=2)
    a = tmp_db.create_task("a", "guide", parent_id=root["id"])
    tmp_db.create_task("b", "guide", parent_id=a["id"])
    with pytest.raises(ValidationError, match="budget is spent"):
        tmp_db.create_task("c", "guide", parent_id=root["id"])
    deep = tmp_db.create_task("Request 2", "guide", budget_depth=1)
    one = tmp_db.create_task("one", "guide", parent_id=deep["id"])
    with pytest.raises(ValidationError, match="deep"):
        tmp_db.create_task("two", "guide", parent_id=one["id"])
    assert DEFAULT_BUDGET_TASKS == 40


def test_no_children_for_a_finished_task(tmp_db: Database) -> None:
    setup(tmp_db)
    root = tmp_db.create_task("Request", "guide")
    tmp_db.cancel_task(root["id"], "guide")
    with pytest.raises(ValidationError, match="cancelled"):
        tmp_db.create_task("late", "guide", parent_id=root["id"])


# -- the size cap ----------------------------------------------------------------------------------


def test_a_task_is_at_most_5_percent_of_the_receivers_context(tmp_db: Database) -> None:
    setup(tmp_db)
    cap = int(8192 * 0.05 * 3)  # 1228 characters for the 8K guide
    tmp_db.create_task("t", "coder", "x" * (cap - 1), assignee="guide")
    with pytest.raises(ValidationError, match="refs"):
        tmp_db.create_task("t", "coder", "x" * cap, assignee="guide")
    tmp_db.create_task("t", "guide", "x" * cap, assignee="coder")  # the 32K coder takes it
    tmp_db.create_task("t", "guide", "x" * 4000, assignee="checker")  # no context given: no cap


# -- the person first, one queue per agent ---------------------------------------------------------


def test_the_person_goes_first(tmp_db: Database) -> None:
    setup(tmp_db)
    tmp_db.create_task("agent, high", "guide", assignee="coder", priority="high")
    tmp_db.create_task("person, low", "ian", assignee="coder", priority="low", origin="person")
    tmp_db.create_task("agent, medium", "guide", assignee="coder")
    order = [tmp_db.next_task("coder")["title"] for _ in range(3)]
    assert order == ["person, low", "agent, high", "agent, medium"]
    assert tmp_db.next_task("coder") is None


def test_a_parent_waits_for_its_children(tmp_db: Database) -> None:
    setup(tmp_db)
    root = tmp_db.create_task("Build it", "guide")  # no assignee: anyone's, once it's free
    child = tmp_db.create_task("Part", "guide", parent_id=root["id"])
    assert tmp_db.next_task("coder")["id"] == child["id"]  # not the request itself
    assert tmp_db.next_task("coder") is None
    tmp_db.update_task(child["id"], "done", agent_name="coder")
    assert tmp_db.next_task("coder")["id"] == root["id"]  # its parts done: now it's next


def test_only_the_person_starts_a_person_request(tmp_db: Database) -> None:
    setup(tmp_db)
    with pytest.raises(ValidationError, match="person"):
        tmp_db.create_task("jump the queue", "coder", origin="person")
    t = tools(tmp_db)
    assert "error" in call(t["register"], name="sneaky", role="person")


def test_a_task_is_claimed_once(tmp_db: Database) -> None:
    setup(tmp_db)
    for i in range(10):
        tmp_db.create_task(f"t{i}", "guide")
    got: list = []

    def worker(name: str) -> None:
        db = Database(tmp_db.config)  # each thread its own connection, one database
        while (task := db.next_task(name)) is not None:
            got.append(task["id"])

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("guide", "coder", "checker")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(got) == sorted(set(got)) and len(got) == 10


# -- Stop ------------------------------------------------------------------------------------------


def test_stop_cancels_the_whole_tree(tmp_db: Database) -> None:
    setup(tmp_db)
    root = tmp_db.create_task("Request", "ian", origin="person")
    a = tmp_db.create_task("a", "guide", parent_id=root["id"])
    b = tmp_db.create_task("b", "guide", parent_id=a["id"])
    tmp_db.update_task(b["id"], "done", agent_name="guide")
    assert "error" in tmp_db.cancel_task(root["id"], "coder")  # not theirs to stop
    assert tmp_db.cancel_task(root["id"], "ian")["cancelled"] == 2  # done stays done
    statuses = {t["title"]: t["status"] for t in tmp_db.list_tasks()}
    assert statuses == {"Request": "cancelled", "a": "cancelled", "b": "done"}
    assert "error" in tmp_db.update_task(a["id"], "in_progress", agent_name="guide")


# -- the reviewer's hand-off -----------------------------------------------------------------------


def test_done_only_when_the_reviewer_approves(tmp_db: Database) -> None:
    setup(tmp_db)
    task = tmp_db.create_task("Fill in main.cpp", "guide", assignee="coder", reviewer="checker")
    tmp_db.claim_task(task["id"], "coder")
    assert "error" in tmp_db.update_task(task["id"], "done", agent_name="coder")
    tmp_db.update_task(task["id"], "awaiting_review", "main.cpp written", agent_name="coder")
    assert "error" in tmp_db.review_task(task["id"], "coder", True)  # not their own review
    back = tmp_db.review_task(task["id"], "checker", False, "main() reimplements Inventory")
    assert back["status"] == "in_progress" and "[review by checker]" in back["result"]
    tmp_db.update_task(task["id"], "awaiting_review", agent_name="coder")
    assert tmp_db.review_task(task["id"], "checker", True)["status"] == "done"
    with pytest.raises(ValidationError, match="different"):
        tmp_db.create_task("x", "guide", assignee="coder", reviewer="coder")


# -- through the tools -----------------------------------------------------------------------------


def test_the_tools_end_to_end(tmp_db: Database) -> None:
    t = tools(tmp_db)
    guide = call(t["register"], name="guide", context_tokens=8192)
    coder = call(t["register"], name="coder", context_tokens=32768)
    root = call(t["create_task"], title="Build it", creator="guide", token=guide["token"],
                budget_tasks=5)
    child = call(t["create_task"], title="Fill in x.cpp", creator="guide", token=guide["token"],
                 assignee="coder", parent_id=root["id"], refs='["include/x.h"]')
    claimed = call(t["next_task"], agent_name="coder", token=coder["token"])
    assert claimed["id"] == child["id"] and claimed["refs"] == ["include/x.h"]
    tree = call(t["task_tree"], task_id=child["id"], agent_name="coder", token=coder["token"])
    assert tree["children"][0]["id"] == child["id"]
    assert call(t["cancel_task"], task_id=root["id"], agent_name="guide",
                token=guide["token"])["cancelled"] == 2
    assert "error" in call(t["create_task"], title="x", creator="guide", token=guide["token"],
                           refs="not json")


# -- an existing table gains the new columns -------------------------------------------------------


def test_an_old_table_is_upgraded_in_place(tmp_path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE members (name TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'agent',
            capabilities TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',
            registered_at TEXT NOT NULL, last_heartbeat TEXT NOT NULL);
        CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium', creator TEXT NOT NULL, assignee TEXT,
            result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO tasks (title, creator, created_at, updated_at) VALUES ('old', 'a', 'x', 'x');
    """)
    old.commit()
    old.close()
    db = Database(Config(db_path=path))
    (task,) = db.list_tasks()
    assert (task["title"], task["depth"], task["refs"], task["origin"]) == ("old", 0, [], "agent")
    db.register("a")
    child = db.create_task("new", "a", parent_id=task["id"])
    assert child["root_id"] == task["id"]
    assert json.loads(json.dumps(db.task_tree(task["id"])))["children"][0]["title"] == "new"
    db.close()
