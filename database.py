import os
import sqlite3
import json
import threading
import time
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.environ.get("DATABASE_PATH")
if not DB_PATH:
    # Anchored to this file's own directory rather than the process's cwd —
    # cwd varies depending on how/where the server is launched (terminal,
    # IDE run config, debugger), which previously made the resolved DB file
    # inconsistent across runs and made data look like it was disappearing.
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "database.db")


def get_db_connection():
    """Helper function to establish a database connection with dictionary-like row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # WAL lets the rolling-window quota reads (fired on every summarise
    # request) proceed concurrently with the writes that log each request's
    # outcome, instead of blocking each other under SQLite's default
    # rollback-journal locking.
    cursor.execute("PRAGMA journal_mode=WAL")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
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
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME,
            input_type TEXT,
            filename TEXT,
            transcript TEXT,
            summary TEXT,
            decisions TEXT,
            action_items TEXT,
            user_id INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            icon TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()

    # Single source of truth for Gemini rate-limit tracking. Every summarise
    # request writes exactly one row here once it's known whether Gemini was
    # actually called (a pre-flight rejection writes nothing, since no real
    # quota was touched). Rolling RPM/TPM/RPD windows are computed by
    # querying this table directly rather than maintaining separate counters,
    # so there's never a second place for usage state to drift out of sync.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            request_type TEXT NOT NULL,
            estimated_tokens INTEGER,
            actual_tokens INTEGER,
            status TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_logs_user_timestamp
        ON usage_logs(user_id, timestamp)
    """)
    conn.commit()

    # Per-user/tier quota assumptions, seeded from Google's published
    # defaults the first time a user is checked (see quota.py) and editable
    # directly in this table. cooldown_until holds the authoritative
    # "try again at" time Gemini itself returned via RetryInfo on a real
    # 429 — pre-flight checks defer to it even when the local rolling-window
    # math still looks like it has room, since Gemini's own accounting is
    # the source of truth.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quota_limits (
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
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()

    # Migration checks for existing databases
    cursor.execute("PRAGMA table_info(users)")
    user_columns = [row[1] for row in cursor.fetchall()]
    if "gemini_api_key" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN gemini_api_key TEXT")
        conn.commit()
    if "firebase_uid" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN firebase_uid TEXT")
        conn.commit()
    if "name" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
        conn.commit()
    if "summary_language" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN summary_language TEXT")
        conn.commit()

    cursor.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if "transcript" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()
    if "user_id" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
        conn.commit()
    if "folder_id" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN folder_id INTEGER")
        conn.commit()
    # Added for the adaptive summary schema (detected content type + a
    # separate key_concepts list alongside decisions/action_items). NULL on
    # every pre-existing row, which is exactly the signal the render layer
    # uses to fall back to the old flat layout for those records.
    if "detected_type" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN detected_type TEXT")
        conn.commit()
    if "key_concepts" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN key_concepts TEXT")
        conn.commit()

    # Added so maybe_relax_limit (see quota.py) can tell how long a tracked
    # limit has been sitting at a downward correction, per-dimension — a
    # single shared timestamp would conflate "RPM was corrected recently"
    # with "RPD was corrected recently" for the same user.
    cursor.execute("PRAGMA table_info(quota_limits)")
    quota_columns = [row[1] for row in cursor.fetchall()]
    for corrected_at_column in ("rpm_limit_corrected_at", "tpm_limit_corrected_at", "rpd_limit_corrected_at"):
        if corrected_at_column not in quota_columns:
            cursor.execute(f"ALTER TABLE quota_limits ADD COLUMN {corrected_at_column} TEXT")
            conn.commit()

    conn.close()


def save_session(input_type, filename, summary, decisions, action_items, transcript=None, user_id=None,
                  detected_type=None, key_concepts=None):
    # `summary`/`decisions` hold the adaptive schema's overview text /
    # decisions_and_takeaways array when detected_type is set (every new
    # summary going forward) — the columns are reused rather than
    # duplicated, since a given row is only ever one shape or the other.
    # `key_concepts` stays NULL for a row saved without detected_type, which
    # is exactly the signal the render layer uses to fall back to the old
    # flat layout.
    decisions = decisions if decisions is not None else []
    action_items = action_items if action_items is not None else []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (created_at, input_type, filename, transcript, summary, decisions, action_items, user_id, detected_type, key_concepts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        # Explicit UTC offset (not the bare datetime.now() this used to be)
        # so every render path — the note list's `new Date(...)`, the docx
        # export's timestamp formatting — can convert to the viewer's local
        # time unambiguously, rather than depending on the server process's
        # OS timezone (UTC in this deployment, but not guaranteed) matching
        # whatever the render side assumes.
        datetime.now(timezone.utc).isoformat(),
        input_type,
        filename,
        transcript,
        summary,
        json.dumps(decisions),
        json.dumps(action_items),
        user_id,
        detected_type,
        json.dumps(key_concepts) if key_concepts is not None else None,
    ))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


