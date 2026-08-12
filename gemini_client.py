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


def is_model_unavailable_error(exc: Exception) -> bool:
    """Best-effort detection of Google retiring GEMINI_MODEL out from under
    us — surfaced as a 404 NOT_FOUND with wording like "no longer available
    to new users" rather than the usual quota/auth-shaped error. Every user
    would otherwise see this identically, so it's a config problem (this
    constant needs updating to a current model), not a per-user one — the
    route should report it distinctly instead of folding it into the
    generic "processing failed" bucket, so it's immediately recognizable in
    logs rather than looking like an ordinary transient failure."""
    message = str(exc).lower()
    if "404" not in message and "not_found" not in message:
        return False
    return any(token in message for token in (
        "no longer available",
        "not found for api version",
        "is not found",
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


# The four buckets `detected_type` is constrained to via the response
# schema's enum — classification is content-driven (vocabulary, subject
# matter), not based on input_type (text vs audio).
DETECTED_TYPES = [
    "Academic / Lecture",
    "Business Meeting",
    "Technical Sync",
    "General Discussion",
]

_BULLET_ARRAY_SCHEMA = {"type": "ARRAY", "items": {"type": "STRING"}}

# Shared by both entry points; summarise_media's schema adds `transcript`
# on top of this, since only the audio path needs Gemini to produce one.
_SUMMARY_SCHEMA_PROPERTIES = {
    "detected_type": {"type": "STRING", "format": "enum", "enum": DETECTED_TYPES},
    "overview": {"type": "STRING"},
    "key_concepts": _BULLET_ARRAY_SCHEMA,
    "action_items": _BULLET_ARRAY_SCHEMA,
    "decisions_and_takeaways": _BULLET_ARRAY_SCHEMA,
}
_SUMMARY_SCHEMA_REQUIRED = ["detected_type", "overview", "key_concepts", "action_items", "decisions_and_takeaways"]

TEXT_SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": _SUMMARY_SCHEMA_PROPERTIES,
    "required": _SUMMARY_SCHEMA_REQUIRED,
}

AUDIO_SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **_SUMMARY_SCHEMA_PROPERTIES,
        "transcript": {"type": "STRING"},
    },
    "required": _SUMMARY_SCHEMA_REQUIRED + ["transcript"],
}

# Formatting/classification rules live in system_instruction rather than the
# prompt text itself, and the structural shape is enforced separately via
# response_mime_type="application/json" + response_schema — constrained at
# decode time, not just requested. {language_instruction} is filled in per
# request from the user's Settings > Preferences "Summary language" choice.
SYSTEM_INSTRUCTION_TEMPLATE = """You are an adaptive meeting/lecture summarisation assistant.

Classify the content into exactly one detected_type based on its subject matter and vocabulary:
- "Academic / Lecture": syllabus items, formulas, professors, exams, coursework.
- "Business Meeting": KPIs, administrative decisions, HR matters, project status updates.
- "Technical Sync": code, system architecture, hardware, bug fixes, deployments.
- "General Discussion": casual syncs or brainstorms without a strict agenda.

Formatting rules for every entry in key_concepts, action_items, and decisions_and_takeaways:
- Format each entry as "**Label:** explanation" — a short bolded label, a colon, then the detail.
- Do not prefix entries with a bullet character (e.g. "- " or "• "). The UI supplies its own bullet marker; a literal one here would double up on screen.
- Never use LaTeX syntax — no "$...$" delimiters, no backslash commands. Write plain notation instead, e.g. "O(n log n)", not "$O(n \\log n)$".

Content rules:
- State facts, decisions, and principles directly. Never use meta-commentary like "The speaker discussed..." or "In this meeting...".
- If a section has nothing to report, return an empty array [] for it. This applies equally to key_concepts, action_items, and decisions_and_takeaways — never write filler text like "No action items." inside an array.
- overview is a 2-3 sentence executive summary of the whole session.

{language_instruction}
"""


def _text_language_instruction(language: str | None) -> str:
    name = _language_name(language)
    if name:
        return f"Write the overview and every bullet point in {name}, regardless of the transcript's source language."
    return "Write the overview and every bullet point in the same predominant language as the transcript."


def _audio_language_instruction(language: str | None) -> str:
    name = _language_name(language)
    target = (
        f"Write the overview and every bullet point in {name}, regardless of the audio's spoken language."
        if name else
        "Write the overview and every bullet point in the same predominant language as the audio."
    )
    return f"{target} Keep the transcript field in its original spoken language, unmodified."


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    # Strip markdown code blocks if Gemini wraps the response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


GEMINI_MODEL = "gemini-3.6-flash"


def count_tokens(text: str, user_api_key: str | None = None) -> int:
    """Calls Gemini's countTokens endpoint for an exact pre-flight estimate.
    This call is free and quota'd separately from generateContent (3000 RPM),
    so it's safe to call on every request."""
    client = _build_client(user_api_key)
    response = client.models.count_tokens(model=GEMINI_MODEL, contents=text)
    return response.total_tokens


def summarise_transcript(text: str, user_api_key: str | None = None, language: str | None = None) -> tuple[dict, object]:
    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
        language_instruction=_text_language_instruction(language)
    )
    client = _build_client(user_api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f'Summarise the following transcript.\n\nTranscript:\n"""\n{text}\n"""',
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=TEXT_SUMMARY_SCHEMA,
        ),
    )
    return _parse_json_response(response.text), response.usage_metadata


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


def summarise_media(file_path: str, mime_type: str, user_api_key: str | None = None, language: str | None = None) -> tuple[dict, object]:
    """
    Uploads a local audio/video file to the Gemini File API and summarises
    it, for files too large to inline directly into a generate_content
    request. The caller owns the local file's lifecycle (save/cleanup); this
    function owns the remote Gemini file's lifecycle (upload/delete).
    """
    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
        language_instruction=_audio_language_instruction(language)
    ) + "\n\nAlso transcribe the audio in full into the transcript field, in its original spoken language, unmodified."

    client = _build_client(user_api_key)

    uploaded_file = None
    try:
        uploaded_file = client.files.upload(
            file=file_path,
            config=types.UploadFileConfig(mime_type=mime_type),
        )
        uploaded_file = _wait_for_file_active(client, uploaded_file)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                "Listen to this recording and summarise it per the system instructions.",
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AUDIO_SUMMARY_SCHEMA,
            ),
        )
        return _parse_json_response(response.text), response.usage_metadata
    finally:
        if uploaded_file and getattr(uploaded_file, "name", None):
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass