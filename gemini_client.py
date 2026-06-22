import json
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

SUMMARY_PROMPT = """
You are an academic summarisation assistant. Summarise the following transcript
and return ONLY a valid JSON object with these exact keys:
- summary: a concise paragraph summarising the session
- decisions: a list of decisions made
- action_items: a list of tasks or follow-up actions identified

Return ONLY the JSON object. No markdown, no code blocks, no explanation.

Transcript:
\"\"\"{text}\"\"\"
"""

AUDIO_PROMPT = """
You are an academic summarisation assistant. Listen to this lecture recording.
First transcribe it, then return ONLY a valid JSON object with these exact keys:
- transcript: the full transcript of the audio
- summary: a concise paragraph summarising the lecture
- decisions: a list of decisions made (empty list if none apply)
- action_items: a list of tasks or follow-up actions identified (empty list if none apply)

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


def summarise_transcript(text: str) -> dict:
    prompt = SUMMARY_PROMPT.format(text=text)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return _parse_json_response(response.text)


def summarise_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> dict:
    """
    Sends raw audio bytes directly to Gemini for transcription + summarisation
    in a single call. mime_type should match what the browser recorded
    (MediaRecorder default is usually audio/webm).
    """
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[AUDIO_PROMPT, audio_part]
    )
    return _parse_json_response(response.text)