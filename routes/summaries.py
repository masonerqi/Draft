from flask import Blueprint, request, jsonify, current_app
from routes.utils import get_current_user, login_required
from gemini_client import summarise_transcript, summarise_audio
from database import (
    save_session,
    get_all_sessions,
    get_session_by_id,
    delete_session,
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
        current_app.logger.debug("DEBUG RESULT: %s", result)
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
