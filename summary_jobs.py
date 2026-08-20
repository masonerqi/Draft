"""Runs the Gemini half of a /summarise request off the HTTP request thread.

Summarising a 30-minute recording is a multi-minute operation: the file is
uploaded to the Gemini File API, waits out its PROCESSING state, and only
then is transcribed and summarised. Holding an HTTP request open for that
long does not work on the deployment target — Vercel caps how long a single
request may run, and the connection is cut well before Gemini answers. The
browser saw that cut as a failed fetch and reported "confirm your Flask
container or server is running", which was never true: the server was fine,
the request had simply outlived its allowance.

So /summarise now does only the fast part inline (save the upload, estimate
cost, check quota) and hands the slow part to this module. The work runs on
a small thread pool; progress is written to the `summary_jobs` table, which
the client polls. Because that state lives in Postgres rather than in
process memory, a poll that lands on a different app instance still sees it.

Failures are recorded as the exact (status, body) pair the old synchronous
route would have returned, so the client keeps one error-handling path.
"""

import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import database
import quota
from gemini_client import (
    GEMINI_MODEL,
    is_invalid_api_key_error,
    is_model_unavailable_error,
    summarise_media,
    summarise_transcript,
)

# Each gunicorn worker process gets its own pool of this size. Kept small on
# purpose: these threads are waiting on Gemini, and every one in flight also
# holds a temp file on disk, so an unbounded thread-per-upload would let a
# burst of uploads exhaust either. Jobs beyond this queue up and stay
# 'queued' until a slot frees.
MAX_CONCURRENT_JOBS = 4

# How long a job may stay unfinished before the polling endpoint declares it
# dead. This is not a limit on Gemini — nothing here interrupts a running
# job — it is the answer to "the instance running this job was recycled and
# nothing will ever finish it". Comfortably above the worst realistic case:
# the File API wait alone is capped at 180s (gemini_client), and generating
# a full transcript of a long recording on top of that runs to minutes.
JOB_STALE_AFTER_SECONDS = 30 * 60

_executor = None
_executor_pid = None
_executor_lock = threading.Lock()


def _get_executor():
    """Builds the pool lazily, per process. gunicorn runs with --preload, so
    module import happens in the master before the fork; threads do not
    survive a fork, so a pool created at import time would be inherited
    dead. The pid check makes each worker create its own on first use."""
    global _executor, _executor_pid
    pid = os.getpid()
    if _executor is None or _executor_pid != pid:
        with _executor_lock:
            if _executor is None or _executor_pid != pid:
                _executor = ThreadPoolExecutor(
                    max_workers=MAX_CONCURRENT_JOBS,
                    thread_name_prefix="summary-job",
                )
                _executor_pid = pid
    return _executor


def _dispatch(fn, *args):
    """Single seam between enqueueing and actually running a job. Tests
    replace this with a direct call so a job's outcome is observable within
    the same request, instead of racing a background thread."""
    _get_executor().submit(fn, *args)


def enqueue_transcript_job(user_id, api_key, transcript, language, estimated_tokens):
    """Queues a summarise of pasted/dictated text. Returns the job id."""
    job_id = database.create_summary_job(_new_job_id(), user_id, "transcript", filename="paste")
    try:
        _dispatch(_run_transcript_job, job_id, user_id, api_key, transcript, language, estimated_tokens)
    except Exception:
        _fail_undispatched(job_id)
    return job_id


def enqueue_media_job(user_id, api_key, tmp_path, mime_type, filename, language, estimated_tokens):
    """Queues a summarise of an uploaded recording. Ownership of `tmp_path`
    transfers to this module the moment the job row exists: the job deletes
    the file when it finishes, however it finishes."""
    job_id = database.create_summary_job(_new_job_id(), user_id, "media", filename=filename)
    try:
        _dispatch(
            _run_media_job, job_id, user_id, api_key, tmp_path, mime_type, filename,
            language, estimated_tokens,
        )
    except Exception:
        _fail_undispatched(job_id)
        _remove_temp_file(tmp_path)
    return job_id


def _fail_undispatched(job_id):
    """The job row exists but nothing will ever run it (the pool refused the
    submission). Close it out now so the first poll returns a real error
    rather than the client waiting out JOB_STALE_AFTER_SECONDS."""
    print(f"[summary_jobs] could not dispatch job {job_id}:\n{traceback.format_exc()}")
    try:
        database.fail_summary_job(
            job_id, "The server couldn't start processing this. Please try again.", 503
        )
    except Exception:
        pass


def _remove_temp_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _new_job_id():
    # Random rather than sequential: a job id is the handle the client polls
    # with, and an unguessable one means the owner check in get_summary_job
    # is not the only thing standing between accounts.
    return os.urandom(16).hex()


