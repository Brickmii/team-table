"""Tests for the poll daemon's core logic."""

from __future__ import annotations

# Import daemon internals
import sys
import tempfile
from pathlib import Path

import pytest

from team_table.config import Config
from team_table.db import Database

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from poll_daemon import auto_reply, needs_escalation, run


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        config = Config(db_path=db_path)
        database = Database(config)
        database._test_db_path = str(db_path)  # expose for tests
        yield database
        database.close()


# ── Escalation detection ─────────────────────────────────────────────────────

class TestNeedsEscalation:
    def test_question_mark(self):
        assert needs_escalation("What port should we use?") is True

    def test_should_we(self):
        assert needs_escalation("Should we deploy to prod?") is True

    def test_could_you(self):
        assert needs_escalation("Could you check this?") is True

    def test_what_do_you_think(self):
        assert needs_escalation("I made changes, what do you think?") is True

    def test_approve(self):
        assert needs_escalation("Please approve the PR") is True

    def test_escalate_keyword(self):
        assert needs_escalation("We should escalate this to the lead") is True

    def test_plain_statement_no_escalation(self):
        assert needs_escalation("Task completed successfully.") is False

    def test_acknowledgement_no_escalation(self):
        assert needs_escalation("Got it, working on it now.") is False

    def test_status_update_no_escalation(self):
        assert needs_escalation("Deployed v2.1 to staging.") is False


# ── Auto-reply ───────────────────────────────────────────────────────────────

class TestAutoReply:
    def test_reply_contains_agent_name(self, db):
        reply = auto_reply(db, "bot-1", "bot-2", "hello")
        assert "bot-1" in reply

    def test_reply_is_string(self, db):
        reply = auto_reply(db, "bot-1", "bot-2", "hello")
        assert isinstance(reply, str)


# ── Integration: message limit ───────────────────────────────────────────────

class TestMessageLimit:
    def test_stops_after_max_messages(self, db):
        """Daemon should stop auto-replying after max_messages exchanges."""
        max_msgs = 3
        db_path = db._test_db_path

        db.register("daemon-agent")
        db.register("other-agent")

        # Send more messages than the limit
        for i in range(max_msgs + 2):
            db.send_message("other-agent", "daemon-agent", f"Message #{i}")

        import threading

        stopped = threading.Event()

        def run_daemon():
            run("daemon-agent", interval=1, max_messages=max_msgs, db_path=db_path)
            stopped.set()

        t = threading.Thread(target=run_daemon, daemon=True)
        t.start()

        # Wait for daemon to process and stop (max 15s)
        assert stopped.wait(timeout=15), "Daemon did not stop within timeout"

        # Check that an escalation message was sent
        msgs = db.get_messages("other-agent")
        escalation_msgs = [
            m for m in msgs
            if "[AUTO]" in m["content"] and "limit" in m["content"].lower()
        ]
        assert len(escalation_msgs) >= 1, "Expected an escalation message about the limit"

    def test_escalates_on_question(self, db):
        """Daemon should escalate when it detects a question."""
        db_path = db._test_db_path

        db.register("daemon-agent")
        db.register("asker")

        db.send_message("asker", "daemon-agent", "Should we deploy to production?")

        import threading

        stopped = threading.Event()

        def run_daemon():
            run("daemon-agent", interval=1, max_messages=13, db_path=db_path)
            stopped.set()

        t = threading.Thread(target=run_daemon, daemon=True)
        t.start()

        assert stopped.wait(timeout=15), "Daemon did not stop within timeout"

        msgs = db.get_messages("asker")
        escalation_msgs = [
            m for m in msgs
            if "[AUTO]" in m["content"] and "decision" in m["content"].lower()
        ]
        assert len(escalation_msgs) >= 1, "Expected an escalation message about a decision"
