"""Security tests: validation, rate limiting, authorization, audit logging."""

from __future__ import annotations

import pytest

from team_table.db import Database
from team_table.validation import ValidationError

# -- Input validation: agent names --

class TestAgentNameValidation:
    def test_empty_name_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="cannot be empty"):
            tmp_db.register("")

    def test_whitespace_only_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="cannot be empty"):
            tmp_db.register("   ")

    def test_too_long_name_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="too long"):
            tmp_db.register("a" * 100)

    def test_special_chars_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="Invalid agent name"):
            tmp_db.register("alice'; DROP TABLE members;--")

    def test_valid_name_with_spaces(self, tmp_db: Database) -> None:
        result = tmp_db.register("claude opus")
        assert result["name"] == "claude opus"

    def test_valid_name_with_hyphens(self, tmp_db: Database) -> None:
        result = tmp_db.register("claude-code")
        assert result["name"] == "claude-code"

    def test_valid_name_with_dots(self, tmp_db: Database) -> None:
        result = tmp_db.register("agent.v2")
        assert result["name"] == "agent.v2"

    def test_single_char_name(self, tmp_db: Database) -> None:
        result = tmp_db.register("A")
        assert result["name"] == "A"


# -- Input validation: roles --

class TestRoleValidation:
    def test_invalid_role_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="Invalid role"):
            tmp_db.register("alice", role="superadmin")

    def test_valid_roles_accepted(self, tmp_db: Database) -> None:
        for role in ("agent", "admin", "lead", "coder", "reviewer"):
            tmp_db.register("test", role=role)


# -- Input validation: messages --

class TestMessageValidation:
    def test_empty_message_rejected(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        with pytest.raises(ValidationError, match="cannot be empty"):
            tmp_db.send_message("alice", "bob", "")

    def test_oversized_message_rejected(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        with pytest.raises(ValidationError, match="too long"):
            tmp_db.send_message("alice", "bob", "x" * 20_000)

    def test_valid_message_accepted(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        result = tmp_db.send_message("alice", "bob", "Hello!")
        assert result["content"] == "Hello!"


# -- Input validation: tasks --

class TestTaskValidation:
    def test_empty_title_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="cannot be empty"):
            tmp_db.create_task("", "alice")

    def test_title_too_long_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="too long"):
            tmp_db.create_task("x" * 300, "alice")

    def test_invalid_priority_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="Invalid priority"):
            tmp_db.create_task("Fix bug", "alice", priority="CRITICAL")

    def test_valid_priorities_accepted(self, tmp_db: Database) -> None:
        for p in ("low", "medium", "high"):
            tmp_db.create_task(f"Task {p}", "alice", priority=p)

    def test_invalid_status_on_update_rejected(self, tmp_db: Database) -> None:
        task = tmp_db.create_task("Fix bug", "alice")
        with pytest.raises(ValidationError, match="Invalid status"):
            tmp_db.update_task(task["id"], "finished")

    def test_valid_statuses_accepted(self, tmp_db: Database) -> None:
        for status in ("pending", "in_progress", "done", "blocked"):
            task = tmp_db.create_task(f"Task {status}", "alice")
            tmp_db.update_task(task["id"], status)


# -- Input validation: context --

class TestContextValidation:
    def test_empty_key_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="cannot be empty"):
            tmp_db.share_context("", "value", "alice")

    def test_key_too_long_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="too long"):
            tmp_db.share_context("k" * 200, "value", "alice")

    def test_value_too_long_rejected(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="too long"):
            tmp_db.share_context("key", "v" * 100_000, "alice")


# -- Input validation: dates --

