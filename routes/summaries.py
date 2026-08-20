import mimetypes
import os
import tempfile

from flask import Blueprint, request, jsonify, current_app, send_file
from routes.utils import get_current_user, login_required
from gemini_client import count_tokens
from database import (
    get_all_sessions,
    get_session_by_id,
    delete_session,
    get_summary_job,
    fail_summary_job,
    get_user_summary_language,
    SUMMARY_JOB_PENDING_STATUSES,
)
import docx_export
import quota
import summary_jobs

summaries_bp = Blueprint("summaries_bp", __name__)


def _quota_exceeded_response(exc):
    return jsonify({
        "error": exc.detail["message"],
        "quota_dimension": exc.dimension,
        "resets_in_seconds": exc.detail["resets_in_seconds"],
    }), 429


@summaries_bp.route("/api/quota", methods=["GET"])
@login_required
def get_quota():
    current_user = get_current_user()
    from database import get_user_api_key

    user_api_key = get_user_api_key(current_user["id"])
    return jsonify(quota.compute_quota_status(current_user["id"], user_api_key)), 200


@summaries_bp.route("/summarise", methods=["POST"])
@login_required
def summarise():
    """Accepts a transcript or a recording and returns 202 with a job id.

    Everything cheap and immediate happens here — reading the upload,
    estimating its token cost, and the pre-flight quota check — so a request
    that can't succeed is still refused inline with the same status code it
    always used. The Gemini call itself is queued (see summary_jobs) and its
    outcome is collected from GET /summarise/jobs/<job_id>, because a long
    recording takes minutes to summarise and the platform will not hold a
    request open that long.
    """
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
            quota.check_capacity(current_user["id"], user_api_key, estimated_tokens)
        except quota.QuotaExceeded as exc:
            quota.record_preflight_rejection(current_user["id"], user_api_key, "text", estimated_tokens)
            return _quota_exceeded_response(exc)

        job_id = summary_jobs.enqueue_transcript_job(
            current_user["id"], user_api_key, transcript, summary_language, estimated_tokens
        )
        return _accepted_response(job_id)

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
        # The upload is written to disk here and handed to the job, which
        # owns it from the moment it has a job id — the job deletes it once
        # Gemini is done with it. Until then this handler is responsible for
        # removing it, which is what the `finally` below is guarding.
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            media_file.save(tmp)
            tmp_path = tmp.name

        job_id = None
        try:
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
                quota.check_capacity(current_user["id"], user_api_key, estimated_tokens)
            except quota.QuotaExceeded as exc:
                quota.record_preflight_rejection(current_user["id"], user_api_key, "audio", estimated_tokens)
                return _quota_exceeded_response(exc)

            job_id = summary_jobs.enqueue_media_job(
                current_user["id"], user_api_key, tmp_path, mime_type, filename,
                summary_language, estimated_tokens,
            )
        finally:
            # Reached on the early returns above and on any unexpected
            # failure as well as on success — but the file must only be
            # removed while this handler still owns it.
            if job_id is None:
                _discard_upload(tmp_path)

        return _accepted_response(job_id)

    else:
        return jsonify({"error": "No transcript or media file provided"}), 400


def _accepted_response(job_id):
    # 202, not 200: the work has been accepted, not completed. The client
    # polls /summarise/jobs/<job_id> from here.
    return jsonify({"job_id": job_id, "status": "queued"}), 202


def _discard_upload(tmp_path):
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            current_app.logger.warning("Could not remove temp upload %s", tmp_path)


@summaries_bp.route("/summarise/jobs/<job_id>", methods=["GET"])
@login_required
def summarise_job(job_id):
    """Reports on a queued summarise.

    While the job is unfinished this is a 200 with status "queued"/
    "processing". On success it is a 200 carrying the same payload the
    synchronous route used to return, under "result". On failure it replays
    the status code and body that route would have returned — so an invalid
    key is still a 401 and a Gemini rate limit is still a 429 with its
    retry_after_seconds — and the client needs only one error path.
    """
    current_user = get_current_user()
    job = get_summary_job(job_id, user_id=current_user["id"])
    if not job:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] in SUMMARY_JOB_PENDING_STATUSES:
        age = job["age_seconds"]
        if age is not None and age > summary_jobs.JOB_STALE_AFTER_SECONDS:
            # Nothing is going to finish this one: the instance that was
            # running it is gone (a redeploy, a recycled container). Close it
            # out so the client stops polling and gets a real explanation
            # instead of an indefinite spinner.
            message = "This recording took too long to process and was abandoned. Please try again."
            fail_summary_job(job_id, message, 504)
            return jsonify({"status": "failed", "error": message}), 504
        return jsonify({"status": job["status"]}), 200

    if job["status"] == "failed":
        body = {"status": "failed", "error": job["error_message"] or "Summarising failed."}
        body.update(job["error_detail"] or {})
        return jsonify(body), job["error_status"] or 500

    return jsonify({"status": "done", "result": job["result"]}), 200


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
