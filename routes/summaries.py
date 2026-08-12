import mimetypes
import os
import tempfile

from flask import Blueprint, request, jsonify, current_app, send_file
from routes.utils import get_current_user, login_required
from gemini_client import (
    summarise_transcript,
    summarise_media,
    count_tokens,
    is_invalid_api_key_error,
    is_model_unavailable_error,
    GEMINI_MODEL,
)
from database import (
    save_session,
    get_all_sessions,
    get_session_by_id,
    delete_session,
    get_user_summary_language,
)
import docx_export
import quota

summaries_bp = Blueprint("summaries_bp", __name__)


def _quota_exceeded_response(exc):
    return jsonify({
        "error": exc.detail["message"],
        "quota_dimension": exc.dimension,
        "resets_in_seconds": exc.detail["resets_in_seconds"],
    }), 429


def _model_unavailable_response(exc):
    # Distinct from the generic 500 below on purpose: this is Google having
    # retired GEMINI_MODEL, identical for every user and not fixable by
    # anything the requester can do — surfacing it separately (status +
    # message) makes it stand out in logs as "update the model constant"
    # rather than blending into ordinary per-request failures.
    print(f"[gemini] GEMINI_MODEL_UNAVAILABLE: configured model {GEMINI_MODEL!r} was rejected by Gemini: {exc}")
    return jsonify({
        "error": "The configured Gemini model is no longer available. This is a server configuration issue, not something you can fix — please contact the site owner.",
    }), 503


def _gemini_rate_limit_response(user_id, request_type, estimated_tokens, exc):
    parsed = quota.parse_gemini_429(exc)
    quota.record_rejection_by_gemini(user_id, request_type, estimated_tokens, parsed)
    return jsonify({
        "error": "Gemini rejected this request for rate limiting.",
        "retry_after_seconds": parsed["retry_after_seconds"],
        "quota_violations": parsed["quota_violations"],
    }), 429


@summaries_bp.route("/api/quota", methods=["GET"])
@login_required
def get_quota():
    current_user = get_current_user()
    return jsonify(quota.compute_quota_status(current_user["id"])), 200


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

        input_tokens = quota.estimate_text_tokens(
            transcript,
            count_tokens_fn=lambda t: count_tokens(t, user_api_key=user_api_key),
        )
        estimated_tokens = input_tokens + quota.OUTPUT_TOKEN_BUFFER
        try:
            quota.check_capacity(current_user["id"], estimated_tokens)
        except quota.QuotaExceeded as exc:
            quota.record_preflight_rejection(current_user["id"], "text", estimated_tokens)
            return _quota_exceeded_response(exc)

        try:
            result, usage_metadata = summarise_transcript(transcript, user_api_key=user_api_key, language=summary_language)
        except Exception as e:
            if is_invalid_api_key_error(e):
                return jsonify({"error": "Your Gemini API key appears to be invalid or expired. Please update it in Settings."}), 401
            if is_model_unavailable_error(e):
                return _model_unavailable_response(e)
            if quota.is_rate_limit_error(e):
                return _gemini_rate_limit_response(current_user["id"], "text", estimated_tokens, e)
            return jsonify({"error": f"Gemini processing failed: {str(e)}"}), 500

        quota.record_success(current_user["id"], "text", estimated_tokens, usage_metadata)
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

            # Duration comes from the file header (mutagen), not a full
            # decode. If the format can't be parsed, fall back to 0 input
            # tokens rather than blocking the request on an estimate we
            # can't produce — the output buffer alone still guards against
            # an empty-capacity edge case.
            duration_seconds = quota.get_media_duration_seconds(tmp_path)
            input_tokens = quota.estimate_audio_tokens(duration_seconds) if duration_seconds is not None else 0
            estimated_tokens = input_tokens + quota.OUTPUT_TOKEN_BUFFER

            try:
                quota.check_capacity(current_user["id"], estimated_tokens)
            except quota.QuotaExceeded as exc:
                quota.record_preflight_rejection(current_user["id"], "audio", estimated_tokens)
                return _quota_exceeded_response(exc)

            try:
                result, usage_metadata = summarise_media(tmp_path, mime_type=mime_type, user_api_key=user_api_key, language=summary_language)
            except Exception as e:
                if is_invalid_api_key_error(e):
                    return jsonify({"error": "Your Gemini API key appears to be invalid or expired. Please update it in Settings."}), 401
                if is_model_unavailable_error(e):
                    return _model_unavailable_response(e)
                if quota.is_rate_limit_error(e):
                    return _gemini_rate_limit_response(current_user["id"], "audio", estimated_tokens, e)
                return jsonify({"error": f"Gemini media processing failed: {str(e)}"}), 500

            quota.record_success(current_user["id"], "audio", estimated_tokens, usage_metadata)
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
        summary=result["overview"],
        decisions=result["decisions_and_takeaways"],
        action_items=result["action_items"],
        transcript=transcript,
        user_id=current_user["id"] if current_user else None,
        detected_type=result["detected_type"],
        key_concepts=result["key_concepts"],
    )

    result["session_id"] = session_id
    result["transcript"] = transcript
    # Mirror the old-shape keys too, so this freshly-generated response has
    # the exact same field set as get_session_by_id returns when this same
    # session is reloaded later — the frontend's render layer only needs one
    # code path regardless of which endpoint the data came from.
    result["summary"] = result["overview"]
    result["decisions"] = result["decisions_and_takeaways"]
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

    # New-format sessions (detected_type set) additionally have a
    # key_concepts list with no equivalent in the old flat shape — include
    # it as its own section rather than silently dropping it from the export.
    key_concepts_section = ""
    if data.get("detected_type") and data.get("key_concepts"):
        concepts_list = "\n".join([f"- {c}" for c in data["key_concepts"]])
        key_concepts_section = f"\n\nKEY CONCEPTS\n{concepts_list}"

    summary_heading = "OVERVIEW" if data.get("detected_type") else "KEY DISCUSSION POINTS"
    decisions_heading = "DECISIONS & TAKEAWAYS" if data.get("detected_type") else "DECISIONS MADE"

    text_content = f"""SUMMARY ANALYSIS\nGenerated on: {data.get('created_at', 'N/A')}\n\n{summary_heading}\n{data.get('summary', 'No summary available.')}{key_concepts_section}\n\n{decisions_heading}\n{decisions_list}\n\nACTION ITEMS\n{actions_list}\n\nFULL TEXT ARCHIVE\n{data.get('transcript', 'No transcript archive available.')}\n"""

    filename = f"summary_session_{session_id}.txt"

    return current_app.response_class(
        text_content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@summaries_bp.route("/sessions/<int:session_id>/export/docx", methods=["GET"])
@login_required
def export_session_docx(session_id):
    current_user = get_current_user()
    data = get_session_by_id(session_id, user_id=current_user["id"])
    if not data:
        return jsonify({"error": "Session not found"}), 404

    buffer = docx_export.build_summary_docx(data, tz_name=request.args.get("tz"))
    download_name = docx_export.sanitized_download_name(data, session_id)

    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=download_name,
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
