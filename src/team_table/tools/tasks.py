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
        description: str = "",
        assignee: str = "",
        priority: str = "medium",
    ) -> str:
        """Post a task to the task board."""
        try:
            result = db.create_task(
                title, creator, description, assignee or None, priority
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
    def list_tasks(status: str = "", assignee: str = "") -> str:
        """View tasks on the board. Filter by status and/or assignee."""
        tasks = db.list_tasks(status or None, assignee or None)
        return json.dumps(tasks)

    @mcp.tool()
    def claim_task(task_id: int, agent_name: str) -> str:
        """Claim a pending task and start working on it."""
        try:
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
    def update_task(task_id: int, status: str, result: str = "", agent_name: str = "") -> str:
        """Update a task's status and optionally set a result."""
        try:
            updated = db.update_task(
                task_id, status, result or None, agent_name=agent_name or None
            )
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
            return json.dumps(updated)
        except ValidationError as e:
            return json.dumps({"error": e.message})
