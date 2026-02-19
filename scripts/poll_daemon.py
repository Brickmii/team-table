#!/usr/bin/env python3
"""Polling daemon for automatic agent-to-agent message handling.

Monitors the team-table inbox for an agent and auto-responds to messages,
escalating to the user when a question arises or the exchange limit is hit.

Usage:
    python scripts/poll_daemon.py <agent-name> [--interval 30] [--max-messages 13]

Environment:
    TEAM_TABLE_DB  – path to the SQLite database (default: ~/.team-table/team_table.db)
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from team_table.config import Config
from team_table.db import Database

# ── Escalation detection ─────────────────────────────────────────────────────

# Patterns that suggest the sender is asking a question or needs a human decision
QUESTION_PATTERNS = [
    r"\?\s*$",                          # ends with ?
    r"(?i)^(should|could|would|can) (we|you|i)\b",
    r"(?i)\b(what do you think|your (thoughts|opinion|preference))\b",
    r"(?i)\b(decide|choose|pick|approve|confirm)\b",
    r"(?i)\b(escalat\w*|ask the user|check with|need (your|human))\b",
]

_compiled_patterns = [re.compile(p) for p in QUESTION_PATTERNS]


def needs_escalation(content: str) -> bool:
    """Return True if the message looks like it needs a human decision."""
    for pat in _compiled_patterns:
        if pat.search(content):
            return True
    return False


# ── Logging ──────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── Auto-response ────────────────────────────────────────────────────────────

def auto_reply(db: Database, agent_name: str, sender: str, content: str) -> str:
    """Generate a simple acknowledgement reply.

    This is intentionally minimal — the real intelligence comes from the
    Claude Code agent itself.  The daemon's job is to relay and track,
    not to reason.  Override this function (or swap it for an LLM call)
    if you want smarter replies.
    """
    return f"Acknowledged: received your message. (auto-reply from {agent_name})"


# ── Main loop ────────────────────────────────────────────────────────────────

def run(
    agent_name: str,
    interval: int = 30,
    max_messages: int = 13,
    db_path: str | None = None,
) -> None:
    config = Config.from_env()
    if db_path:
        config = Config(db_path=Path(db_path))
    db = Database(config)

    # Ensure the agent is registered
    db.register(agent_name)
    db.heartbeat(agent_name)

    # Track exchanges per conversation partner: sender -> count
    exchange_count: dict[str, int] = defaultdict(int)
    total_auto_replies = 0

    # Graceful shutdown
    running = True

    def _stop(sig: int, _frame: object) -> None:
        nonlocal running
        log(f"Received signal {sig}, shutting down…")
        running = False

    # Signal handlers only work in the main thread
    import threading
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

    log(f"Polling daemon started for '{agent_name}'")
    log(f"  interval : {interval}s")
    log(f"  max msgs : {max_messages}")
    log(f"  db       : {config.db_path}")
    log("Waiting for messages… (Ctrl-C to stop)\n")

    while running:
        try:
            db.heartbeat(agent_name)
            messages = db.get_messages(agent_name)

            for msg in messages:
                sender = msg["sender"]
                content = msg["content"]
                msg_id = msg["id"]

                # Skip own messages and broadcasts we sent
                if sender == agent_name:
                    continue

                log(f"← [{sender}] (msg #{msg_id}): {content[:120]}")

                # ── Check exchange limit ─────────────────────────────
                exchange_count[sender] += 1
                total_auto_replies += 1

                if total_auto_replies > max_messages:
                    log(f"⚠  ESCALATION: max message limit ({max_messages}) reached!")
                    log(f"   Last message from {sender}: {content[:200]}")
                    log("   Stopping auto-replies. Please respond manually.")
                    db.send_message(
                        agent_name,
                        sender,
                        f"[AUTO] Message limit ({max_messages}) reached. "
                        f"Escalating to human operator. Please stand by.",
                    )
                    running = False
                    break

                # ── Check for questions / decisions ──────────────────
                if needs_escalation(content):
                    log(f"⚠  ESCALATION: question/decision detected from {sender}")
                    log(f"   Message: {content[:200]}")
                    log("   Stopping auto-replies. Please respond manually.")
                    db.send_message(
                        agent_name,
                        sender,
                        f"[AUTO] This looks like it needs a human decision. "
                        f"Escalating to {agent_name}'s operator. Please stand by.",
                    )
                    running = False
                    break

                # ── Auto-reply ───────────────────────────────────────
                reply = auto_reply(db, agent_name, sender, content)
                db.send_message(agent_name, sender, reply)
                log(f"→ [{agent_name}] reply #{total_auto_replies}/{max_messages}: {reply[:120]}")

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log(f"ERROR: {exc}")

        # Sleep in small increments so we catch signals quickly
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    db.close()
    log("Daemon stopped.")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poll team-table for messages and auto-respond.",
    )
    parser.add_argument(
        "agent_name",
        help="Name of the agent to poll messages for",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Polling interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=13,
        help="Max auto-replies before escalating to user (default: 13)",
    )
    args = parser.parse_args()
    run(args.agent_name, args.interval, args.max_messages)


if __name__ == "__main__":
    main()