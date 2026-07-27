import os
import sqlite3
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.environ.get("DATABASE_PATH")
if not DB_PATH:
    DB_PATH = os.path.join(os.path.dirname(__file__), "sessions.db")


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            gemini_api_key TEXT,
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

    cursor.execute("PRAGMA table_info(users)")
    user_columns = [row[1] for row in cursor.fetchall()]
    if "gemini_api_key" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN gemini_api_key TEXT")
        conn.commit()

    cursor.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if "transcript" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()
        columns.append("transcript")
    if "user_id" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
        conn.commit()

    conn.close()


def save_session(input_type, filename, summary, decisions, action_items, transcript=None, user_id=None):
    decisions = decisions if decisions is not None else []
    action_items = action_items if action_items is not None else []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (created_at, input_type, filename, transcript, summary, decisions, action_items, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        input_type,
        filename,
        transcript,
        summary,
        json.dumps(decisions),
        json.dumps(action_items),
        user_id
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
            "SELECT id, created_at, input_type, filename, summary FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
    else:
        cursor.execute(
            "SELECT id, created_at, input_type, filename, summary FROM sessions ORDER BY created_at DESC"
        )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "created_at": r[1], "input_type": r[2], "filename": r[3], "summary": r[4]}
        for r in rows
    ]


def get_session_by_id(session_id, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT id, created_at, input_type, filename, transcript, summary, decisions, action_items, user_id
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

    return {
        "id": row[0],
        "created_at": row[1],
        "input_type": row[2],
        "filename": row[3],
        "transcript": row[4],
        "summary": row[5],
        "decisions": safe_json_loads(row[6]),
        "action_items": safe_json_loads(row[7]),
        "user_id": row[8]
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


def create_user(username, password):
    password_hash = generate_password_hash(password)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, datetime.now().isoformat())
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def get_user_by_username(username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, created_at FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "created_at": row[3]}


def get_user_api_key(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT gemini_api_key FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_api_key(user_id, api_key):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET gemini_api_key = ? WHERE id = ?", (api_key, user_id))
    conn.commit()
    conn.close()


def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "created_at": row[3]}


def authenticate_user(username, password):
    user = get_user_by_username(username)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user