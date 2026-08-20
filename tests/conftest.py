import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import summary_jobs


@pytest.fixture
def inline_jobs(monkeypatch):
    """Runs queued summarise jobs on the calling thread instead of the
    background pool, so a test can assert on a job's outcome immediately
    after the request that queued it rather than racing a worker thread."""
    monkeypatch.setattr(summary_jobs, "_dispatch", lambda fn, *args: fn(*args))


@pytest.fixture
def db_path(monkeypatch):
    """Points database.py at a throwaway Postgres database for the duration
    of one test, so tests never touch the real deployed database. Requires
    TEST_DATABASE_URL (or DATABASE_URL) to point at a real, disposable
    Postgres instance — e.g. a local `docker run postgres` — since the app
    no longer supports a file-based database."""
    test_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not test_url:
        pytest.skip("Set TEST_DATABASE_URL (or DATABASE_URL) to a throwaway Postgres database to run this suite.")
    monkeypatch.setattr(database, "DATABASE_URL", test_url)

    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DROP TABLE IF EXISTS summary_jobs, quota_limits, usage_logs, sessions, folders, users CASCADE"
    )
    conn.commit()
    cursor.close()
    conn.close()

    database.init_db()
    return test_url


@pytest.fixture
def user_id(db_path):
    return database.create_user("quota-test@example.com", "password123", name="Quota Tester")
