import io
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from docxtpl import DocxTemplate, RichText

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "docx", "summary_export_template.docx")

# Matches the "**Label:** explanation" convention the Gemini system prompt
# enforces for key_concepts/decisions_and_takeaways/action_items entries
# (see SYSTEM_INSTRUCTION_TEMPLATE in gemini_client.py). Older, pre-adaptive
# rows store plain sentences with no such label, which is what the
# fallback_label branch below is for.
# The colon lives inside the bold markers per the prompt's own convention
# ("**Label:** explanation"), so it's stripped from the label capture here —
# the template already supplies its own ": " separator between label and
# text, and leaving the colon in would double it up ("Label:: text").
_BOLD_LABEL_RE = re.compile(r"^\*\*(.+?):?\*\*:?\s*(.*)$", re.DOTALL)


def _parse_bullet_item(entry, fallback_label="Note"):
    # The template's "{{ item.label }}: {{ item.text }}" separator is
    # literal XML, not a placeholder, so an empty label would render as a
    # stray leading ": ". Legacy entries get a generic label instead so the
    # bullet still reads naturally.
    match = _BOLD_LABEL_RE.match(entry.strip())
    if match:
        return {"label": match.group(1), "text": match.group(2)}
    return {"label": fallback_label, "text": entry}


def _format_imported_at(created_at_iso, tz_name=None):
    if not created_at_iso:
        return ""
    try:
        dt = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return created_at_iso
    # Rows written before database.py started using datetime.now(timezone.utc)
    # have no offset at all — treat those as UTC too, same as the frontend's
    # parseUtcTimestamp() does, rather than leaving them un-convertible.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if tz_name:
        try:
            dt = dt.astimezone(ZoneInfo(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}, {dt.hour % 12 or 12}:{dt.strftime('%M')} {dt.strftime('%p')}"


def _build_transcript_richtext(transcript):
    rt = RichText()
    lines = transcript.split("\n")
    for i, line in enumerate(lines):
        if i > 0:
            rt.xml += "<w:br/>"
        rt.add(line)
    return rt


def build_summary_docx(data, tz_name=None):
    """Renders templates/docx/summary_export_template.docx for one session
    record (the dict shape returned by database.get_session_by_id) into an
    in-memory .docx buffer. Never touches disk. `tz_name` is an IANA zone
    (e.g. "Asia/Kuala_Lumpur") the caller's browser resolved for itself —
    the server has no other way to know the viewer's local timezone, so the
    "Imported File" timestamp falls back to UTC when it's omitted."""
    detected_type = data.get("detected_type")

    if detected_type:
        key_concepts = [_parse_bullet_item(x) for x in (data.get("key_concepts") or [])]
        decisions_source = data.get("decisions_and_takeaways") or []
        action_items_source = data.get("action_items") or []
    else:
        # Old-format rows have no key_concepts equivalent; decisions/action
        # items live in the flat fields instead, as plain unlabeled strings.
        key_concepts = []
        decisions_source = data.get("decisions") or []
        action_items_source = data.get("action_items") or []

    decisions_and_takeaways = [_parse_bullet_item(x) for x in decisions_source]
    action_items = [_parse_bullet_item(x) for x in action_items_source]

    transcript = data.get("transcript")
    raw_transcript = _build_transcript_richtext(transcript) if transcript else None

    context = {
        "imported_at": _format_imported_at(data.get("created_at"), tz_name=tz_name),
        "detected_type": detected_type,
        "detected_type_upper": detected_type.upper() if detected_type else "",
        "overview": data.get("overview") if detected_type else None,
        "key_concepts": key_concepts,
        "decisions_and_takeaways": decisions_and_takeaways,
        "action_items": action_items,
        "raw_transcript": raw_transcript,
    }

    tpl = DocxTemplate(TEMPLATE_PATH)
    tpl.render(context)

    buffer = io.BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer


def sanitized_download_name(data, session_id):
    filename = (data.get("filename") or "").strip()
    if filename.lower() in ("", "paste", "recording"):
        base = f"summary_session_{session_id}"
    else:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(filename)[0]).strip("_") or f"summary_session_{session_id}"

    date_suffix = ""
    created_at = data.get("created_at")
    if created_at:
        try:
            date_suffix = "_" + datetime.fromisoformat(created_at).strftime("%Y%m%d")
        except ValueError:
            pass

    return f"{base}{date_suffix}.docx"
