"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from team_table.config import Config
from team_table.db import Database


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    """Create a Database backed by a temporary file."""
    config = Config(db_path=tmp_path / "test.db")
    return Database(config)
