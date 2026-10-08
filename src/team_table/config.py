"""Configuration for Team Table MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_db_path() -> Path:
    """Return the default database path (~/.team-table/team_table.db)."""
    return Path.home() / ".team-table" / "team_table.db"


_VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class Config:
    """Server configuration."""

    db_path: Path = field(default_factory=_default_db_path)
    busy_timeout_ms: int = 5000
    heartbeat_timeout_s: int = 300  # 5 minutes before considered inactive
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8741
    require_tokens: bool = True

    @classmethod
    def from_env(cls) -> Config:
        """Create config from environment variables."""
        db_path = os.environ.get("TEAM_TABLE_DB")
        transport = os.environ.get("TEAM_TABLE_TRANSPORT", "stdio").lower()
        if transport not in _VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid TEAM_TABLE_TRANSPORT={transport!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
            )
        host = os.environ.get("TEAM_TABLE_HOST", "127.0.0.1")
        try:
            port = int(os.environ.get("TEAM_TABLE_PORT", "8741"))
        except ValueError:
            raise ValueError(
                f"Invalid TEAM_TABLE_PORT={os.environ.get('TEAM_TABLE_PORT')!r}. "
                "Must be an integer."
            )
        if not (1 <= port <= 65535):
            raise ValueError(
                f"Invalid TEAM_TABLE_PORT={port}. Must be between 1 and 65535."
            )
        require_tokens = _parse_bool(
            os.environ.get("TEAM_TABLE_REQUIRE_TOKENS"), default=True
        )
        return cls(
            db_path=Path(db_path) if db_path else _default_db_path(),
            transport=transport,
            host=host,
            port=port,
            require_tokens=require_tokens,
        )
