import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import database
import gemini_client
import quota
import summary_jobs


def test_is_model_unavailable_error_detects_a_retired_model():
    exc = Exception(
        "404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model "
        "models/gemini-2.5-flash is no longer available to new users. "
        "Please update your code to use a newer model...', 'status': 'NOT_FOUND'}}"
    )
    assert gemini_client.is_model_unavailable_error(exc) is True


def test_is_model_unavailable_error_ignores_unrelated_errors():
    assert gemini_client.is_model_unavailable_error(ValueError("network timeout")) is False
    # A plain 404 without the "retired model" wording shouldn't match —
    # this detector is specifically for the deprecation shape, not every 404.
    assert gemini_client.is_model_unavailable_error(Exception("404 file not found on disk")) is False


def _client(db_path):
    application = app_module.create_app(test_config={"TESTING": True})
    application.config.update(TESTING=True)
    return application.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _submit_transcript(client, text="hello world"):
    """Posts a transcript and returns the accepted job's id. Every successful
    submission is now a 202 handoff — the summary itself arrives from the
    polling endpoint."""
    res = client.post("/summarise", data={"transcript": text})
    assert res.status_code == 202, res.get_json()
    return res.get_json()["job_id"]


def _poll(client, job_id):
    return client.get(f"/summarise/jobs/{job_id}")


def test_summarise_accepts_the_work_and_reports_the_result_from_the_job(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    class FakeUsageMetadata:
        total_token_count = 777

    def fake_summarise_transcript(text, user_api_key=None, language=None):
        return {
            "detected_type": "General Discussion",
            "overview": "s",
            "key_concepts": [],
            "decisions_and_takeaways": [],
            "action_items": [],
        }, FakeUsageMetadata()

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "summarise_transcript", fake_summarise_transcript)

    client = _client(db_path)
    _login(client, user_id)

    job_id = _submit_transcript(client)
    res = _poll(client, job_id)

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "done"
    # The finished payload keeps the shape the synchronous route returned,
    # including the old-format aliases the render layer reads.
    assert body["result"]["overview"] == "s"
    assert body["result"]["summary"] == "s"
    assert isinstance(body["result"]["session_id"], int)

    # The session is saved by the job, not by the request that queued it.
    saved = database.get_session_by_id(body["result"]["session_id"], user_id=user_id)
    assert saved["detected_type"] == "General Discussion"

    window = database.get_usage_window(user_id, database.hash_api_key("fake-gemini-key"), "1 minute")
    # The estimate (20 + OUTPUT_TOKEN_BUFFER) is discarded once Gemini's own
    # usageMetadata is available — the log reflects the real number.
    assert window["tokens"] == 777
    assert window["requests"] == 1


def test_job_reports_processing_until_the_worker_finishes(db_path, user_id, monkeypatch):
    """Without the inline_jobs fixture nothing runs the job, which is exactly
    the state a client sees while Gemini is still working."""
    database.set_user_api_key(user_id, "fake-gemini-key")
    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "_dispatch", lambda fn, *args: None)

    client = _client(db_path)
    _login(client, user_id)

    job_id = _submit_transcript(client)
    res = _poll(client, job_id)

    assert res.status_code == 200
    assert res.get_json() == {"status": "queued"}


def test_summarise_job_returns_503_when_the_configured_model_is_retired(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    def raise_model_retired(*args, **kwargs):
        raise Exception(
            "404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model "
            "models/gemini-3.6-flash is no longer available to new users.', "
            "'status': 'NOT_FOUND'}}"
        )

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "summarise_transcript", raise_model_retired)

    client = _client(db_path)
    _login(client, user_id)

    res = _poll(client, _submit_transcript(client))

    assert res.status_code == 503
    assert "server configuration" in res.get_json()["error"].lower()


def test_preflight_rejects_before_calling_gemini_when_rpm_exhausted(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    key_hash = database.hash_api_key("fake-gemini-key")
    limits = database.get_quota_limits(user_id, key_hash, quota.get_default_limits())
    allowed = int(limits["rpm_limit"] * quota.SAFETY_MARGIN)
    for _ in range(allowed):
        database.log_usage(user_id, key_hash, "text", "success", estimated_tokens=50, actual_tokens=50)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Gemini must not be called once local RPM capacity is exhausted")

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "summarise_transcript", fail_if_called)

    client = _client(db_path)
    _login(client, user_id)

    # The quota check stays inline: a request that can't succeed is still
    # refused by the POST itself, not deferred into a job the client would
    # have to poll to discover was doomed.
    res = client.post("/summarise", data={"transcript": "hello world"})

    assert res.status_code == 429
    body = res.get_json()
    assert body["quota_dimension"] == "rpm"
    assert isinstance(body["resets_in_seconds"], int)
    assert "job_id" not in body


