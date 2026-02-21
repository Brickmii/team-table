"""Shared test fixtures."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from team_table.config import Config
from team_table.db import Database


@pytest.fixture
def tmp_db() -> Database:
    """Create a Database backed by a per-test temporary file."""
    Database.reset_rate_limits()
    base = Path(".tmp") / "test_db"
    base.mkdir(parents=True, exist_ok=True)
    tmp_dir = base / f"case_{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    db = Database(Config(db_path=tmp_dir / "test.db"))
    try:
        yield db
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
