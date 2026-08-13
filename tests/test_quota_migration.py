"""Covers init_db's upgrade of the pre-per-key quota schema.

quota_limits was originally keyed by user_id alone and usage_logs had no
key_hash at all. SQLite can't alter a primary key in place, so init_db
rebuilds the table — the risky kind of migration worth testing directly
against a database built in the old shape.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import quota

OLD_KEY = "AIza-key-in-use-at-migration-time"


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """Builds a database in the pre-per-key shape, with one user who has a
    saved API key, some usage history, and a tightened quota row."""
    path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(database, "DB_PATH", path)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            gemini_api_key TEXT,
            firebase_uid TEXT UNIQUE,
            created_at DATETIME NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            request_type TEXT NOT NULL,
            estimated_tokens INTEGER,
            actual_tokens INTEGER,
            status TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE quota_limits (
            user_id INTEGER PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            rpm_limit INTEGER NOT NULL,
            tpm_limit INTEGER NOT NULL,
            rpd_limit INTEGER NOT NULL,
            cooldown_until TEXT,
            updated_at TEXT NOT NULL,
            rpm_limit_corrected_at TEXT,
            tpm_limit_corrected_at TEXT,
            rpd_limit_corrected_at TEXT
        )
    """)

    cursor.execute(
        "INSERT INTO users (username, password_hash, gemini_api_key, created_at) VALUES (?, ?, ?, ?)",
        ("with-key@example.com", "", OLD_KEY, "2026-08-01T00:00:00+00:00"),
    )
    keyed_user = cursor.lastrowid
    cursor.execute(
        "INSERT INTO users (username, password_hash, gemini_api_key, created_at) VALUES (?, ?, ?, ?)",
        ("no-key@example.com", "", None, "2026-08-01T00:00:00+00:00"),
    )
    keyless_user = cursor.lastrowid

    for _ in range(3):
        cursor.execute(
            "INSERT INTO usage_logs (user_id, timestamp, request_type, estimated_tokens, actual_tokens, status) "
            "VALUES (?, datetime('now'), 'text', 50, 50, 'success')",
            (keyed_user,),
        )
    cursor.execute(
        "INSERT INTO quota_limits (user_id, tier, rpm_limit, tpm_limit, rpd_limit, updated_at, rpd_limit_corrected_at) "
        "VALUES (?, 'free', 5, 250000, 7, datetime('now'), datetime('now'))",
        (keyed_user,),
    )
    cursor.execute(
        "INSERT INTO quota_limits (user_id, tier, rpm_limit, tpm_limit, rpd_limit, updated_at) "
        "VALUES (?, 'free', 5, 250000, 20, datetime('now'))",
        (keyless_user,),
    )
    conn.commit()
    conn.close()

    return {"path": path, "keyed_user": keyed_user, "keyless_user": keyless_user}


def test_init_db_migrates_quota_state_onto_the_users_current_key(legacy_db):
    database.init_db()

    key_hash = database.hash_api_key(OLD_KEY)
    limits = database.get_quota_limits(legacy_db["keyed_user"], key_hash, quota.get_default_limits())

    # The tightened rpd_limit carried across rather than being reseeded.
    assert limits["rpd_limit"] == 7
    assert limits["rpd_limit_corrected_at"] is not None


def test_init_db_attributes_existing_usage_to_the_current_key(legacy_db):
    database.init_db()

    key_hash = database.hash_api_key(OLD_KEY)
    window = database.get_usage_window(legacy_db["keyed_user"], key_hash, "1 day")

    # Historical usage must keep counting — dropping it would hand the user
    # a fresh budget that Google's side would not honour.
    assert window["requests"] == 3
    assert window["tokens"] == 150


def test_init_db_drops_quota_rows_for_users_without_a_key(legacy_db):
    database.init_db()

    conn = sqlite3.connect(database.DB_PATH)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM quota_limits WHERE user_id = ?", (legacy_db["keyless_user"],)
    ).fetchone()[0]
    conn.close()

    # No key means no budget the row could describe; it reseeds from tier
    # defaults once they save one.
    assert remaining == 0