def _run_transcript_job(job_id, user_id, api_key, transcript, language, estimated_tokens):
    def call_gemini():
        return summarise_transcript(transcript, user_api_key=api_key, language=language)

    _run_job(job_id, user_id, api_key, "text", estimated_tokens, call_gemini,
             input_type="transcript", filename="paste", transcript=transcript)


def _run_media_job(job_id, user_id, api_key, tmp_path, mime_type, filename, language, estimated_tokens):
    def call_gemini():
        return summarise_media(tmp_path, mime_type=mime_type, user_api_key=api_key, language=language)

    try:
        _run_job(job_id, user_id, api_key, "audio", estimated_tokens, call_gemini,
                 input_type="media", filename=filename, transcript=None)
    finally:
        # The upload is only needed for the Gemini call; drop it as soon as
        # that is over, since a 30-minute recording is not a small file to
        # leave sitting on an instance's disk.
        _remove_temp_file(tmp_path)


def _run_job(job_id, user_id, api_key, request_type, estimated_tokens, call_gemini,
             input_type, filename, transcript):
    """Shared body for both input kinds: call Gemini, reconcile quota, save
    the session, and record the outcome on the job row. Nothing raises out of
    here — an unhandled exception in a pool thread would leave the job stuck
    'processing' with no way for the client to learn why."""
    try:
        database.mark_summary_job_processing(job_id)

        try:
            result, usage_metadata = call_gemini()
        except Exception as exc:
            status, message, detail = _classify_gemini_failure(
                exc, user_id, api_key, request_type, estimated_tokens
            )
            database.fail_summary_job(job_id, message, status, detail)
            return

        quota.record_success(user_id, api_key, request_type, estimated_tokens, usage_metadata)

        # For media, Gemini returns the transcript inside the result; for
        # text, the transcript is what the user gave us.
        saved_transcript = result.get("transcript") if input_type == "media" else transcript

        session_id = database.save_session(
            input_type=input_type,
            filename=filename,
            summary=result["overview"],
            decisions=result["decisions_and_takeaways"],
            action_items=result["action_items"],
            transcript=saved_transcript,
            user_id=user_id,
            detected_type=result["detected_type"],
            key_concepts=result["key_concepts"],
        )

        result["session_id"] = session_id
        result["transcript"] = saved_transcript
        # Mirror the old-shape keys too, so this freshly-generated payload has
        # the exact same field set as get_session_by_id returns when this same
        # session is reloaded later — the frontend's render layer only needs
        # one code path regardless of which endpoint the data came from.
        result["summary"] = result["overview"]
        result["decisions"] = result["decisions_and_takeaways"]

        database.complete_summary_job(job_id, result, session_id)
    except Exception:
        # Anything not already handled above (a malformed Gemini payload
        # missing a key, the database being unreachable) still has to reach
        # the user as a finished, failed job.
        print(f"[summary_jobs] job {job_id} failed unexpectedly:\n{traceback.format_exc()}")
        try:
            database.fail_summary_job(job_id, "Summarising failed unexpectedly. Please try again.", 500)
        except Exception:
            pass


def _classify_gemini_failure(exc, user_id, api_key, request_type, estimated_tokens):
    """Maps a Gemini exception to the (status, message, detail) the
    synchronous route used to return for it, and performs the quota
    bookkeeping a real 429 requires."""
    if is_invalid_api_key_error(exc):
        return 401, "Your Gemini API key appears to be invalid or expired. Please update it in Settings.", None

    if is_model_unavailable_error(exc):
        # Distinct from the generic 500 on purpose: this is Google having
        # retired GEMINI_MODEL, identical for every user and not fixable by
        # anything the requester can do — surfacing it separately (status +
        # message) makes it stand out in logs as "update the model constant"
        # rather than blending into ordinary per-request failures.
        print(f"[gemini] GEMINI_MODEL_UNAVAILABLE: configured model {GEMINI_MODEL!r} was rejected by Gemini: {exc}")
        return 503, (
            "The configured Gemini model is no longer available. This is a server "
            "configuration issue, not something you can fix — please contact the site owner."
        ), None

    if quota.is_rate_limit_error(exc):
        parsed = quota.parse_gemini_429(exc)
        quota.record_rejection_by_gemini(user_id, api_key, request_type, estimated_tokens, parsed)
        return 429, "Gemini rejected this request for rate limiting.", {
            "retry_after_seconds": parsed["retry_after_seconds"],
            "quota_violations": parsed["quota_violations"],
        }

    label = "Gemini media processing failed" if request_type == "audio" else "Gemini processing failed"
    return 500, f"{label}: {exc}", None