class TestDateValidation:
    def test_invalid_date_on_purge_rejected(self, tmp_db: Database) -> None:
        tmp_db.register("alice", role="admin")
        with pytest.raises(ValidationError, match="Invalid date"):
            tmp_db.purge_messages("alice", "not-a-date")

    def test_invalid_date_on_clear_inbox_rejected(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        with pytest.raises(ValidationError, match="Invalid date"):
            tmp_db.clear_inbox("alice", before_date="2025-13-45")

    def test_valid_date_accepted(self, tmp_db: Database) -> None:
        tmp_db.register("alice", role="admin")
        result = tmp_db.purge_messages("alice", "2025-01-01T00:00:00")
        assert "purged_count" in result


# -- Rate limiting --

class TestRateLimiting:
    def test_rate_limit_enforced(self, tmp_db: Database) -> None:
        tmp_db.register("spammer")
        tmp_db.register("target")
        for i in range(30):
            tmp_db.send_message("spammer", "target", f"msg {i}")
        with pytest.raises(ValidationError, match="Rate limit exceeded"):
            tmp_db.send_message("spammer", "target", "one too many")

    def test_rate_limit_per_sender(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        tmp_db.register("target")
        # Alice sends 30
        for i in range(30):
            tmp_db.send_message("alice", "target", f"msg {i}")
        # Bob should still be able to send
        result = tmp_db.send_message("bob", "target", "hello from bob")
        assert result["sender"] == "bob"

    def test_broadcast_rate_limited(self, tmp_db: Database) -> None:
        tmp_db.register("spammer")
        for i in range(30):
            tmp_db.broadcast("spammer", f"spam {i}")
        with pytest.raises(ValidationError, match="Rate limit exceeded"):
            tmp_db.broadcast("spammer", "one too many")

    def test_reset_rate_limits(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        for i in range(30):
            tmp_db.send_message("alice", "bob", f"msg {i}")
        Database.reset_rate_limits()
        # Should work again after reset
        result = tmp_db.send_message("alice", "bob", "fresh start")
        assert result["sender"] == "alice"


# -- Task authorization --

class TestTaskAuthorization:
    def test_claim_assigned_task_by_wrong_agent(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        tmp_db.register("charlie")
        task = tmp_db.create_task("Fix bug", "alice", assignee="bob")
        result = tmp_db.claim_task(task["id"], "charlie")
        assert result is not None
        assert "error" in result

    def test_claim_assigned_task_by_correct_agent(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        task = tmp_db.create_task("Fix bug", "alice", assignee="bob")
        result = tmp_db.claim_task(task["id"], "bob")
        assert result is not None
        assert result["assignee"] == "bob"
        assert result["status"] == "in_progress"

    def test_admin_can_claim_assigned_task(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        tmp_db.register("admin1", role="admin")
        task = tmp_db.create_task("Fix bug", "alice", assignee="bob")
        result = tmp_db.claim_task(task["id"], "admin1")
        assert result is not None
        assert result["assignee"] == "admin1"

    def test_update_task_by_unauthorized_agent(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        tmp_db.register("charlie")
        task = tmp_db.create_task("Fix bug", "alice")
        tmp_db.claim_task(task["id"], "bob")
        result = tmp_db.update_task(task["id"], "done", agent_name="charlie")
        assert result is not None
        assert "error" in result

    def test_update_task_by_creator(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        task = tmp_db.create_task("Fix bug", "alice")
        tmp_db.claim_task(task["id"], "bob")
        result = tmp_db.update_task(task["id"], "done", agent_name="alice")
        assert result is not None
        assert result["status"] == "done"

    def test_update_task_by_assignee(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        task = tmp_db.create_task("Fix bug", "alice")
        tmp_db.claim_task(task["id"], "bob")
        result = tmp_db.update_task(task["id"], "done", agent_name="bob")
        assert result is not None
        assert result["status"] == "done"

    def test_update_task_without_agent_name_still_works(self, tmp_db: Database) -> None:
        """Backward compat: update_task without agent_name skips authz check."""
        tmp_db.register("alice")
        task = tmp_db.create_task("Fix bug", "alice")
        result = tmp_db.update_task(task["id"], "done")
        assert result is not None
        assert result["status"] == "done"


# -- Audit logging --

class TestAuditLogging:
    def test_register_logged(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        logs = tmp_db.get_audit_log(agent_name="alice", action="register")
        assert len(logs) == 1
        assert logs[0]["agent_name"] == "alice"
        assert logs[0]["action"] == "register"

    def test_deregister_logged(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.deregister("alice")
        logs = tmp_db.get_audit_log(agent_name="alice", action="deregister")
        assert len(logs) == 1

    def test_send_message_logged(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        tmp_db.send_message("alice", "bob", "hi")
        logs = tmp_db.get_audit_log(agent_name="alice", action="send_message")
        assert len(logs) == 1

    def test_create_task_logged(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.create_task("Fix bug", "alice")
        logs = tmp_db.get_audit_log(agent_name="alice", action="create_task")
        assert len(logs) == 1

    def test_share_context_logged(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.share_context("key1", "val1", "alice")
        logs = tmp_db.get_audit_log(agent_name="alice", action="share_context")
        assert len(logs) == 1

    def test_audit_log_since_filter(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        all_logs = tmp_db.get_audit_log()
        assert len(all_logs) >= 1
        # Filter for future date should return empty
        future_logs = tmp_db.get_audit_log(since="2099-01-01T00:00:00")
        assert len(future_logs) == 0

    def test_audit_log_limit(self, tmp_db: Database) -> None:
        tmp_db.register("alice")
        tmp_db.register("bob")
        for i in range(5):
            tmp_db.send_message("alice", "bob", f"msg {i}")
        logs = tmp_db.get_audit_log(limit=3)
        assert len(logs) == 3


# -- Config validation --

class TestConfigValidation:
    def test_invalid_port_too_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from team_table.config import Config
        monkeypatch.setenv("TEAM_TABLE_PORT", "99999")
        with pytest.raises(ValueError, match="Must be between 1 and 65535"):
            Config.from_env()

    def test_invalid_port_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from team_table.config import Config
        monkeypatch.setenv("TEAM_TABLE_PORT", "0")
        with pytest.raises(ValueError, match="Must be between 1 and 65535"):
            Config.from_env()

    def test_invalid_port_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from team_table.config import Config
        monkeypatch.setenv("TEAM_TABLE_PORT", "abc")
        with pytest.raises(ValueError, match="Must be an integer"):
            Config.from_env()

    def test_default_host_is_localhost(self) -> None:
        from team_table.config import Config
        config = Config()
        assert config.host == "127.0.0.1"

    def test_invalid_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from team_table.config import Config
        monkeypatch.setenv("TEAM_TABLE_TRANSPORT", "websocket")
        with pytest.raises(ValueError, match="Invalid TEAM_TABLE_TRANSPORT"):
            Config.from_env()


# -- Capabilities validation --

class TestCapabilitiesValidation:
    def test_too_many_capabilities(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="Too many capabilities"):
            tmp_db.register("alice", capabilities=["cap"] * 25)

    def test_capability_too_long(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="Capability too long"):
            tmp_db.register("alice", capabilities=["x" * 100])

    def test_non_string_capability(self, tmp_db: Database) -> None:
        with pytest.raises(ValidationError, match="must be a string"):
            tmp_db.register("alice", capabilities=[123])  # type: ignore[list-item]
