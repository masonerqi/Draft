import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import database
import gemini_client
import quota


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


def test_summarise_route_returns_503_when_the_configured_model_is_retired(db_path, user_id, monkeypatch):
    database.set_user_api_key(user_id, "fake-gemini-key")

    def raise_model_retired(*args, **kwargs):
        raise Exception(
            "404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model "
            "models/gemini-3.6-flash is no longer available to new users.', "
            "'status': 'NOT_FOUND'}}"
        )

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr("routes.summaries.summarise_transcript", raise_model_retired)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/summarise", data={"transcript": "hello world"})

    assert res.status_code == 503
    assert "server configuration" in res.get_json()["error"].lower()


def _client(db_path):
    application = app_module.create_app(test_config={"TESTING": True})
    application.config.update(TESTING=True)
    return application.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_preflight_rejects_before_calling_gemini_when_rpm_exhausted(db_path, user_id, monkeypatch):
    database.set_user_api_key(user_id, "fake-gemini-key")

    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    allowed = int(limits["rpm_limit"] * quota.SAFETY_MARGIN)
    for _ in range(allowed):
        database.log_usage(user_id, "text", "success", estimated_tokens=50, actual_tokens=50)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Gemini must not be called once local RPM capacity is exhausted")

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr("routes.summaries.summarise_transcript", fail_if_called)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/summarise", data={"transcript": "hello world"})

    assert res.status_code == 429
    body = res.get_json()
    assert body["quota_dimension"] == "rpm"
    assert isinstance(body["resets_in_seconds"], int)


def test_successful_summarise_reconciles_with_real_usage_metadata(db_path, user_id, monkeypatch):
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
    monkeypatch.setattr("routes.summaries.summarise_transcript", fake_summarise_transcript)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/summarise", data={"transcript": "hello world"})

    assert res.status_code == 200
    window = database.get_usage_window(user_id, "1 minute")
    # The estimate (20 + OUTPUT_TOKEN_BUFFER) is discarded once Gemini's own
    # usageMetadata is available — the log reflects the real number.
    assert window["tokens"] == 777
    assert window["requests"] == 1


def test_gemini_429_passthrough_starts_a_cooldown(db_path, user_id, monkeypatch):
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
    monkeypatch.setattr("routes.summaries.summarise_transcript", raise_429)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/summarise", data={"transcript": "hello world"})

    assert res.status_code == 429
    body = res.get_json()
    assert body["retry_after_seconds"] == 5

    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    assert limits["cooldown_until"] is not None
