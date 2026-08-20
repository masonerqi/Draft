import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool
from werkzeug.security import generate_password_hash, check_password_hash

# Vercel's Postgres integrations (and most hosted providers) commonly expose
# either name depending on how the database was provisioned/connected, so
# both are accepted rather than forcing one convention.
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")

_pool = None
_pool_pid = None
_pool_lock = threading.Lock()


def _get_pool():
    """Lazily creates the process-wide connection pool on first use. Opening
    a fresh TCP+TLS+auth connection to a remote Postgres host on every query
    (the previous behavior) added seconds of latency per request; a pool
    pays that cost only when it needs to grow, and reuses connections for
    every request after that.

    The pid check rebuilds the pool after a fork. gunicorn runs with
    --preload, so init_db() opens the first connection in the *master*
    process and every worker inherits that pool object — and with it the
    same open sockets. Two workers using one socket corrupts the Postgres
    wire protocol, which surfaces as sporadic, unreproducible query errors.
    A forked child therefore starts its own pool rather than reusing the
    inherited one."""
    global _pool, _pool_pid
    pid = os.getpid()
    if _pool is None or _pool_pid != pid:
        with _pool_lock:
            if _pool is None or _pool_pid != pid:
                if not DATABASE_URL:
                    raise RuntimeError(
                        "DATABASE_URL (or POSTGRES_URL) must be set to a Postgres connection string."
                    )
                # Deliberately not closed: the inherited connections belong
                # to the parent, which is still using them.
                _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
                _pool_pid = pid
    return _pool


class _PooledConnection:
    """Transparent proxy around a pooled connection. Every call site in this
    module already calls `conn.close()` when it's done; rather than rewrite
    every one of them to return the connection explicitly, this makes that
    existing `close()` call return the connection to the pool instead of
    tearing down the socket. psycopg2's pool rolls back any uncommitted
    transaction on the connection before reusing it, so a caller that closes
    without committing still leaves the next borrower a clean connection."""

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        self._pool.putconn(self._conn)


