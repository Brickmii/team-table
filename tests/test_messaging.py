"""Tests for messaging tools."""

from __future__ import annotations

from team_table.db import Database


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
