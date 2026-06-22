from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from gemini_client import summarise_transcript, summarise_audio
from database import init_db, save_session, get_all_sessions, get_session_by_id, delete_session
import os

app = Flask(__name__, static_folder="static")
CORS(app)

# Initialise database on startup
init_db()

# ── Serve frontend ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── Summarise ───────────────────────────────────────────────────────────────
@app.route("/summarise", methods=["POST"])
def summarise():
    # Accept either a transcript text or an audio file
    if "transcript" in request.form and request.form["transcript"].strip():
        transcript = request.form["transcript"].strip()
        result = summarise_transcript(transcript)
        filename = "paste"

    elif "audio" in request.files:
        audio_file = request.files["audio"]
        audio_bytes = audio_file.read()

        if len(audio_bytes) == 0:
            return jsonify({"error": "Empty audio file received"}), 400

        # Browser MediaRecorder typically sends webm; fall back to its
        # reported content type if present
        mime_type = audio_file.mimetype or "audio/webm"

        try:
            result = summarise_audio(audio_bytes, mime_type=mime_type)
        except Exception as e:
            return jsonify({"error": f"Gemini audio processing failed: {str(e)}"}), 500

        filename = audio_file.filename or "recording"

    else:
        return jsonify({"error": "No transcript or audio provided"}), 400

    session_id = save_session(
        input_type="audio" if "audio" in request.files else "transcript",
        filename=filename,
        summary=result["summary"],
        decisions=result["decisions"],
        action_items=result["action_items"]
    )

    result["session_id"] = session_id
    return jsonify(result), 200

# ── Sessions ──────────────────────────────────────────────────────────────────
@app.route("/sessions", methods=["GET"])
def sessions():
    return jsonify(get_all_sessions()), 200

@app.route("/sessions/<int:session_id>", methods=["GET"])
def session(session_id):
    data = get_session_by_id(session_id)
    if not data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(data), 200

@app.route("/sessions/<int:session_id>", methods=["DELETE"])
def remove_session(session_id):
    delete_session(session_id)
    return jsonify({"message": "Deleted"}), 200

# ── Run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Binding to 0.0.0.0 maps the port outside the WSL boundary
    app.run(debug=True, host="0.0.0.0", port=5000)