"""Task board tools: create_task, list_tasks, claim_task, update_task."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from team_table.db import Database
from team_table.notifications import (
    EVENT_TASK_ASSIGNED,
    EVENT_TASK_UPDATED,
    make_event,
    notify,
    notify_all,
)
from team_table.validation import ValidationError


def register_tools(mcp: FastMCP, db: Database) -> None:
    @mcp.tool()
    def create_task(
        title: str,
        creator: str,
        token: str,
        description: str = "",
        assignee: str = "",
        priority: str = "medium",
        parent_id: int = 0,
        kind: str = "",
        refs: str = "[]",
        reviewer: str = "",
        origin: str = "",
        budget_tasks: int = 0,
        budget_depth: int = 0,
    ) -> str:
        """Post a task (a turn token). parent_id: the task this one serves — it joins that
        request's tree and budgets. refs: a JSON array of file paths or shared-context keys
        holding the bulk (a task for a member is at most 5 % of its context). reviewer: who
        approves it before it's done. budget_tasks/budget_depth: limits for a new request."""
        try:
            db.require_token(creator, token)
            try:
                ref_list = json.loads(refs or "[]")
            except (json.JSONDecodeError, TypeError):
                raise ValidationError(f"Invalid refs JSON: {refs!r}")
            if not isinstance(ref_list, list):
                raise ValidationError("refs must be a JSON array")
            result = db.create_task(
                title, creator, description, assignee or None, priority,
                parent_id=parent_id or None, kind=kind, refs=ref_list,
                reviewer=reviewer or None, origin=origin or None,
                budget_tasks=budget_tasks or None, budget_depth=budget_depth or None,
            )
            event = make_event(EVENT_TASK_ASSIGNED, {
                "id": result["id"],
                "title": title,
                "creator": creator,
                "assignee": result["assignee"],
                "priority": result["priority"],
            })
            if result["assignee"]:
                notify(result["assignee"], event)
            else:
                notify_all(event, exclude=creator)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def list_tasks(agent_name: str, token: str, status: str = "", assignee: str = "") -> str:
        """View tasks on the board. Filter by status and/or assignee."""
        try:
            db.require_token(agent_name, token)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        tasks = db.list_tasks(status or None, assignee or None)
        return json.dumps(tasks)

    @mcp.tool()
    def claim_task(task_id: int, agent_name: str, token: str) -> str:
        """Claim a pending task and start working on it."""
        try:
            db.require_token(agent_name, token)
            result = db.claim_task(task_id, agent_name)
            if result is None:
                return json.dumps(
                    {"error": f"Task {task_id} not found or not in pending status"}
                )
            if "error" not in result:
                notify_all(
                    make_event(EVENT_TASK_UPDATED, {
                        "id": task_id,
                        "status": "in_progress",
                        "assignee": agent_name,
                    }),
                    exclude=agent_name,
                )
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def update_task(
        task_id: int, status: str, agent_name: str, token: str, result: str = ""
    ) -> str:
        """Update a task's status and optionally set a result."""
        try:
            if not agent_name:
                raise ValidationError("agent_name is required")
            db.require_token(agent_name, token)
            # always the caller's name: with tokens off, an empty name used to skip the
            # permission check
            updated = db.update_task(task_id, status, result or None, agent_name=agent_name)
            if updated is None:
                return json.dumps({"error": f"Task {task_id} not found"})
            if "error" not in updated:
                notify_all(
                    make_event(EVENT_TASK_UPDATED, {
                        "id": task_id,
                        "status": status,
                    }),
                    exclude=agent_name or None,
                )
                if status == "awaiting_review" and updated.get("reviewer"):
                    notify(updated["reviewer"], make_event(EVENT_TASK_ASSIGNED, {
                        "id": task_id, "title": updated["title"], "review": True,
                    }))
            return json.dumps(updated)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def next_task(agent_name: str, token: str) -> str:
        """Claim your next task: the person's requests first, then by priority, then oldest."""
        try:
            db.require_token(agent_name, token)
            task = db.next_task(agent_name)
            return json.dumps(task if task is not None else {"task": None})
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def cancel_task(task_id: int, agent_name: str, token: str) -> str:
        """Stop a task and every task under it."""
        try:
            db.require_token(agent_name, token)
            result = db.cancel_task(task_id, agent_name)
            if result is None:
                return json.dumps({"error": f"Task {task_id} not found"})
            if "error" not in result:
                notify_all(make_event(EVENT_TASK_UPDATED, {"id": task_id, "status": "cancelled"}),
                           exclude=agent_name)
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def review_task(
        task_id: int, agent_name: str, token: str, approve: bool, note: str = ""
    ) -> str:
        """Approve a task awaiting your review (it's done), or send it back with a note."""
        try:
            db.require_token(agent_name, token)
            result = db.review_task(task_id, agent_name, approve, note)
            if result is None:
                return json.dumps({"error": f"Task {task_id} not found"})
            if "error" not in result and result.get("assignee"):
                notify(result["assignee"], make_event(EVENT_TASK_UPDATED, {
                    "id": task_id, "status": result["status"], "approved": approve,
                }))
            return json.dumps(result)
        except ValidationError as e:
            return json.dumps({"error": e.message})

    @mcp.tool()
    def task_tree(task_id: int, agent_name: str, token: str) -> str:
        """A request's whole tree of tasks, from its first task."""
        try:
            db.require_token(agent_name, token)
        except ValidationError as e:
            return json.dumps({"error": e.message})
        tree = db.task_tree(task_id)
        return json.dumps(tree if tree is not None else {"error": f"Task {task_id} not found"})
