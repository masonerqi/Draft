import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import database
import gemini_client


# --- gemini_client: structured output config, not prompt-only JSON --------

class _FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)
        self.usage_metadata = object()


class _FakeModels:
    def __init__(self, payload):
        self.payload = payload
        self.last_call = None

    def generate_content(self, model=None, contents=None, config=None):
        self.last_call = {"model": model, "contents": contents, "config": config}
        return _FakeResponse(self.payload)


class _FakeFile:
    name = "files/abc"
    uri = "files/abc"
    mime_type = "audio/mpeg"
    state = None


class _FakeFilesAPI:
    def __init__(self):
        self.deleted = []

    def upload(self, file=None, config=None):
        return _FakeFile()

    def delete(self, name=None):
        self.deleted.append(name)


class _FakeClient:
    def __init__(self, payload):
        self.models = _FakeModels(payload)
        self.files = _FakeFilesAPI()


def test_detected_type_is_a_constrained_schema_enum():
    schema = gemini_client.TEXT_SUMMARY_SCHEMA["properties"]["detected_type"]
    assert schema["format"] == "enum"
    assert schema["enum"] == gemini_client.DETECTED_TYPES
    assert schema["enum"] == [
        "Academic / Lecture",
        "Business Meeting",
        "Technical Sync",
        "General Discussion",
    ]


def test_summarise_transcript_uses_structured_output_not_prompt_only_json(monkeypatch):
    payload = {
        "detected_type": "Business Meeting",
        "overview": "Overview text.",
        "key_concepts": ["**Concept:** detail"],
        "action_items": ["**Task:** detail"],
        "decisions_and_takeaways": ["**Decision:** detail"],
    }
    fake_client = _FakeClient(payload)
    monkeypatch.setattr(gemini_client, "_build_client", lambda user_api_key=None: fake_client)

    result, usage_metadata = gemini_client.summarise_transcript("hello there", user_api_key="key")

    assert result == payload
    call = fake_client.models.last_call
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema == gemini_client.TEXT_SUMMARY_SCHEMA
    assert "hello there" in call["contents"]


def test_summarise_media_uses_audio_schema_with_transcript_field(monkeypatch, tmp_path):
    payload = {
        "detected_type": "Academic / Lecture",
        "overview": "Lecture overview.",
        "key_concepts": [],
        "action_items": [],
        "decisions_and_takeaways": [],
        "transcript": "raw transcript",
    }
    fake_client = _FakeClient(payload)
    monkeypatch.setattr(gemini_client, "_build_client", lambda user_api_key=None: fake_client)
    monkeypatch.setattr(gemini_client, "_wait_for_file_active", lambda client, uploaded_file: uploaded_file)

    audio_path = tmp_path / "lecture.mp3"
    audio_path.write_bytes(b"not-really-audio")

    result, usage_metadata = gemini_client.summarise_media(str(audio_path), "audio/mpeg", user_api_key="key")

    assert result == payload
    call = fake_client.models.last_call
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema == gemini_client.AUDIO_SUMMARY_SCHEMA
    assert "transcript" in call["config"].response_schema["required"]
    # The remote File API upload is cleaned up regardless of success.
    assert fake_client.files.deleted == ["files/abc"]


def test_language_instruction_prioritizes_explicit_selection_over_matching():
    explicit = gemini_client._text_language_instruction("ms-MY")
    assert "Bahasa Melayu" in explicit

    fallback = gemini_client._text_language_instruction(None)
    assert "same predominant language as the transcript" in fallback


# --- database: adaptive schema round-trip + legacy backward compatibility --