def test_gemini_429_passthrough_starts_a_cooldown(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    class FakeGeminiClientError(Exception):
        def __init__(self):
            self.code = 429
            self.status = "RESOURCE_EXHAUSTED"
            self.details = {
                "error": {
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "5s"},
                    ]
                }
            }
            super().__init__("429")

    def raise_429(*args, **kwargs):
        raise FakeGeminiClientError()

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "summarise_transcript", raise_429)

    client = _client(db_path)
    _login(client, user_id)

    res = _poll(client, _submit_transcript(client))

    assert res.status_code == 429
    body = res.get_json()
    assert body["retry_after_seconds"] == 5

    limits = database.get_quota_limits(user_id, database.hash_api_key("fake-gemini-key"), quota.get_default_limits())
    assert limits["cooldown_until"] is not None


def test_a_job_is_only_readable_by_the_account_that_created_it(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")
    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(
        summary_jobs, "summarise_transcript",
        lambda *a, **k: ({"detected_type": "General Discussion", "overview": "s",
                          "key_concepts": [], "decisions_and_takeaways": [], "action_items": []}, None),
    )

    client = _client(db_path)
    _login(client, user_id)
    job_id = _submit_transcript(client)

    other_user_id = database.create_user("someone-else@example.com", "password123")
    other_client = _client(db_path)
    _login(other_client, other_user_id)

    assert _poll(other_client, job_id).status_code == 404
    # ...and the owner still sees it, so the 404 is about ownership rather
    # than the job having gone missing.
    assert _poll(client, job_id).status_code == 200


def test_a_job_whose_worker_never_finished_is_reported_as_timed_out(db_path, user_id, monkeypatch):
    database.set_user_api_key(user_id, "fake-gemini-key")
    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr(summary_jobs, "_dispatch", lambda fn, *args: None)

    client = _client(db_path)
    _login(client, user_id)
    job_id = _submit_transcript(client)

    # Nothing interrupts a running job; this covers the case where the
    # instance running it went away and no worker will ever come back to it.
    monkeypatch.setattr(summary_jobs, "JOB_STALE_AFTER_SECONDS", -1)

    res = _poll(client, job_id)
    assert res.status_code == 504
    assert res.get_json()["status"] == "failed"

    # The job is closed out, so a client that polls again gets the same
    # answer rather than falling back to "still processing".
    assert _poll(client, job_id).status_code == 504


def test_media_upload_is_queued_and_its_temp_file_is_cleaned_up(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    seen = {}

    def fake_summarise_media(file_path, mime_type=None, user_api_key=None, language=None):
        seen["path"] = file_path
        seen["mime_type"] = mime_type
        # The file has to still be there while Gemini is being called.
        assert os.path.exists(file_path)
        return {
            "detected_type": "Business Meeting",
            "overview": "o",
            "key_concepts": [],
            "decisions_and_takeaways": [],
            "action_items": [],
            "transcript": "the spoken words",
        }, None

    monkeypatch.setattr(summary_jobs, "summarise_media", fake_summarise_media)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post(
        "/summarise",
        data={"media": (io.BytesIO(b"not really audio, but not empty"), "meeting.m4a")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 202
    job_id = res.get_json()["job_id"]

    body = _poll(client, job_id).get_json()
    assert body["status"] == "done"
    assert body["result"]["transcript"] == "the spoken words"
    assert seen["mime_type"] == "audio/mp4"
    # The upload is the job's to delete once Gemini no longer needs it.
    assert not os.path.exists(seen["path"])


def test_empty_media_upload_is_rejected_without_queueing_a_job(db_path, user_id, monkeypatch, inline_jobs):
    database.set_user_api_key(user_id, "fake-gemini-key")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("An empty upload must never reach Gemini")

    monkeypatch.setattr(summary_jobs, "summarise_media", fail_if_called)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post(
        "/summarise",
        data={"media": (io.BytesIO(b""), "meeting.m4a")},
        content_type="multipart/form-data",
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == "Empty file received"