def hash_api_key(api_key):
    """Stable per-key identifier for quota tracking. Google enforces quota
    against the API key itself, so every piece of tracked usage state is
    keyed by this rather than by user_id — a user may hold several keys and
    switch between them, and each key's history has to survive the switch.
    Hashed (not stored raw) because these rows outlive the key's presence in
    `users.gemini_api_key`; the hash only ever needs to be compared, never
    reversed."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_db_connection():
    """Borrows a connection to the Postgres database from the process-wide
    pool (created on first call). A single hosted database (not a local
    file) is required so every app instance — Vercel can route requests to
    different, independent instances — sees the same data."""
    pool = _get_pool()
    return _PooledConnection(pool, pool.getconn())


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            gemini_api_key TEXT,
            firebase_uid TEXT UNIQUE,
            summary_language TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY,
            created_at TEXT,
            input_type TEXT,
            filename TEXT,
            transcript TEXT,
            summary TEXT,
            decisions TEXT,
            action_items TEXT,
            user_id INTEGER REFERENCES users(id),
            folder_id INTEGER,
            detected_type TEXT,
            key_concepts TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            icon TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Single source of truth for Gemini rate-limit tracking. Every summarise
    # request writes exactly one row here once it's known whether Gemini was
    # actually called (a pre-flight rejection writes nothing, since no real
    # quota was touched). Rolling RPM/TPM/RPD windows are computed by
    # querying this table directly rather than maintaining separate counters,
    # so there's never a second place for usage state to drift out of sync.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            key_hash TEXT,
            timestamp TEXT NOT NULL,
            request_type TEXT NOT NULL,
            estimated_tokens INTEGER,
            actual_tokens INTEGER,
            status TEXT NOT NULL
        )
    """)

    # Per-key/tier quota assumptions, seeded from Google's published defaults
    # the first time a given API key is checked (see quota.py) and editable
    # directly in this table. cooldown_until holds the authoritative
    # "try again at" time Gemini itself returned via RetryInfo on a real
    # 429 — pre-flight checks defer to it even when the local rolling-window
    # math still looks like it has room, since Gemini's own accounting is
    # the source of truth.
    #
    # Keyed by (user_id, key_hash), not user_id alone: Google enforces quota
    # against the API key, so a user holding several keys has genuinely
    # independent budgets. One row per key means switching keys — including
    # switching *back* to a previously-used one — resumes that key's own
    # history instead of resetting it or inheriting another key's cooldown.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quota_limits (
            user_id INTEGER NOT NULL REFERENCES users(id),
            key_hash TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free',
            rpm_limit INTEGER NOT NULL,
            tpm_limit INTEGER NOT NULL,
            rpd_limit INTEGER NOT NULL,
            cooldown_until TEXT,
            updated_at TEXT NOT NULL,
            rpm_limit_corrected_at TEXT,
            tpm_limit_corrected_at TEXT,
            rpd_limit_corrected_at TEXT,
            PRIMARY KEY (user_id, key_hash)
        )
    """)

    # One row per /summarise submission. Summarising a long recording takes
    # minutes — far longer than the platform will hold a single HTTP request
    # open — so the request only enqueues the work and returns a job id; the
    # outcome (result payload or the error the synchronous route used to
    # return inline) lands here for the client to poll. Stored in Postgres
    # rather than in process memory because Vercel routes requests to
    # independent instances: the poll for a job very often arrives at an
    # instance other than the one running it.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS summary_jobs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL,
            input_type TEXT NOT NULL,
            filename TEXT,
            result TEXT,
            session_id INTEGER,
            error_message TEXT,
            error_status INTEGER,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_logs_user_key_timestamp
        ON usage_logs(user_id, key_hash, timestamp)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_summary_jobs_user_created
        ON summary_jobs(user_id, created_at)
    """)

    conn.commit()
    cursor.close()
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

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (created_at, input_type, filename, transcript, summary, decisions, action_items, user_id, detected_type, key_concepts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
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
    session_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return session_id


def get_all_sessions(user_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            "SELECT id, created_at, input_type, filename, summary, folder_id FROM sessions WHERE user_id = %s ORDER BY created_at DESC",
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, created_at, input_type, filename, summary, folder_id FROM sessions WHERE user_id = %s AND folder_id = %s ORDER BY created_at DESC",
        (user_id, folder_id)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "created_at": r[1], "input_type": r[2], "filename": r[3], "summary": r[4], "folder_id": r[5]}
        for r in rows
    ]


def get_session_by_id(session_id, user_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT id, created_at, input_type, filename, transcript, summary, decisions, action_items, user_id, detected_type, key_concepts
        FROM sessions
        WHERE id = %s
    """
    params = [session_id]
    if user_id is not None:
        query += " AND user_id = %s"
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
    conn = get_db_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute("DELETE FROM sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
    else:
        cursor.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
    conn.commit()
    conn.close()


# --- Asynchronous summarise jobs -------------------------------------------
#
# A job moves 'queued' -> 'processing' -> 'succeeded' | 'failed' and never
# leaves a terminal state. `result` holds the exact JSON payload the old
# synchronous route returned on success; `error_status`/`error_message`/
# `error_detail` hold the exact status code and body it returned on failure,
# so the client's error handling is unchanged by the move to polling.

SUMMARY_JOB_PENDING_STATUSES = ("queued", "processing")


def create_summary_job(job_id, user_id, input_type, filename=None):
    now = _utcnow_iso()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO summary_jobs (id, user_id, status, input_type, filename, created_at, updated_at)
        VALUES (%s, %s, 'queued', %s, %s, %s, %s)
    """, (job_id, user_id, input_type, filename, now, now))
    conn.commit()
    conn.close()
    return job_id


def mark_summary_job_processing(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE summary_jobs SET status = 'processing', updated_at = %s WHERE id = %s",
        (_utcnow_iso(), job_id)
    )
    conn.commit()
    conn.close()


