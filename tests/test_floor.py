"""The floor: a table's conversation, one speaker at a time — the holder speaks, the floor passes by name
or in seat order, a seat waits its turn in the queue, the person and the lead seat speak when they need
to, and a silent holder loses the floor."""

from __future__ import annotations

import time

import pytest

from team_table.db import Database
from team_table.validation import ValidationError


def table(db: Database, hold: int = 180) -> dict:
    db.register("ian", role="person")
    for name in ("haiku", "luna", "coder", "claude"):
        db.register(name)
    return db.open_floor("store", ["haiku", "luna", "coder", "claude"], ["ian", "haiku"], "ian",
                         hold_seconds=hold)


def test_one_speaker_at_a_time(tmp_db: Database) -> None:
    state = table(tmp_db)
    assert (state["holder"], state["queue"]) == (None, [])
    tmp_db.say("store", "luna", "I'll take the inventory file.")  # a free floor: the first to speak has it
    assert tmp_db.floor("store")["holder"] == "luna"
    with pytest.raises(ValidationError, match="'luna' has the floor"):
        tmp_db.say("store", "coder", "me too")
    tmp_db.request_floor("store", "coder")
    tmp_db.request_floor("store", "claude")
    assert tmp_db.floor("store")["queue"] == ["coder", "claude"]
    state = tmp_db.pass_floor("store", "luna")  # to the queue's first
    assert (state["holder"], state["queue"]) == ("coder", ["claude"])
    tmp_db.say("store", "coder", "Done with the stubs.")
    assert [m["sender"] for m in tmp_db.conversation("store")] == ["luna", "coder"]
    assert [m["content"] for m in tmp_db.conversation("store", after_id=1)] == ["Done with the stubs."]


def test_passing_by_name_or_in_seat_order_and_leaving_it_free(tmp_db: Database) -> None:
    table(tmp_db)
    tmp_db.request_floor("store", "luna")
    assert tmp_db.pass_floor("store", "luna", to="claude")["holder"] == "claude"
    assert tmp_db.pass_floor("store", "claude")["holder"] == "haiku"  # next in seat order, round the table
    assert tmp_db.pass_floor("store", "haiku", to="")["holder"] is None
    with pytest.raises(ValidationError, match="Only the holder"):
        tmp_db.pass_floor("store", "luna", to="coder")
    with pytest.raises(ValidationError, match="no seat at this table"):
        tmp_db.pass_floor("store", "ian", to="stranger")


def test_the_person_and_the_lead_speak_when_they_need_to(tmp_db: Database) -> None:
    table(tmp_db)
    tmp_db.say("store", "luna", "Working on it.")
    tmp_db.say("store", "ian", "Use the real prices, please.")  # an operator: any time, the floor stays
    assert tmp_db.floor("store")["holder"] == "luna"
    state = tmp_db.take_floor("store", "haiku")  # the lead takes it; the one interrupted speaks next
    assert (state["holder"], state["queue"]) == ("haiku", ["luna"])
    with pytest.raises(ValidationError, match="Only the person or the lead"):
        tmp_db.take_floor("store", "coder")
    with pytest.raises(ValidationError, match="Only the person"):
        tmp_db.open_floor("store", ["coder"], ["coder"], "coder")


def test_a_silent_holder_loses_the_floor(tmp_db: Database) -> None:
    table(tmp_db, hold=10)
    tmp_db.request_floor("store", "coder")
    tmp_db.request_floor("store", "luna")
    tmp_db._get_conn().execute("UPDATE floors SET since=? WHERE channel='store'", (time.time() - 11,))
    state = tmp_db.floor("store")
    assert (state["holder"], state["queue"]) == ("luna", [])  # a stuck seat never holds the table
    assert any(e["action"] == "floor_expired" for e in tmp_db.get_audit_log(limit=50))


def test_strangers_and_floods_are_refused(tmp_db: Database) -> None:
    table(tmp_db)
    tmp_db.register("outsider")
    with pytest.raises(ValidationError, match="no seat at this table"):
        tmp_db.say("store", "outsider", "hi")
    with pytest.raises(ValidationError, match="Too long for the table"):
        tmp_db.say("store", "luna", "x" * 5000)
    with pytest.raises(ValidationError, match="No table floor"):
        tmp_db.floor("nowhere")


def test_through_the_mcp_tools_the_token_is_checked(tmp_db: Database) -> None:
    import json

    from team_table.tools import floor as floor_tools
    from tests.test_hardening import FakeMCP
    table(tmp_db)
    mcp = FakeMCP()
    floor_tools.register_tools(mcp, tmp_db)
    token = tmp_db.issue_token("luna")
    said = json.loads(mcp.tools["say"](agent_name="luna", token=token, channel="store", content="Hello, table."))
    assert said["holder"] == "luna"
    refused = json.loads(mcp.tools["say"](agent_name="luna", token="wrong", channel="store", content="x"))
    assert "error" in refused
    talk = json.loads(mcp.tools["conversation"](agent_name="luna", token=token, channel="store"))
    assert [m["content"] for m in talk] == ["Hello, table."]
