"""Smoke test: simulate two agents sharing one database."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from team_table.config import Config
from team_table.db import Database


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "smoke.db"
        config = Config(db_path=db_path)

        # Simulate two separate processes with two Database instances
        agent_a = Database(config)
        agent_b = Database(config)

        # Registration
        agent_a.register("claude-1", "coder", ["python", "architecture"])
        agent_b.register("claude-2", "reviewer", ["code-review", "testing"])

        members = agent_a.list_members()
        print(f"Members: {json.dumps(members, indent=2)}")
        assert len(members) == 2, f"Expected 2 members, got {len(members)}"

        # Messaging
        agent_a.send_message("claude-1", "claude-2", "Can you review my PR?")
        messages = agent_b.get_messages("claude-2")
        print(f"\nclaude-2 inbox: {json.dumps(messages, indent=2)}")
        assert len(messages) == 1

        # Broadcast
        agent_a.broadcast("claude-1", "Starting deployment")
        msgs = agent_b.get_messages("claude-2")
        print(f"\nclaude-2 broadcast: {json.dumps(msgs, indent=2)}")
        assert len(msgs) == 1

        # Task board
        task = agent_a.create_task("Write tests", "claude-1", description="Unit tests for db.py")
        print(f"\nCreated task: {json.dumps(task, indent=2)}")

        claimed = agent_b.claim_task(task["id"], "claude-2")
        print(f"Claimed: {json.dumps(claimed, indent=2)}")
        assert claimed["assignee"] == "claude-2"

        updated = agent_b.update_task(task["id"], "done", result="All tests passing")
        print(f"Updated: {json.dumps(updated, indent=2)}")
        assert updated["status"] == "done"

        # Shared context
        agent_a.share_context("repo_url", json.dumps("https://github.com/example/team-table"), "claude-1")
        ctx = agent_b.get_shared_context("repo_url")
        print(f"\nShared context: {json.dumps(ctx, indent=2)}")
        assert ctx["set_by"] == "claude-1"

        print("\n--- All smoke tests passed! ---")

        # Close connections so Windows can clean up the temp dir
        agent_a.close()
        agent_b.close()


if __name__ == "__main__":
    main()
