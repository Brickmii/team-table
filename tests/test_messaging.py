"""Tests for messaging tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from team_table.db import Database

# -- Existing tests --


def test_send_and_receive(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "hello bob")
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 1
    assert messages[0]["sender"] == "alice"
    assert messages[0]["content"] == "hello bob"


def test_messages_marked_read(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "hello")
    tmp_db.get_messages("bob")  # marks as read
    unread = tmp_db.get_messages("bob", include_read=False)
    assert len(unread) == 0


def test_include_read(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "hello")
    tmp_db.get_messages("bob")  # marks as read
    all_msgs = tmp_db.get_messages("bob", include_read=True)
    assert len(all_msgs) == 1


def test_broadcast(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.broadcast("alice", "hello everyone")
    bob_msgs = tmp_db.get_messages("bob")
    assert len(bob_msgs) == 1
    assert bob_msgs[0]["recipient"] == "*"
    assert bob_msgs[0]["content"] == "hello everyone"


def test_broadcast_visible_to_all(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    tmp_db.broadcast("alice", "announcement")
    assert len(tmp_db.get_messages("bob")) == 1
    assert len(tmp_db.get_messages("charlie")) == 1


def test_broadcast_marked_read_per_agent(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    tmp_db.broadcast("alice", "hello all")
    tmp_db.get_messages("bob")  # Bob reads
    assert tmp_db.unread_count("bob") == 0
    assert tmp_db.unread_count("charlie") == 1


def test_broadcast_unread_count_does_not_grow(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.broadcast("alice", "first")
    tmp_db.get_messages("bob")  # clears
    assert tmp_db.unread_count("bob") == 0
    tmp_db.broadcast("alice", "second")
    assert tmp_db.unread_count("bob") == 1


def test_broadcast_unread_preview_excludes_read(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.broadcast("alice", "old news")
    tmp_db.get_messages("bob")  # mark read
    tmp_db.broadcast("alice", "breaking")
    preview = tmp_db.unread_preview("bob")
    assert len(preview) == 1
    assert preview[0]["content"] == "breaking"


def test_broadcast_include_read_shows_all(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.broadcast("alice", "msg1")
    tmp_db.get_messages("bob")  # mark read
    tmp_db.broadcast("alice", "msg2")
    all_msgs = tmp_db.get_messages("bob", include_read=True)
    assert len(all_msgs) == 2


# -- get_member_role --


def test_get_member_role_returns_role(tmp_db: Database) -> None:
    tmp_db.register("alice", role="admin")
    assert tmp_db.get_member_role("alice") == "admin"


def test_get_member_role_not_found(tmp_db: Database) -> None:
    assert tmp_db.get_member_role("nobody") is None


def test_get_member_role_inactive(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.deregister("alice")
    assert tmp_db.get_member_role("alice") is None


# -- delete_message --


def test_delete_message_by_sender(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.delete_message(msg["id"], "alice")
    assert result is not None
    assert "error" not in result
    assert result["archived_at"] is not None
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 0


def test_delete_message_by_recipient(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.delete_message(msg["id"], "bob")
    assert result is not None
    assert "error" not in result
    assert result["archived_at"] is not None


def test_delete_message_not_found(tmp_db: Database) -> None:
    tmp_db.register("alice")
    result = tmp_db.delete_message(999, "alice")
    assert result is None


def test_delete_message_unauthorized(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.delete_message(msg["id"], "charlie")
    assert result is not None
    assert "error" in result


def test_delete_message_admin_override(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie", role="admin")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.delete_message(msg["id"], "charlie")
    assert result is not None
    assert "error" not in result


def test_delete_message_lead_override(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie", role="lead")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.delete_message(msg["id"], "charlie")
    assert result is not None
    assert "error" not in result


# -- archive_message --


def test_archive_message_marks_read(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.archive_message(msg["id"], "bob")
    assert result is not None
    assert "error" not in result
    assert result["archived_at"] is not None
    assert result["read"] is True
    assert tmp_db.unread_count("bob") == 0


def test_archive_message_unauthorized(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    msg = tmp_db.send_message("alice", "bob", "hello")
    result = tmp_db.archive_message(msg["id"], "charlie")
    assert result is not None
    assert "error" in result


def test_archive_broadcast_message(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    msg = tmp_db.broadcast("alice", "hello all")
    result = tmp_db.archive_message(msg["id"], "bob")
    assert result is not None
    assert "error" not in result
    assert result["archived_at"] is not None
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 0


# -- clear_inbox --


def test_clear_inbox_all(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "msg1")
    tmp_db.send_message("alice", "bob", "msg2")
    tmp_db.send_message("alice", "bob", "msg3")
    result = tmp_db.clear_inbox("bob")
    assert result["archived_count"] == 3
    assert len(tmp_db.get_messages("bob")) == 0


def test_clear_inbox_with_before_date(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    # Insert old message directly with a fixed timestamp
    conn = tmp_db._get_conn()
    conn.execute(
        "INSERT INTO messages (sender, recipient, content, created_at) VALUES (?, ?, ?, ?)",
        ("alice", "bob", "old msg", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    tmp_db.send_message("alice", "bob", "new msg")
    result = tmp_db.clear_inbox("bob", before_date="2025-01-01T00:00:00+00:00")
    assert result["archived_count"] == 1
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 1
    assert messages[0]["content"] == "new msg"


def test_clear_inbox_with_sender_filter(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    tmp_db.send_message("alice", "bob", "from alice")
    tmp_db.send_message("charlie", "bob", "from charlie")
    result = tmp_db.clear_inbox("bob", sender="alice")
    assert result["archived_count"] == 1
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 1
    assert messages[0]["sender"] == "charlie"


def test_clear_inbox_combined_filters(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.register("charlie")
    # Insert old message directly with a fixed timestamp
    conn = tmp_db._get_conn()
    conn.execute(
        "INSERT INTO messages (sender, recipient, content, created_at) VALUES (?, ?, ?, ?)",
        ("alice", "bob", "old from alice", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    tmp_db.send_message("alice", "bob", "new from alice")
    tmp_db.send_message("charlie", "bob", "from charlie")
    result = tmp_db.clear_inbox(
        "bob", before_date="2025-01-01T00:00:00+00:00", sender="alice"
    )
    assert result["archived_count"] == 1
    messages = tmp_db.get_messages("bob")
    assert len(messages) == 2


def test_clear_inbox_empty(tmp_db: Database) -> None:
    tmp_db.register("bob")
    result = tmp_db.clear_inbox("bob")
    assert result["archived_count"] == 0


# -- purge_messages --


def test_purge_messages_admin(tmp_db: Database) -> None:
    tmp_db.register("alice", role="admin")
    tmp_db.register("bob")
    tmp_db.send_message("bob", "alice", "hello")
    future = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    result = tmp_db.purge_messages("alice", before_date=future)
    assert "error" not in result
    assert result["purged_count"] == 1
    # Verify hard delete — not even with include_archived
    messages = tmp_db.get_messages("alice", include_read=True, include_archived=True)
    assert len(messages) == 0


def test_purge_messages_non_admin(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("bob", "alice", "hello")
    future = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    result = tmp_db.purge_messages("alice", before_date=future)
    assert "error" in result
    # Messages still exist
    messages = tmp_db.get_messages("alice", include_read=True)
    assert len(messages) == 1


def test_purge_messages_lead(tmp_db: Database) -> None:
    tmp_db.register("alice", role="lead")
    tmp_db.register("bob")
    tmp_db.send_message("bob", "alice", "hello")
    future = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    result = tmp_db.purge_messages("alice", before_date=future)
    assert "error" not in result
    assert result["purged_count"] == 1


def test_purge_messages_cleans_broadcast_reads(tmp_db: Database) -> None:
    tmp_db.register("alice", role="admin")
    tmp_db.register("bob")
    tmp_db.broadcast("bob", "hello all")
    tmp_db.get_messages("alice")  # creates broadcast_reads entry
    future = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    result = tmp_db.purge_messages("alice", before_date=future)
    assert result["purged_count"] == 1
    messages = tmp_db.get_messages("alice", include_read=True, include_archived=True)
    assert len(messages) == 0


# -- get_messages excludes archived --


def test_get_messages_excludes_archived_by_default(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "visible")
    msg2 = tmp_db.send_message("alice", "bob", "archived")
    tmp_db.archive_message(msg2["id"], "bob")
    messages = tmp_db.get_messages("bob", include_read=True)
    assert len(messages) == 1
    assert messages[0]["content"] == "visible"


def test_get_messages_include_archived(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "visible")
    msg2 = tmp_db.send_message("alice", "bob", "archived")
    tmp_db.archive_message(msg2["id"], "bob")
    messages = tmp_db.get_messages("bob", include_read=True, include_archived=True)
    assert len(messages) == 2
    archived = [m for m in messages if m["archived_at"] is not None]
    assert len(archived) == 1


# -- unread_count excludes archived --


def test_unread_count_excludes_archived(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "keep")
    msg2 = tmp_db.send_message("alice", "bob", "archive me")
    tmp_db.archive_message(msg2["id"], "bob")
    assert tmp_db.unread_count("bob") == 1


# -- unread_preview excludes archived --


def test_unread_preview_excludes_archived(tmp_db: Database) -> None:
    tmp_db.register("alice")
    tmp_db.register("bob")
    tmp_db.send_message("alice", "bob", "keep")
    msg2 = tmp_db.send_message("alice", "bob", "archive me")
    tmp_db.archive_message(msg2["id"], "bob")
    preview = tmp_db.unread_preview("bob")
    assert len(preview) == 1
    assert preview[0]["content"] == "keep"
