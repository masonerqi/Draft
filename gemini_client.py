import json
import time
from google import genai
from google.genai import types

# Wall-clock budget for an uploaded file to leave the Gemini File API's
# PROCESSING state and become ACTIVE. Video can take 10-30s+; this leaves
# generous headroom above that.
FILE_PROCESSING_TIMEOUT_SECONDS = 180
FILE_PROCESSING_POLL_INTERVAL_SECONDS = 3

# Do not configure Gemini globally. Each request must pass a user-specific
# API key into the client builder so keys are isolated per authenticated user.

def _resolve_api_key(user_api_key=None):
    if user_api_key:
        return user_api_key
    raise ValueError("No Gemini API key configured. Please provide a user-specific Gemini API key.")


def _build_client(user_api_key=None):
    api_key = _resolve_api_key(user_api_key)
    return genai.Client(api_key=api_key)


def is_invalid_api_key_error(exc: Exception) -> bool:
    """Best-effort detection of an invalid/expired/unauthorized Gemini API
    key from whatever exception the SDK raised, so the route can tell the
    user to fix their key in Settings instead of showing a generic error."""
    message = str(exc).lower()
    return any(token in message for token in (
        "api key not valid",
        "api_key_invalid",
        "invalid api key",
        "permission_denied",
        "unauthenticated",
        "401",
        "403",
    ))

# Maps the BCP-47 codes offered by the recorder's language picker
# (templates/note.html#languageSelect) to a descriptive name for the prompt.
LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "ms-MY": "Bahasa Melayu",
    "zh-CN": "Mandarin Chinese (Simplified)",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "es-ES": "Spanish",
    "fr-FR": "French",
    "de-DE": "German",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
}


def _language_name(language: str | None) -> str | None:
    if not language:
        return None
    return LANGUAGE_NAMES.get(language, language)


SUMMARY_PROMPT = """
You are an academic summarisation assistant. Summarise the following transcript
and return ONLY a valid JSON object with these exact keys:
- summary: a concise paragraph summarising the session
- decisions: a list of decisions made
- action_items: a list of tasks or follow-up actions identified

{language_instruction}

Return ONLY the JSON object. No markdown, no code blocks, no explanation.

Transcript:
\"\"\"{text}\"\"\"
"""

AUDIO_PROMPT = """
You are an academic summarisation assistant. Listen to this lecture recording.
First transcribe it, then return ONLY a valid JSON object with these exact keys:
- transcript: the full transcript of the audio, in its original spoken language
- summary: a concise paragraph summarising the lecture
- decisions: a list of decisions made (empty list if none apply)
- action_items: a list of tasks or follow-up actions identified (empty list if none apply)

{language_instruction}

Return ONLY the JSON object. No markdown, no code blocks, no explanation.
"""


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    # Strip markdown code blocks if Gemini wraps the response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def summarise_transcript(text: str, user_api_key: str | None = None, language: str | None = None) -> dict:
    name = _language_name(language)
    language_instruction = (
        f"Write the summary, decisions, and action_items in {name}."
        if name else
        "Write the summary, decisions, and action_items in the same language as the transcript."
    )
    prompt = SUMMARY_PROMPT.format(text=text, language_instruction=language_instruction)
    client = _build_client(user_api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return _parse_json_response(response.text)


def _wait_for_file_active(client, uploaded_file):
    """Polls the Gemini File API until the upload leaves PROCESSING. Files
    (especially video) aren't immediately usable in generate_content."""
    elapsed = 0
    current = uploaded_file
    while current.state == types.FileState.PROCESSING:
        if elapsed >= FILE_PROCESSING_TIMEOUT_SECONDS:
            raise TimeoutError("Gemini took too long to process the uploaded file.")
        time.sleep(FILE_PROCESSING_POLL_INTERVAL_SECONDS)
        elapsed += FILE_PROCESSING_POLL_INTERVAL_SECONDS
        current = client.files.get(name=uploaded_file.name)
    if current.state == types.FileState.FAILED:
        raise RuntimeError("Gemini failed to process the uploaded file.")
    return current


def summarise_media(file_path: str, mime_type: str, user_api_key: str | None = None, language: str | None = None) -> dict:
    """
    Uploads a local audio/video file to the Gemini File API and summarises
    it, for files too large to inline directly into a generate_content
    request. The caller owns the local file's lifecycle (save/cleanup); this
    function owns the remote Gemini file's lifecycle (upload/delete).
    """
    name = _language_name(language)
    language_instruction = (
        f"Write the summary, decisions, and action_items in {name}. "
        "Keep the transcript field in its original spoken language, unmodified."
        if name else
        "Write the summary, decisions, and action_items in the same language as the audio."
    )

    client = _build_client(user_api_key)

    uploaded_file = None
    try:
        uploaded_file = client.files.upload(
            file=file_path,
            config=types.UploadFileConfig(mime_type=mime_type),
        )
        uploaded_file = _wait_for_file_active(client, uploaded_file)

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                AUDIO_PROMPT.format(language_instruction=language_instruction),
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
            ]
        )
        return _parse_json_response(response.text)
    finally:
        if uploaded_file and getattr(uploaded_file, "name", None):
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass