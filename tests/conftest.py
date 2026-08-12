import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Points database.py at a throwaway sqlite file for the duration of one
    test, so tests never touch the real data/database.db."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.fixture
def user_id(db_path):
    return database.create_user("quota-test@example.com", "password123", name="Quota Tester")