def complete_summary_job(job_id, result, session_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE summary_jobs
        SET status = 'succeeded', result = %s, session_id = %s, updated_at = %s
        WHERE id = %s
    """, (json.dumps(result), session_id, _utcnow_iso(), job_id))
    conn.commit()
    conn.close()


def fail_summary_job(job_id, message, status_code=500, detail=None):
    """Records a terminal failure. `detail` carries whatever extra fields the
    old inline error body had (e.g. a 429's resets_in_seconds) so the polling
    endpoint can reproduce that body verbatim."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE summary_jobs
        SET status = 'failed', error_message = %s, error_status = %s, error_detail = %s, updated_at = %s
        WHERE id = %s
    """, (message, status_code, json.dumps(detail) if detail else None, _utcnow_iso(), job_id))
    conn.commit()
    conn.close()


def get_summary_job(job_id, user_id=None):
    """Reads one job. `user_id` scopes the lookup to its owner — another
    account asking for the id gets the same "not found" as a nonexistent one,
    so a job id is never a way to read someone else's summary."""
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT id, user_id, status, input_type, filename, result, session_id,
               error_message, error_status, error_detail, created_at, updated_at
        FROM summary_jobs
        WHERE id = %s
    """
    params = [job_id]
    if user_id is not None:
        query += " AND user_id = %s"
        params.append(user_id)
    cursor.execute(query, tuple(params))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "user_id": row[1],
        "status": row[2],
        "input_type": row[3],
        "filename": row[4],
        "result": json.loads(row[5]) if row[5] else None,
        "session_id": row[6],
        "error_message": row[7],
        "error_status": row[8],
        "error_detail": json.loads(row[9]) if row[9] else None,
        "created_at": row[10],
        "updated_at": row[11],
        "age_seconds": _age_seconds(row[10]),
    }


def _age_seconds(timestamp):
    """Seconds since a `_utcnow_iso()` timestamp, or None if it can't be
    parsed. Used to spot jobs whose worker died mid-flight (the instance was
    recycled), which would otherwise stay 'processing' forever."""
    try:
        started = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


def cleanup_old_summary_jobs(max_age_hours=24):
    """Drops finished jobs older than `max_age_hours`. The summary itself
    lives in `sessions` and is unaffected — this only clears the short-lived
    handoff rows the polling endpoint reads."""
    cutoff = _utc_cutoff_iso(timedelta(hours=max_age_hours))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM summary_jobs WHERE created_at < %s", (cutoff,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return max(deleted, 0)


def create_folder(user_id, name, icon):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO folders (user_id, name, icon, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, name, icon, datetime.now().isoformat())
    )
    folder_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return {"id": folder_id, "name": name, "icon": icon, "note_count": 0}


def get_folders(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT folders.id, folders.name, folders.icon, COUNT(sessions.id)
        FROM folders
        LEFT JOIN sessions ON sessions.folder_id = folders.id
        WHERE folders.user_id = %s
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
    conn = get_db_connection()
    cursor = conn.cursor()
    # Notes are never deleted with their folder — just unfiled.
    cursor.execute(
        "UPDATE sessions SET folder_id = NULL WHERE folder_id = %s AND user_id = %s",
        (folder_id, user_id)
    )
    cursor.execute("DELETE FROM folders WHERE id = %s AND user_id = %s", (folder_id, user_id))
    conn.commit()
    conn.close()


def assign_sessions_to_folder(session_ids, user_id, folder_id):
    if not session_ids:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ",".join(["%s"] * len(session_ids))
    cursor.execute(
        f"UPDATE sessions SET folder_id = %s WHERE id IN ({placeholders}) AND user_id = %s",
        (folder_id, *session_ids, user_id)
    )
    conn.commit()
    conn.close()


def create_user(username, password, name=None, firebase_uid=None):
    """Creates a new user record with optional name and firebase_uid."""
    password_hash = generate_password_hash(password) if password else ""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, name, firebase_uid, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (username, password_hash, name, firebase_uid, datetime.now().isoformat())
    )
    user_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return user_id


def get_user_by_firebase_uid(firebase_uid):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, firebase_uid, name, created_at FROM users WHERE firebase_uid = %s", (firebase_uid,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "firebase_uid": row[3], "name": row[4], "created_at": row[5]}


def get_user_by_email(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, firebase_uid, name, created_at FROM users WHERE username = %s", (email,))
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
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET name = %s WHERE id = %s", (name, user["id"]))
            conn.commit()
            conn.close()
            user["name"] = name
        return user

    # Then try by email (stored in username)
    if email:
        user = get_user_by_email(email)
        if user:
            conn = get_db_connection()
            cursor = conn.cursor()
            if name and not user.get("name"):
                cursor.execute("UPDATE users SET firebase_uid = %s, name = %s WHERE id = %s", (firebase_uid, name, user["id"]))
            else:
                cursor.execute("UPDATE users SET firebase_uid = %s WHERE id = %s", (firebase_uid, user["id"]))
            conn.commit()
            conn.close()
            user["firebase_uid"] = firebase_uid
            if name:
                user["name"] = name
            return user

    # Create a new user using email as username
    conn = get_db_connection()
    cursor = conn.cursor()
    username = email or f"firebase:{firebase_uid}"
    cursor.execute("""
        INSERT INTO users (username, password_hash, name, firebase_uid, created_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, username, password_hash, firebase_uid, name, created_at
    """, (username, "", name, firebase_uid, datetime.now().isoformat()))
    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return {"id": row[0], "username": row[1], "password_hash": row[2], "firebase_uid": row[3], "name": row[4], "created_at": row[5]}


def update_user_email(user_id, email):
    # `username` doubles as the login email, so this is the single place
    # that needs updating when Firebase confirms a new address.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET username = %s WHERE id = %s", (email, user_id))
    conn.commit()
    conn.close()


def update_user_name(user_id, name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))
    conn.commit()
    conn.close()


def delete_user_account(user_id):
    """Cascade-deletes everything owned by a user, then the user row itself."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM summary_jobs WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM folders WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, name, created_at FROM users WHERE username = %s", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "name": row[3], "created_at": row[4]}


def get_user_api_key(user_id):
    # Always scope the API key lookup to the authenticated user's numeric id.
    # This ensures no shared or global key is accidentally returned.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT gemini_api_key FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_api_key(user_id, api_key):
    # Store the provided API key only for the authenticated user's record.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET gemini_api_key = %s WHERE id = %s", (api_key, user_id))
    conn.commit()
    conn.close()


def get_user_summary_language(user_id):
    # NULL/empty means "match the transcript's language" (Gemini's existing
    # default behavior when no language is specified).
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT summary_language FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_summary_language(user_id, language):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET summary_language = %s WHERE id = %s", (language, user_id))
    conn.commit()
    conn.close()


def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, name, created_at FROM users WHERE id = %s", (user_id,))
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
    # Naive ISO string in UTC (no SQL-side "now" function involved), so
    # string comparisons in the rolling-window queries below stay
    # apples-to-apples with what gets inserted here.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_cutoff_iso(delta):
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d %H:%M:%S")


def log_usage(user_id, key_hash, request_type, status, estimated_tokens=None, actual_tokens=None):
    """Records the outcome of one summarise request against the specific API
    key that made it. Only called once it's known whether Gemini was
    actually invoked — a request rejected by the local pre-flight check
    never reaches here, since it never touched real quota."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO usage_logs (user_id, key_hash, timestamp, request_type, estimated_tokens, actual_tokens, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (user_id, key_hash, _utcnow_iso(), request_type, estimated_tokens, actual_tokens, status))
    conn.commit()
    conn.close()


_USAGE_WINDOW_DELTAS = {"1 minute": timedelta(minutes=1), "1 day": timedelta(days=1)}


def get_usage_window(user_id, key_hash, window):
    """Rolling (not calendar-aligned) usage for one API key over the trailing
    window, summed from actual (reconciled) token counts. Scoped to
    `key_hash` because that's the granularity Google enforces at — usage
    made with a different key of the same user's is a different budget
    entirely. Only successful calls count toward RPM/TPM/RPD — a Gemini-side
    429 never consumed the quota it was rejected for, so counting it here
    would double-penalize the user. Also returns the oldest timestamp in the
    window, which callers use to work out when the window will next free up
    capacity."""
    if window not in _USAGE_WINDOW_DELTAS:
        raise ValueError(f"Unsupported usage window: {window!r}")
    cutoff = _utc_cutoff_iso(_USAGE_WINDOW_DELTAS[window])
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(SUM(actual_tokens), 0), COUNT(*), MIN(timestamp)
        FROM usage_logs
        WHERE user_id = %s AND key_hash = %s AND status = 'success'
          AND timestamp >= %s
    """, (user_id, key_hash, cutoff))
    tokens, requests, oldest_timestamp = cursor.fetchone()
    conn.close()
    return {"tokens": tokens, "requests": requests, "oldest_timestamp": oldest_timestamp}


def cleanup_old_usage_logs(batch_size=500):
    """Deletes usage_logs rows older than the longest rolling window (1 day)
    in small batches so the sweep never holds a write lock for long."""
    cutoff = _utc_cutoff_iso(timedelta(days=1))
    conn = get_db_connection()
    cursor = conn.cursor()
    total_deleted = 0
    while True:
        cursor.execute("""
            DELETE FROM usage_logs
            WHERE id IN (
                SELECT id FROM usage_logs
                WHERE timestamp < %s
                LIMIT %s
            )
        """, (cutoff, batch_size))
        conn.commit()
        deleted = cursor.rowcount
        total_deleted += max(deleted, 0)
        if deleted < batch_size:
            break
    conn.close()
    return total_deleted


_QUOTA_SELECT = (
    "SELECT tier, rpm_limit, tpm_limit, rpd_limit, cooldown_until, "
    "rpm_limit_corrected_at, tpm_limit_corrected_at, rpd_limit_corrected_at "
    "FROM quota_limits WHERE user_id = %s AND key_hash = %s"
)


def _quota_row_to_dict(row):
    return {
        "tier": row[0], "rpm_limit": row[1], "tpm_limit": row[2], "rpd_limit": row[3],
        "cooldown_until": row[4], "rpm_limit_corrected_at": row[5],
        "tpm_limit_corrected_at": row[6], "rpd_limit_corrected_at": row[7],
    }


def get_quota_limits(user_id, key_hash, defaults):
    """Returns the tracked RPM/TPM/RPD limits for one of the user's API keys,
    seeding them from `defaults` (Google's published free-tier numbers for
    the model in use) the first time that key is seen. Stored per key, so
    each key a user holds carries its own limits, cooldown, and
    corrections."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(_QUOTA_SELECT, (user_id, key_hash))
    row = cursor.fetchone()
    if row is not None:
        conn.close()
        return _quota_row_to_dict(row)

    try:
        cursor.execute("""
            INSERT INTO quota_limits (user_id, key_hash, tier, rpm_limit, tpm_limit, rpd_limit, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (user_id, key_hash, defaults["tier"], defaults["rpm"], defaults["tpm"], defaults["rpd"], _utcnow_iso()))
        conn.commit()
        conn.close()
        return {"tier": defaults["tier"], "rpm_limit": defaults["rpm"], "tpm_limit": defaults["tpm"],
                "rpd_limit": defaults["rpd"], "cooldown_until": None,
                "rpm_limit_corrected_at": None, "tpm_limit_corrected_at": None, "rpd_limit_corrected_at": None}
    except psycopg2.errors.UniqueViolation:
        # Two app instances raced to seed the same never-seen key — the
        # loser reads back whichever row the winner just committed instead
        # of erroring, now that concurrent instances hitting this at once is
        # the normal case rather than a rare local-dev race.
        conn.rollback()
        cursor.execute(_QUOTA_SELECT, (user_id, key_hash))
        row = cursor.fetchone()
        conn.close()
        return _quota_row_to_dict(row)


def update_quota_limit(user_id, key_hash, dimension, new_limit):
    """Tightens (or otherwise corrects) one tracked dimension after Gemini's
    own QuotaFailure told us our assumption for it was wrong. Stamps that
    dimension's `_corrected_at` so maybe_relax_limit (see quota.py) knows
    when it's safe to re-test whether the correction was too conservative."""
    if dimension not in ("rpm_limit", "tpm_limit", "rpd_limit"):
        raise ValueError(f"Unsupported quota dimension: {dimension!r}")
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _utcnow_iso()
    cursor.execute(
        f"UPDATE quota_limits SET {dimension} = %s, updated_at = %s, {dimension}_corrected_at = %s "
        "WHERE user_id = %s AND key_hash = %s",
        (new_limit, now, now, user_id, key_hash)
    )
    conn.commit()
    conn.close()


def relax_quota_limit(user_id, key_hash, dimension, new_limit):
    """Resets a previously-tightened dimension back toward its tier default.
    Clears `_corrected_at` for that dimension rather than re-stamping it, so
    this reset isn't itself mistaken for a fresh downward correction — the
    limit only drifts down again on a real Gemini 429."""
    if dimension not in ("rpm_limit", "tpm_limit", "rpd_limit"):
        raise ValueError(f"Unsupported quota dimension: {dimension!r}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE quota_limits SET {dimension} = %s, updated_at = %s, {dimension}_corrected_at = NULL "
        "WHERE user_id = %s AND key_hash = %s",
        (new_limit, _utcnow_iso(), user_id, key_hash)
    )
    conn.commit()
    conn.close()


def get_all_quota_limits():
    """Admin view: every user alongside the tracked quota limits for each API
    key they've used, one row per (user, key). Uses a LEFT JOIN because
    quota_limits rows are only seeded lazily on a key's first summarise
    request — a user who has never triggered one yet still shows up here,
    with every quota field as None. `is_active_key` marks the row for the
    key the user currently has saved, which is the one the admin PATCH
    endpoint targets; older keys remain listed so their retained history is
    visible."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("""
        SELECT u.id AS user_id, u.username, u.name, q.key_hash,
               q.tier, q.rpm_limit, q.tpm_limit, q.rpd_limit, q.cooldown_until,
               q.rpm_limit_corrected_at, q.tpm_limit_corrected_at, q.rpd_limit_corrected_at,
               q.updated_at
        FROM users u
        LEFT JOIN quota_limits q ON q.user_id = u.id
        ORDER BY u.id, q.updated_at
    """)
    rows = cursor.fetchall()
    cursor.execute(
        "SELECT id, gemini_api_key FROM users WHERE gemini_api_key IS NOT NULL AND gemini_api_key != ''"
    )
    active_hashes = {row["id"]: hash_api_key(row["gemini_api_key"]) for row in cursor.fetchall()}
    conn.close()
    result = []
    for row in rows:
        entry = dict(row)
        entry["is_active_key"] = (
            entry["key_hash"] is not None and active_hashes.get(entry["user_id"]) == entry["key_hash"]
        )
        result.append(entry)
    return result


def set_quota_tier(user_id, key_hash, tier):
    """Updates only the display label on one key's quota row. Enforcement
    (check_capacity/compute_quota_status in quota.py) reads the numeric
    rpm/tpm/rpd_limit columns directly and never re-derives them from this
    column, so changing it alone does not change what a user can do —
    rpm_limit/tpm_limit/rpd_limit must be set explicitly to actually raise
    or lower their capacity."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quota_limits SET tier = %s, updated_at = %s WHERE user_id = %s AND key_hash = %s",
        (tier, _utcnow_iso(), user_id, key_hash)
    )
    conn.commit()
    conn.close()


def start_maintenance_thread(interval_seconds=3600):
    """Sweeps the two tables that grow on every summarise request —
    usage_logs and summary_jobs — on a fixed interval in a daemon thread, so
    neither grows unbounded. A daemon thread exits automatically with the
    process; no shutdown hook is needed for a best-effort sweep like this.
    Each sweep is independent: one failing never skips the other."""
    def _loop():
        while True:
            time.sleep(interval_seconds)
            for sweep in (cleanup_old_usage_logs, cleanup_old_summary_jobs):
                try:
                    sweep()
                except Exception:
                    pass

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def set_quota_cooldown(user_id, key_hash, until_iso):
    """Stores Gemini's authoritative RetryInfo.retryDelay-derived "try again
    at" time so subsequent pre-flight checks defer to it even if the local
    rolling-window math still looks like it has room. Scoped to the key that
    was actually told to back off — another key of the same user's is a
    separate budget and isn't under this cooldown."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quota_limits SET cooldown_until = %s, updated_at = %s WHERE user_id = %s AND key_hash = %s",
        (until_iso, _utcnow_iso(), user_id, key_hash)
    )
    conn.commit()
    conn.close()
