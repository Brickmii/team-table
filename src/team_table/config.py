"""Configuration for Team Table MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_db_path() -> Path:
    """Return the default database path (~/.team-table/team_table.db)."""
    return Path.home() / ".team-table" / "team_table.db"


@dataclass
class Config:
    """Server configuration."""

    db_path: Path = field(default_factory=_default_db_path)
    busy_timeout_ms: int = 5000
    heartbeat_timeout_s: int = 300  # 5 minutes before considered inactive

    @classmethod
    def from_env(cls) -> Config:
        """Create config from environment variables."""
        db_path = os.environ.get("TEAM_TABLE_DB")
        return cls(
            db_path=Path(db_path) if db_path else _default_db_path(),
        )