def get_all_sessions(user_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            "SELECT id, created_at, input_type, filename, summary, folder_id FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
    else:
        cursor.execute(
            "SELECT id, created_at, input_type, filename, summary, folder_id FROM sessions ORDER BY created_at DESC"
        )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "created_at": r[1], "input_type": r[2], "filename": r[3], "summary": r[4], "folder_id": r[5]}
        for r in rows
    ]


def get_sessions_by_folder(user_id, folder_id):
    # Folders are scoped per-user, so this stays consistent with
    # get_all_sessions' user scoping even though folder_id alone is unique.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, created_at, input_type, filename, summary, folder_id FROM sessions WHERE user_id = ? AND folder_id = ? ORDER BY created_at DESC",
        (user_id, folder_id)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "created_at": r[1], "input_type": r[2], "filename": r[3], "summary": r[4], "folder_id": r[5]}
        for r in rows
    ]


def get_session_by_id(session_id, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT id, created_at, input_type, filename, transcript, summary, decisions, action_items, user_id, detected_type, key_concepts
        FROM sessions
        WHERE id = ?
    """
    params = [session_id]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)

    cursor.execute(query, tuple(params))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    def safe_json_loads(data):
        if not data or not str(data).strip():
            return []
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return []

    # detected_type is the discriminator the frontend renders on: present
    # (every row saved since the adaptive schema shipped) means the
    # overview/key_concepts/decisions_and_takeaways aliases below carry real
    # data; NULL (every pre-existing row) means only the old summary/
    # decisions fields are meaningful and the render layer should fall back
    # to the flat layout. summary/decisions themselves are always populated
    # either way, since the columns are shared between both shapes.
    detected_type = row[9]
    return {
        "id": row[0],
        "created_at": row[1],
        "input_type": row[2],
        "filename": row[3],
        "transcript": row[4],
        "summary": row[5],
        "decisions": safe_json_loads(row[6]),
        "action_items": safe_json_loads(row[7]),
        "user_id": row[8],
        "detected_type": detected_type,
        "overview": row[5] if detected_type else None,
        "key_concepts": safe_json_loads(row[10]) if detected_type else None,
        "decisions_and_takeaways": safe_json_loads(row[6]) if detected_type else None,
    }


def delete_session(session_id, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    else:
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


def create_folder(user_id, name, icon):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO folders (user_id, name, icon, created_at) VALUES (?, ?, ?, ?)",
        (user_id, name, icon, datetime.now().isoformat())
    )
    conn.commit()
    folder_id = cursor.lastrowid
    conn.close()
    return {"id": folder_id, "name": name, "icon": icon, "note_count": 0}


def get_folders(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT folders.id, folders.name, folders.icon, COUNT(sessions.id)
        FROM folders
        LEFT JOIN sessions ON sessions.folder_id = folders.id
        WHERE folders.user_id = ?
        GROUP BY folders.id
        ORDER BY folders.created_at ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "icon": r[2], "note_count": r[3]}
        for r in rows
    ]


def delete_folder(folder_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Notes are never deleted with their folder — just unfiled.
    cursor.execute(
        "UPDATE sessions SET folder_id = NULL WHERE folder_id = ? AND user_id = ?",
        (folder_id, user_id)
    )
    cursor.execute("DELETE FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id))
    conn.commit()
    conn.close()


def assign_sessions_to_folder(session_ids, user_id, folder_id):
    if not session_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in session_ids)
    cursor.execute(
        f"UPDATE sessions SET folder_id = ? WHERE id IN ({placeholders}) AND user_id = ?",
        (folder_id, *session_ids, user_id)
    )
    conn.commit()
    conn.close()


def create_user(username, password, name=None, firebase_uid=None):
    """Creates a new user record with optional name and firebase_uid."""
    password_hash = generate_password_hash(password) if password else ""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, name, firebase_uid, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, password_hash, name, firebase_uid, datetime.now().isoformat())
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def get_user_by_firebase_uid(firebase_uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, firebase_uid, name, created_at FROM users WHERE firebase_uid = ?", (firebase_uid,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "firebase_uid": row[3], "name": row[4], "created_at": row[5]}


def get_user_by_email(email):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, firebase_uid, name, created_at FROM users WHERE username = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "firebase_uid": row[3], "name": row[4], "created_at": row[5]}


def create_or_get_user_from_firebase(firebase_uid, email=None, name=None):
    """Retrieves an existing user or creates one if they don't exist yet."""
    # Try to find by firebase_uid first
    user = get_user_by_firebase_uid(firebase_uid)
    if user:
        if name and not user.get("name"):
            # Update missing name if provided
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
            conn.commit()
            conn.close()
            user["name"] = name
        return user

    # Then try by email (stored in username)
    if email:
        user = get_user_by_email(email)
        if user:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            if name and not user.get("name"):
                cursor.execute("UPDATE users SET firebase_uid = ?, name = ? WHERE id = ?", (firebase_uid, name, user["id"]))
            else:
                cursor.execute("UPDATE users SET firebase_uid = ? WHERE id = ?", (firebase_uid, user["id"]))
            conn.commit()
            conn.close()
            user["firebase_uid"] = firebase_uid
            if name:
                user["name"] = name
            return user

    # Create a new user using email as username
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    username = email or f"firebase:{firebase_uid}"
    cursor.execute(
        "INSERT INTO users (username, password_hash, name, firebase_uid, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, "", name, firebase_uid, datetime.now().isoformat())
    )
    conn.commit()
    user_id = cursor.lastrowid
    cursor.execute("SELECT id, username, password_hash, firebase_uid, name, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return {"id": row[0], "username": row[1], "password_hash": row[2], "firebase_uid": row[3], "name": row[4], "created_at": row[5]}


def update_user_email(user_id, email):
    # `username` doubles as the login email, so this is the single place
    # that needs updating when Firebase confirms a new address.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET username = ? WHERE id = ?", (email, user_id))
    conn.commit()
    conn.close()


def update_user_name(user_id, name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
    conn.commit()
    conn.close()


def delete_user_account(user_id):
    """Cascade-deletes everything owned by a user, then the user row itself."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM folders WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, name, created_at FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "name": row[3], "created_at": row[4]}


def get_user_api_key(user_id):
    # Always scope the API key lookup to the authenticated user's numeric id.
    # This ensures no shared or global key is accidentally returned.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT gemini_api_key FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_api_key(user_id, api_key):
    # Store the provided API key only for the authenticated user's record.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET gemini_api_key = ? WHERE id = ?", (api_key, user_id))
    conn.commit()
    conn.close()


def get_user_summary_language(user_id):
    # NULL/empty means "match the transcript's language" (Gemini's existing
    # default behavior when no language is specified).
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT summary_language FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_summary_language(user_id, language):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET summary_language = ? WHERE id = ?", (language, user_id))
    conn.commit()
    conn.close()


def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, name, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "name": row[3], "created_at": row[4]}


def authenticate_user(username, password):
    user = get_user_by_username(username)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def _utcnow_iso():
    # Naive ISO string in UTC, matching the format SQLite's own
    # datetime('now') produces, so string comparisons in the rolling-window
    # queries below stay apples-to-apples with what gets inserted here.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_usage(user_id, request_type, status, estimated_tokens=None, actual_tokens=None):
    """Records the outcome of one summarise request. Only called once it's
    known whether Gemini was actually invoked — a request rejected by the
    local pre-flight check never reaches here, since it never touched real
    quota."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO usage_logs (user_id, timestamp, request_type, estimated_tokens, actual_tokens, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, _utcnow_iso(), request_type, estimated_tokens, actual_tokens, status))
    conn.commit()
    conn.close()


def get_usage_window(user_id, window):
    """Rolling (not calendar-aligned) usage over the trailing window, summed
    from actual (reconciled) token counts. Only successful calls count
    toward RPM/TPM/RPD — a Gemini-side 429 never consumed the quota it was
    rejected for, so counting it here would double-penalize the user.
    Also returns the oldest timestamp in the window, which callers use to
    work out when the window will next free up capacity."""
    if window not in ("1 minute", "1 day"):
        raise ValueError(f"Unsupported usage window: {window!r}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT COALESCE(SUM(actual_tokens), 0), COUNT(*), MIN(timestamp)
        FROM usage_logs
        WHERE user_id = ? AND status = 'success' AND timestamp >= datetime('now', '-{window}')
    """, (user_id,))
    tokens, requests, oldest_timestamp = cursor.fetchone()
    conn.close()
    return {"tokens": tokens, "requests": requests, "oldest_timestamp": oldest_timestamp}


def cleanup_old_usage_logs(batch_size=500):
    """Deletes usage_logs rows older than the longest rolling window (1 day)
    in small batches so the sweep never holds a write lock for long, per
    SQLite's lack of a native DELETE ... LIMIT."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total_deleted = 0
    while True:
        cursor.execute("""
            DELETE FROM usage_logs
            WHERE rowid IN (
                SELECT rowid FROM usage_logs
                WHERE timestamp < datetime('now', '-1 day')
                LIMIT ?
            )
        """, (batch_size,))
        conn.commit()
        deleted = cursor.rowcount
        total_deleted += max(deleted, 0)
        if deleted < batch_size:
            break
    conn.close()
    return total_deleted


def get_quota_limits(user_id, defaults):
    """Returns the user's tracked RPM/TPM/RPD limits, seeding them from
    `defaults` (Google's published free-tier numbers for the model in use)
    on first access. Stored per-user so different tiers/models can carry
    different assumptions."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tier, rpm_limit, tpm_limit, rpd_limit, cooldown_until, "
        "rpm_limit_corrected_at, tpm_limit_corrected_at, rpd_limit_corrected_at "
        "FROM quota_limits WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    if row is None:
        cursor.execute("""
            INSERT INTO quota_limits (user_id, tier, rpm_limit, tpm_limit, rpd_limit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, defaults["tier"], defaults["rpm"], defaults["tpm"], defaults["rpd"], _utcnow_iso()))
        conn.commit()
        conn.close()
        return {"tier": defaults["tier"], "rpm_limit": defaults["rpm"], "tpm_limit": defaults["tpm"],
                "rpd_limit": defaults["rpd"], "cooldown_until": None,
                "rpm_limit_corrected_at": None, "tpm_limit_corrected_at": None, "rpd_limit_corrected_at": None}
    conn.close()
    return {"tier": row[0], "rpm_limit": row[1], "tpm_limit": row[2], "rpd_limit": row[3], "cooldown_until": row[4],
            "rpm_limit_corrected_at": row[5], "tpm_limit_corrected_at": row[6], "rpd_limit_corrected_at": row[7]}


def update_quota_limit(user_id, dimension, new_limit):
    """Tightens (or otherwise corrects) one tracked dimension after Gemini's
    own QuotaFailure told us our assumption for it was wrong. Stamps that
    dimension's `_corrected_at` so maybe_relax_limit (see quota.py) knows
    when it's safe to re-test whether the correction was too conservative."""
    if dimension not in ("rpm_limit", "tpm_limit", "rpd_limit"):
        raise ValueError(f"Unsupported quota dimension: {dimension!r}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = _utcnow_iso()
    cursor.execute(
        f"UPDATE quota_limits SET {dimension} = ?, updated_at = ?, {dimension}_corrected_at = ? WHERE user_id = ?",
        (new_limit, now, now, user_id)
    )
    conn.commit()
    conn.close()


def relax_quota_limit(user_id, dimension, new_limit):
    """Resets a previously-tightened dimension back toward its tier default.
    Clears `_corrected_at` for that dimension rather than re-stamping it, so
    this reset isn't itself mistaken for a fresh downward correction — the
    limit only drifts down again on a real Gemini 429."""
    if dimension not in ("rpm_limit", "tpm_limit", "rpd_limit"):
        raise ValueError(f"Unsupported quota dimension: {dimension!r}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE quota_limits SET {dimension} = ?, updated_at = ?, {dimension}_corrected_at = NULL WHERE user_id = ?",
        (new_limit, _utcnow_iso(), user_id)
    )
    conn.commit()
    conn.close()


def get_all_quota_limits():
    """Admin view: every user alongside their tracked quota limits. Uses a
    LEFT JOIN because quota_limits rows are only seeded lazily on a user's
    first summarise request — a user who has never triggered one yet still
    shows up here, with every quota field as None."""
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT u.id AS user_id, u.username, u.name,
               q.tier, q.rpm_limit, q.tpm_limit, q.rpd_limit, q.cooldown_until,
               q.rpm_limit_corrected_at, q.tpm_limit_corrected_at, q.rpd_limit_corrected_at,
               q.updated_at
        FROM users u
        LEFT JOIN quota_limits q ON q.user_id = u.id
        ORDER BY u.id
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_quota_tier(user_id, tier):
    """Updates only the display label on a user's quota row. Enforcement
    (check_capacity/compute_quota_status in quota.py) reads the numeric
    rpm/tpm/rpd_limit columns directly and never re-derives them from this
    column, so changing it alone does not change what a user can do —
    rpm_limit/tpm_limit/rpd_limit must be set explicitly to actually raise
    or lower their capacity."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quota_limits SET tier = ?, updated_at = ? WHERE user_id = ?",
        (tier, _utcnow_iso(), user_id)
    )
    conn.commit()
    conn.close()


def start_usage_log_cleanup_thread(interval_seconds=3600):
    """Runs cleanup_old_usage_logs on a fixed interval in a daemon thread, so
    usage_logs (written on every summarise request) doesn't grow unbounded.
    A daemon thread exits automatically with the process; no shutdown hook
    is needed for a best-effort maintenance sweep like this."""
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                cleanup_old_usage_logs()
            except Exception:
                pass

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def set_quota_cooldown(user_id, until_iso):
    """Stores Gemini's authoritative RetryInfo.retryDelay-derived "try again
    at" time so subsequent pre-flight checks defer to it even if the local
    rolling-window math still looks like it has room."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quota_limits SET cooldown_until = ?, updated_at = ? WHERE user_id = ?",
        (until_iso, _utcnow_iso(), user_id)
    )
    conn.commit()
    conn.close()