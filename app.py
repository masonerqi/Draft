import json
import os
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from gemini_client import summarise_transcript, summarise_audio
from database import (
    init_db,
    save_session,
    get_all_sessions,
    get_session_by_id,
    delete_session,
    authenticate_user,
    create_user,
    get_user_by_id,
    get_user_by_username,
    get_user_api_key,
    set_user_api_key,
)

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable must be set")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
CORS(app, supports_credentials=True)

# Initialise database on startup
init_db()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def _parse_request_payload():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.get_json(force=True, silent=True)
    if payload is None:
        raw_body = request.get_data(as_text=True)
        if raw_body:
            try:
                payload = json.loads(raw_body)
            except (ValueError, TypeError):
                payload = None
    if payload is None:
        payload = request.form.to_dict()
    return payload or {}


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return jsonify({"error": "Authentication required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _mask_api_key(api_key):
    if not api_key:
        return None
    if len(api_key) <= 8:
        return api_key[0:2] + "..."
    return f"{api_key[:4]}...{api_key[-4:]}"

# ── Serve frontend ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── Authentication ──────────────────────────────────────────────────────────
@app.route("/register", methods=["POST"])
def register():
    payload = _parse_request_payload()
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if get_user_by_username(username):
        return jsonify({"error": "Username already exists"}), 400

    user_id = create_user(username, password)
    session["user_id"] = user_id
    return jsonify({"id": user_id, "username": username}), 201

@app.route("/login", methods=["POST"])
def login():
    payload = _parse_request_payload()
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    user = authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    session["user_id"] = user["id"]
    return jsonify({"id": user["id"], "username": user["username"]}), 200

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"}), 200

@app.route("/me", methods=["GET"])
def me():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"id": current_user["id"], "username": current_user["username"]}), 200

@app.route("/api/user/settings", methods=["GET"])
@login_required
def get_user_settings():
    current_user = get_current_user()
    api_key = get_user_api_key(current_user["id"])
    return jsonify({
        "api_key_saved": bool(api_key),
        "api_key_masked": _mask_api_key(api_key)
    }), 200

@app.route("/api/user/settings", methods=["POST"])
@login_required
def update_user_settings():
    current_user = get_current_user()
    payload = _parse_request_payload()
    api_key = (payload.get("gemini_api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "Gemini API key is required."}), 400
    set_user_api_key(current_user["id"], api_key)
    return jsonify({"api_key_saved": True, "api_key_masked": _mask_api_key(api_key)}), 200

# ── Summarise ───────────────────────────────────────────────────────────────
@app.route("/summarise", methods=["POST"])
@login_required
def summarise():
    transcript = None
    current_user = get_current_user()
    user_api_key = get_user_api_key(current_user["id"])
    if not user_api_key:
        return jsonify({"error": "No Gemini API key configured. Save your personal Google AI Studio key in Settings."}), 400

    # Accept either a transcript text or an audio file
    if "transcript" in request.form and request.form["transcript"].strip():
        transcript = request.form["transcript"].strip()
        result = summarise_transcript(transcript, user_api_key=user_api_key)
        filename = "paste"
        input_type = "transcript"

    elif "audio" in request.files:
        audio_file = request.files["audio"]
        audio_bytes = audio_file.read()

        if len(audio_bytes) == 0:
            return jsonify({"error": "Empty audio file received"}), 400

        mime_type = audio_file.mimetype or "audio/webm"

        try:
            result = summarise_audio(audio_bytes, mime_type=mime_type, user_api_key=user_api_key)
        except Exception as e:
            return jsonify({"error": f"Gemini audio processing failed: {str(e)}"}), 500

        # Gemini returns the transcript inside the result for audio input
        transcript = result.get("transcript")
        print("DEBUG RESULT:", result, flush=True)
        filename = audio_file.filename or "recording"
        input_type = "audio"

    else:
        return jsonify({"error": "No transcript or audio provided"}), 400

    session_id = save_session(
        input_type=input_type,
        filename=filename,
        summary=result["summary"],
        decisions=result["decisions"],
        action_items=result["action_items"],
        transcript=transcript,
        user_id=current_user["id"] if current_user else None,
    )

    result["session_id"] = session_id
    result["transcript"] = transcript
    return jsonify(result), 200

# ── Export to Text ────────────────────────────────────────────────────────────
@app.route("/sessions/<int:session_id>/export/text", methods=["GET"])
@login_required
def export_session_text(session_id):
    current_user = get_current_user()
    data = get_session_by_id(session_id, user_id=current_user["id"])
    if not data:
        return jsonify({"error": "Session not found"}), 404

    # Format decisions and action items into clean bulleted lists
    decisions_list = "\n".join([f"- {d}" for d in data.get("decisions", [])]) if data.get("decisions") else "No concrete structural decisions resolved."
    actions_list = "\n".join([f"- {a}" for a in data.get("action_items", [])]) if data.get("action_items") else "No pending contextual action items."
    
    # Construct the raw text document structure
    text_content = f"""SUMMARY ANALYSIS
Generated on: {data.get('created_at', 'N/A')}

KEY DISCUSSION POINTS
{data.get('summary', 'No summary available.')}

DECISIONS MADE
{decisions_list}

ACTION ITEMS
{actions_list}

FULL TEXT ARCHIVE
{data.get('transcript', 'No transcript archive available.')}
"""

    # Create a dynamic filename (e.g., summary_session_5.txt)
    filename = f"summary_session_{session_id}.txt"
    
    # Return raw text response with headers that force a browser download wrapper
    return app.response_class(
        text_content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

# ── Sessions ──────────────────────────────────────────────────────────────────
@app.route("/sessions", methods=["GET"])
@login_required
def sessions():
    current_user = get_current_user()
    return jsonify(get_all_sessions(current_user["id"])), 200

@app.route("/sessions/<int:session_id>", methods=["GET"])
@login_required
def get_session_details(session_id):
    current_user = get_current_user()
    data = get_session_by_id(session_id, user_id=current_user["id"])
    if not data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(data), 200

@app.route("/sessions/<int:session_id>", methods=["DELETE"])
@login_required
def remove_session(session_id):
    current_user = get_current_user()
    delete_session(session_id, user_id=current_user["id"])
    return jsonify({"message": "Deleted"}), 200

# ── Run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Binding to 0.0.0.0 maps the port outside the WSL boundary
    app.run(debug=True, host="0.0.0.0", port=5000)
