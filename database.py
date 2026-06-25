import sqlite3
import json
from datetime import datetime

DB_PATH = "sessions.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME,
            input_type TEXT,
            filename TEXT,
            transcript TEXT,
            summary TEXT,
            decisions TEXT,
            action_items TEXT
        )
    """)
    conn.commit()

    # Lightweight migration for existing databases created before this
    # column existed — safe to run every startup, no-op if already present.
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if "transcript" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()

    conn.close()


def save_session(input_type, filename, summary, decisions, action_items, transcript=None):
    decisions = decisions if decisions is not None else []
    action_items = action_items if action_items is not None else []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (created_at, input_type, filename, transcript, summary, decisions, action_items)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        input_type,
        filename,
        transcript,
        summary,
        json.dumps(decisions),
        json.dumps(action_items)
    ))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


def get_all_sessions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, created_at, input_type, filename, summary FROM sessions ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "created_at": r[1], "input_type": r[2], "filename": r[3], "summary": r[4]}
        for r in rows
    ]


def get_session_by_id(session_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Explicitly define the column order in the query
    cursor.execute("""
        SELECT id, created_at, input_type, filename, transcript, summary, decisions, action_items 
        FROM sessions 
        WHERE id = ?
    """, (session_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    # Helper to safely parse JSON strings
    def safe_json_loads(data):
        if not data or not str(data).strip():
            return []
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return []

    # Now the indexes will ALWAYS match this exact query's order
    return {
        "id": row[0],
        "created_at": row[1],
        "input_type": row[2],
        "filename": row[3],
        "transcript": row[4],
        "summary": row[5],
        "decisions": safe_json_loads(row[6]),
        "action_items": safe_json_loads(row[7])
    }


def delete_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()