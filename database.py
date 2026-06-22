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
            summary TEXT,
            decisions TEXT,
            action_items TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_session(input_type, filename, summary, decisions, action_items):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (created_at, input_type, filename, summary, decisions, action_items)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        input_type,
        filename,
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
    cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "created_at": row[1],
        "input_type": row[2],
        "filename": row[3],
        "summary": row[4],
        "decisions": json.loads(row[5]),
        "action_items": json.loads(row[6])
    }

def delete_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()