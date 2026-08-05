import os
import sqlite3
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.environ.get("DATABASE_PATH")
if not DB_PATH:
    # default to persistent path inside container /app/data/database.db
    DB_PATH = os.path.join(os.getcwd(), "data", "database.db")


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

    cursor.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if "transcript" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()
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