def test_save_and_get_session_round_trips_the_adaptive_schema(db_path, user_id):
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="Overview text.",
        decisions=["**Decision:** detail"],
        action_items=["**Task:** detail"],
        transcript="raw transcript",
        user_id=user_id,
        detected_type="Business Meeting",
        key_concepts=["**Concept:** detail"],
    )

    data = database.get_session_by_id(session_id, user_id=user_id)

    assert data["detected_type"] == "Business Meeting"
    assert data["overview"] == "Overview text."
    assert data["key_concepts"] == ["**Concept:** detail"]
    assert data["decisions_and_takeaways"] == ["**Decision:** detail"]
    assert data["action_items"] == ["**Task:** detail"]
    # Old-shape keys stay populated too (same underlying columns), so any
    # remaining reader of the old field names — e.g. the text export route —
    # keeps working without special-casing.
    assert data["summary"] == "Overview text."
    assert data["decisions"] == ["**Decision:** detail"]


def test_get_session_by_id_is_backward_compatible_with_legacy_rows(db_path, user_id):
    # No detected_type/key_concepts passed — simulates a row saved before
    # the adaptive schema shipped.
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="Old paragraph summary.",
        decisions=["Old decision"],
        action_items=["Old action"],
        transcript="raw transcript",
        user_id=user_id,
    )

    data = database.get_session_by_id(session_id, user_id=user_id)

    assert data["detected_type"] is None
    assert data["overview"] is None
    assert data["key_concepts"] is None
    assert data["decisions_and_takeaways"] is None
    assert data["summary"] == "Old paragraph summary."
    assert data["decisions"] == ["Old decision"]
    assert data["action_items"] == ["Old action"]


# --- routes/summaries.py: full request/response shape ----------------------

def _client(db_path):
    application = app_module.create_app(test_config={"TESTING": True})
    application.config.update(TESTING=True)
    return application.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_summarise_route_returns_and_persists_the_adaptive_schema(db_path, user_id, monkeypatch):
    database.set_user_api_key(user_id, "fake-gemini-key")

    class FakeUsageMetadata:
        total_token_count = 500

    payload = {
        "detected_type": "Technical Sync",
        "overview": "We fixed the deploy pipeline.",
        "key_concepts": ["**CI:** GitHub Actions runs on every push."],
        "action_items": ["**Fix flaky test:** add retry logic."],
        "decisions_and_takeaways": ["**Rollback plan:** revert via tag."],
    }

    def fake_summarise_transcript(text, user_api_key=None, language=None):
        return dict(payload), FakeUsageMetadata()

    monkeypatch.setattr("routes.summaries.count_tokens", lambda text, user_api_key=None: 20)
    monkeypatch.setattr("routes.summaries.summarise_transcript", fake_summarise_transcript)

    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/summarise", data={"transcript": "hello world"})
    assert res.status_code == 200
    body = res.get_json()
    for key, value in payload.items():
        assert body[key] == value
    # Mirrored old-shape keys, so this response has the same field set the
    # session will have when reloaded via GET /sessions/<id> below.
    assert body["summary"] == payload["overview"]
    assert body["decisions"] == payload["decisions_and_takeaways"]

    res2 = client.get(f"/sessions/{body['session_id']}")
    assert res2.status_code == 200
    fetched = res2.get_json()
    assert fetched["detected_type"] == "Technical Sync"
    assert fetched["key_concepts"] == payload["key_concepts"]
    assert fetched["decisions_and_takeaways"] == payload["decisions_and_takeaways"]


def test_export_text_includes_key_concepts_for_adaptive_summaries(db_path, user_id):
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="Overview text.",
        decisions=["**Decision:** detail"],
        action_items=["**Task:** detail"],
        transcript="raw transcript",
        user_id=user_id,
        detected_type="Business Meeting",
        key_concepts=["**Concept:** important detail"],
    )

    client = _client(db_path)
    _login(client, user_id)

    res = client.get(f"/sessions/{session_id}/export/text")
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "KEY CONCEPTS" in text
    assert "**Concept:** important detail" in text


def test_export_text_still_works_for_legacy_summaries(db_path, user_id):
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="Old paragraph summary.",
        decisions=["Old decision"],
        action_items=["Old action"],
        transcript="raw transcript",
        user_id=user_id,
    )

    client = _client(db_path)
    _login(client, user_id)

    res = client.get(f"/sessions/{session_id}/export/text")
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "KEY DISCUSSION POINTS" in text
    assert "KEY CONCEPTS" not in text
