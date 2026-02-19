"""Input validation utilities for Team Table."""

from __future__ import annotations

import re
from datetime import datetime

# -- Limits --
MAX_AGENT_NAME_LENGTH = 64
MAX_MESSAGE_CONTENT_LENGTH = 10_000
MAX_TASK_TITLE_LENGTH = 200
MAX_TASK_DESCRIPTION_LENGTH = 5_000
MAX_TASK_RESULT_LENGTH = 5_000
MAX_CONTEXT_KEY_LENGTH = 128
MAX_CONTEXT_VALUE_LENGTH = 50_000
MAX_CAPABILITIES_COUNT = 20
MAX_CAPABILITY_LENGTH = 64

# -- Allowed values --
VALID_PRIORITIES = {"low", "medium", "high"}
VALID_TASK_STATUSES = {"pending", "in_progress", "done", "blocked"}
VALID_ROLES = {"agent", "admin", "lead", "coder", "reviewer", "designer", "tester"}

# Agent names: alphanumeric, hyphens, underscores, spaces, dots
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]$")


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def validate_agent_name(name: str) -> None:
    """Validate an agent name for allowed characters and length."""
    if not name or not name.strip():
        raise ValidationError("Agent name cannot be empty")
    if len(name) > MAX_AGENT_NAME_LENGTH:
        raise ValidationError(
            f"Agent name too long ({len(name)} chars, max {MAX_AGENT_NAME_LENGTH})"
        )
    if not _AGENT_NAME_RE.match(name):
        raise ValidationError(
            f"Invalid agent name: {name!r}. Must be alphanumeric with hyphens, "
            "underscores, spaces, or dots. Must start and end with alphanumeric."
        )


def validate_message_content(content: str) -> None:
    """Validate message content length."""
    if not content or not content.strip():
        raise ValidationError("Message content cannot be empty")
    if len(content) > MAX_MESSAGE_CONTENT_LENGTH:
        raise ValidationError(
            f"Message too long ({len(content)} chars, max {MAX_MESSAGE_CONTENT_LENGTH})"
        )


def validate_task_title(title: str) -> None:
    """Validate task title."""
    if not title or not title.strip():
        raise ValidationError("Task title cannot be empty")
    if len(title) > MAX_TASK_TITLE_LENGTH:
        raise ValidationError(
            f"Task title too long ({len(title)} chars, max {MAX_TASK_TITLE_LENGTH})"
        )


def validate_task_description(description: str) -> None:
    """Validate task description length."""
    if len(description) > MAX_TASK_DESCRIPTION_LENGTH:
        raise ValidationError(
            f"Task description too long ({len(description)} chars, max {MAX_TASK_DESCRIPTION_LENGTH})"
        )


def validate_task_result(result: str) -> None:
    """Validate task result length."""
    if len(result) > MAX_TASK_RESULT_LENGTH:
        raise ValidationError(
            f"Task result too long ({len(result)} chars, max {MAX_TASK_RESULT_LENGTH})"
        )


def validate_priority(priority: str) -> None:
    """Validate task priority is a known value."""
    if priority not in VALID_PRIORITIES:
        raise ValidationError(
            f"Invalid priority: {priority!r}. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"
        )


def validate_task_status(status: str) -> None:
    """Validate task status is a known value."""
    if status not in VALID_TASK_STATUSES:
        raise ValidationError(
            f"Invalid status: {status!r}. Must be one of: {', '.join(sorted(VALID_TASK_STATUSES))}"
        )


def validate_role(role: str) -> None:
    """Validate agent role is a known value."""
    if role not in VALID_ROLES:
        raise ValidationError(
            f"Invalid role: {role!r}. Must be one of: {', '.join(sorted(VALID_ROLES))}"
        )


def validate_capabilities(caps: list) -> None:
    """Validate capabilities list."""
    if len(caps) > MAX_CAPABILITIES_COUNT:
        raise ValidationError(
            f"Too many capabilities ({len(caps)}, max {MAX_CAPABILITIES_COUNT})"
        )
    for cap in caps:
        if not isinstance(cap, str):
            raise ValidationError(f"Capability must be a string, got {type(cap).__name__}")
        if len(cap) > MAX_CAPABILITY_LENGTH:
            raise ValidationError(
                f"Capability too long: {cap!r} ({len(cap)} chars, max {MAX_CAPABILITY_LENGTH})"
            )


def validate_context_key(key: str) -> None:
    """Validate shared context key."""
    if not key or not key.strip():
        raise ValidationError("Context key cannot be empty")
    if len(key) > MAX_CONTEXT_KEY_LENGTH:
        raise ValidationError(
            f"Context key too long ({len(key)} chars, max {MAX_CONTEXT_KEY_LENGTH})"
        )


def validate_context_value(value: str) -> None:
    """Validate shared context value."""
    if len(value) > MAX_CONTEXT_VALUE_LENGTH:
        raise ValidationError(
            f"Context value too long ({len(value)} chars, max {MAX_CONTEXT_VALUE_LENGTH})"
        )


def validate_iso_date(date_str: str) -> None:
    """Validate that a string is a valid ISO date/datetime."""
    try:
        datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        raise ValidationError(
            f"Invalid date format: {date_str!r}. Expected ISO 8601 format (e.g. 2025-01-15T00:00:00)"
        )
