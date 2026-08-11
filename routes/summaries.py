import mimetypes
import os
import tempfile

from flask import Blueprint, request, jsonify, current_app
from routes.utils import get_current_user, login_required
from gemini_client import summarise_transcript, summarise_media, is_invalid_api_key_error
from database import (
    save_session,
    get_all_sessions,
    get_session_by_id,
    delete_session,
    get_user_summary_language,
)

summaries_bp = Blueprint("summaries_bp", __name__)


@summaries_bp.route("/summarise", methods=["POST"])
@login_required
def summarise():
    transcript = None
    current_user = get_current_user()
    from database import get_user_api_key

    # Retrieve the Gemini API key scoped to the currently authenticated user.
    # This prevents any shared or global API key from being used for another account.
    user_api_key = get_user_api_key(current_user["id"])
    if not user_api_key:
        return jsonify({"error": "No Gemini API key configured. Save your personal Google AI Studio key in Settings."}), 400

    # The recorder's picker (templates/note.html) only controls live speech
    # recognition now — what language the summary is written in is the
    # user's persistent Settings > Preferences choice instead, so every note
    # (regardless of what was selected when recording) follows the same
    # saved preference. None means "match the transcript's language".
    summary_language = get_user_summary_language(current_user["id"])

    # Accept either a transcript text or an audio file
    if "transcript" in request.form and request.form["transcript"].strip():
        transcript = request.form["transcript"].strip()
        try:
            result = summarise_transcript(transcript, user_api_key=user_api_key, language=summary_language)
        except Exception as e:
            if is_invalid_api_key_error(e):
                return jsonify({"error": "Your Gemini API key appears to be invalid or expired. Please update it in Settings."}), 401
            return jsonify({"error": f"Gemini processing failed: {str(e)}"}), 500
        filename = "paste"
        input_type = "transcript"

    elif "media" in request.files:
        media_file = request.files["media"]
        filename = media_file.filename or "recording"

        # Werkzeug's Content-Type sniffing is frequently missing or generic
        # (application/octet-stream) for .m4a/.webm uploads on Windows —
        # fall back to guessing from the filename extension.
        mime_type = media_file.mimetype
        if not mime_type or mime_type == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(filename)
            mime_type = guessed or mime_type or "application/octet-stream"

        suffix = os.path.splitext(filename)[1]
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                media_file.save(tmp)
                tmp_path = tmp.name

            if os.path.getsize(tmp_path) == 0:
                return jsonify({"error": "Empty file received"}), 400

            try:
                result = summarise_media(tmp_path, mime_type=mime_type, user_api_key=user_api_key, language=summary_language)
            except Exception as e:
                if is_invalid_api_key_error(e):
                    return jsonify({"error": "Your Gemini API key appears to be invalid or expired. Please update it in Settings."}), 401
                return jsonify({"error": f"Gemini media processing failed: {str(e)}"}), 500
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Gemini returns the transcript inside the result for media input
        transcript = result.get("transcript")
        current_app.logger.debug("DEBUG RESULT: %s", result)
        input_type = "media"

    else:
        return jsonify({"error": "No transcript or media file provided"}), 400

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


@summaries_bp.route("/sessions/<int:session_id>/export/text", methods=["GET"])
@login_required
def export_session_text(session_id):
    current_user = get_current_user()
    data = get_session_by_id(session_id, user_id=current_user["id"])
    if not data:
        return jsonify({"error": "Session not found"}), 404

    decisions_list = "\n".join([f"- {d}" for d in data.get("decisions", [])]) if data.get("decisions") else "No concrete structural decisions resolved."
    actions_list = "\n".join([f"- {a}" for a in data.get("action_items", [])]) if data.get("action_items") else "No pending contextual action items."

    text_content = f"""SUMMARY ANALYSIS\nGenerated on: {data.get('created_at', 'N/A')}\n\nKEY DISCUSSION POINTS\n{data.get('summary', 'No summary available.')}\n\nDECISIONS MADE\n{decisions_list}\n\nACTION ITEMS\n{actions_list}\n\nFULL TEXT ARCHIVE\n{data.get('transcript', 'No transcript archive available.')}\n"""

    filename = f"summary_session_{session_id}.txt"

    return current_app.response_class(
        text_content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@summaries_bp.route("/sessions", methods=["GET"])
@login_required
def sessions():
    current_user = get_current_user()
    return jsonify(get_all_sessions(current_user["id"])), 200


@summaries_bp.route("/sessions/<int:session_id>", methods=["GET"])
@login_required
def get_session_details(session_id):
    current_user = get_current_user()
    data = get_session_by_id(session_id, user_id=current_user["id"])
    if not data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(data), 200


@summaries_bp.route("/sessions/<int:session_id>", methods=["DELETE"])
@login_required
def remove_session(session_id):
    current_user = get_current_user()
    delete_session(session_id, user_id=current_user["id"])
    return jsonify({"message": "Deleted"}), 200