def test_init_db_is_idempotent(legacy_db):
    database.init_db()
    database.init_db()

    key_hash = database.hash_api_key(OLD_KEY)
    limits = database.get_quota_limits(legacy_db["keyed_user"], key_hash, quota.get_default_limits())
    assert limits["rpd_limit"] == 7
    assert database.get_usage_window(legacy_db["keyed_user"], key_hash, "1 day")["requests"] == 3


@pytest.fixture
def half_migrated_db(tmp_path, monkeypatch):
    """A database carrying a key_hash column while still keyed by user_id
    alone. The presence of the column isn't enough to call it migrated —
    the single-row-per-user primary key is exactly what breaks a second
    key — so init_db has to rebuild this shape too."""
    path = str(tmp_path / "half.db")
    monkeypatch.setattr(database, "DB_PATH", path)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            gemini_api_key TEXT,
            firebase_uid TEXT UNIQUE,
            created_at DATETIME NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key_hash TEXT,
            timestamp TEXT NOT NULL,
            request_type TEXT NOT NULL,
            estimated_tokens INTEGER,
            actual_tokens INTEGER,
            status TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE quota_limits (
            user_id INTEGER PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            rpm_limit INTEGER NOT NULL,
            tpm_limit INTEGER NOT NULL,
            rpd_limit INTEGER NOT NULL,
            cooldown_until TEXT,
            updated_at TEXT NOT NULL,
            rpm_limit_corrected_at TEXT,
            tpm_limit_corrected_at TEXT,
            rpd_limit_corrected_at TEXT,
            key_hash TEXT,
            tracking_since TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO users (username, password_hash, gemini_api_key, created_at) VALUES (?, ?, ?, ?)",
        ("with-key@example.com", "", OLD_KEY, "2026-08-01T00:00:00+00:00"),
    )
    keyed_user = cursor.lastrowid
    cursor.execute(
        "INSERT INTO quota_limits (user_id, tier, rpm_limit, tpm_limit, rpd_limit, updated_at, key_hash) "
        "VALUES (?, 'free', 5, 250000, 11, datetime('now'), ?)",
        (keyed_user, database.hash_api_key(OLD_KEY)),
    )
    conn.commit()
    conn.close()

    return {"path": path, "keyed_user": keyed_user}


def test_init_db_rebuilds_a_half_migrated_quota_table(half_migrated_db):
    database.init_db()

    conn = sqlite3.connect(database.DB_PATH)
    pk_columns = {row[1] for row in conn.execute("PRAGMA table_info(quota_limits)") if row[5]}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(quota_limits)")}
    conn.close()

    assert pk_columns == {"user_id", "key_hash"}
    assert "tracking_since" not in columns

    # The row's own key_hash was authoritative and kept, along with its limits.
    limits = database.get_quota_limits(
        half_migrated_db["keyed_user"], database.hash_api_key(OLD_KEY), quota.get_default_limits()
    )
    assert limits["rpd_limit"] == 11


def test_half_migrated_database_accepts_a_second_key(half_migrated_db):
    database.init_db()
    user = half_migrated_db["keyed_user"]

    # This is what the old single-row primary key made impossible.
    quota.record_success(user, "second-key", "text", estimated_tokens=100, usage_metadata=None)
    database.get_quota_limits(user, database.hash_api_key("second-key"), quota.get_default_limits())

    assert database.get_usage_window(user, database.hash_api_key("second-key"), "1 day")["requests"] == 1


def test_migrated_database_tracks_a_newly_added_second_key_separately(legacy_db):
    database.init_db()
    user = legacy_db["keyed_user"]

    quota.record_success(user, "a-different-key", "text", estimated_tokens=100, usage_metadata=None)

    assert database.get_usage_window(user, database.hash_api_key(OLD_KEY), "1 day")["requests"] == 3
    assert database.get_usage_window(user, database.hash_api_key("a-different-key"), "1 day")["requests"] == 1
