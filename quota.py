"""Local shadow-tracking of each user's Gemini rate limits.

Google enforces the real quota on the user's API key, server-side — this
module doesn't enforce anything itself. It estimates request cost before
calling Gemini, checks that estimate against a rolling-window read of
`usage_logs`, and reconciles with Gemini's real numbers afterward, purely to
warn the user before they hit Google's actual wall. It can drift from the
true state (most likely if the same key is also used outside this app), so a
real 429 from Gemini is always the source of truth: see
`record_rejection_by_gemini`, which resyncs the local cooldown/limits toward
whatever Gemini's error response says.
"""

from datetime import datetime, timedelta, timezone

import database

# Google's published defaults, last verified against this project's actual
# Cloud Console quota dashboard on 2026-08-12 for gemini-2.5-flash. Free
# tier is the only one seeded automatically; a paid-tier user's row in
# `quota_limits` can be edited directly (or switched to "tier_1") until a
# tier-management UI exists — see the task's non-goals.
#
# STALE: GEMINI_MODEL (gemini_client.py) was bumped to gemini-3.6-flash
# after Google retired 2.5-flash for new users/keys — these rpm/tpm/rpd
# numbers were never re-checked against the dashboard for 3.6-flash and may
# be wrong in either direction. Re-verify before trusting them again.
# Re-check periodically even once confirmed: Google has changed free-tier
# quotas without notice before (a ~50-80% cut across the free tier happened
# around December 2025) and limits vary by model family (Flash vs. Pro) in
# ways that aren't stable. If this app ever calls more than one model,
# TIER_DEFAULTS will need to be keyed by model, not just tier.
TIER_DEFAULTS = {
    "free": {"tier": "free", "rpm": 5, "tpm": 250_000, "rpd": 20},
    "tier_1": {"tier": "tier_1", "rpm": 1000, "tpm": 1_000_000, "rpd": 10_000},
}

# How long a downward correction (from a real Gemini 429, see
# record_rejection_by_gemini) is trusted before maybe_relax_limit re-tests
# whether it was too conservative. Long enough that a single noisy 429 near
# a window boundary doesn't cause the limit to flap; short enough that a
# stale correction doesn't cap the user for days.
RELAX_AFTER_SECONDS = 24 * 60 * 60

# Maps each tracked limit column to its key in a TIER_DEFAULTS entry.
_DIMENSION_TO_DEFAULT_KEY = {"rpm_limit": "rpm", "tpm_limit": "tpm", "rpd_limit": "rpd"}

# Treat this fraction of the tracked limit as "full". Audio estimates in
# particular can be off by a few percent, and reconciliation only corrects
# *future* checks, not the request currently in flight — the margin reduces
# (doesn't eliminate) the chance of a near-the-edge request passing the local
# check and still getting rejected by Gemini.
SAFETY_MARGIN = 0.9

# Gemini tokenizes audio input at a fixed rate, independent of content.
AUDIO_TOKENS_PER_SECOND = 32

# Reserved on top of the input estimate for the expected response, since
# audio summarization (transcript + summary + decisions + action_items)
# tends to produce longer output than a text-only summary.
OUTPUT_TOKEN_BUFFER = 1500


class QuotaExceeded(Exception):
    """Raised by check_capacity when a request would exceed local capacity
    tracking (or an active Gemini-issued cooldown). `dimension` is one of
    "cooldown", "rpm", "tpm", "rpd"; `detail` carries a message plus
    resets_in_seconds for the frontend."""

    def __init__(self, dimension, detail):
        self.dimension = dimension
        self.detail = detail
        super().__init__(detail.get("message", dimension))


def get_default_limits(tier="free"):
    return TIER_DEFAULTS.get(tier, TIER_DEFAULTS["free"])


def get_media_duration_seconds(file_path):
    """Reads duration from the file's own header (no full-stream decode) via
    mutagen. Returns None if the format/file can't be parsed, so callers can
    fall back to a coarser estimate rather than crash."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return None


def estimate_audio_tokens(duration_seconds):
    return int(max(duration_seconds, 0) * AUDIO_TOKENS_PER_SECOND)


def estimate_text_tokens(text, count_tokens_fn=None):
    """Uses Gemini's countTokens endpoint (free, quota'd separately) when a
    client is available; falls back to a rough chars/4 heuristic so a
    countTokens failure never blocks the actual summarise call."""
    if count_tokens_fn is not None:
        try:
            return count_tokens_fn(text)
        except Exception:
            pass
    return max(1, len(text or "") // 4)


def _parse_iso(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _resets_in_seconds(oldest_timestamp, window_seconds):
    oldest = _parse_iso(oldest_timestamp)
    if not oldest:
        return 0
    elapsed = (datetime.now(timezone.utc) - oldest).total_seconds()
    return max(0, int(round(window_seconds - elapsed)))


def maybe_relax_limit(user_id, dimension, tier="free"):
    """Re-tests one tracked dimension that was previously tightened by
    record_rejection_by_gemini. A downward correction is trusted for
    RELAX_AFTER_SECONDS (it might have been exactly right, or Gemini's real
    quota might have moved since); past that, it's reset to the current
    TIER_DEFAULTS value rather than left pinned at the last observed
    failure count forever. A fresh real 429 will immediately tighten it
    again if the default was in fact still too generous, so this only
    costs one extra rejection in the worst case — not a permanent cap."""
    if dimension not in _DIMENSION_TO_DEFAULT_KEY:
        raise ValueError(f"Unsupported quota dimension: {dimension!r}")

    limits = database.get_quota_limits(user_id, get_default_limits(tier))
    corrected_at = _parse_iso(limits.get(f"{dimension}_corrected_at"))
    if not corrected_at:
        return

    elapsed = (datetime.now(timezone.utc) - corrected_at).total_seconds()
    if elapsed < RELAX_AFTER_SECONDS:
        return

    default_value = get_default_limits(tier)[_DIMENSION_TO_DEFAULT_KEY[dimension]]
    database.relax_quota_limit(user_id, dimension, default_value)


def check_capacity(user_id, estimated_tokens, tier="free"):
    """Raises QuotaExceeded if calling Gemini now would push the user over
    ~90% of their tracked RPM/TPM capacity, or if a Gemini-issued cooldown
    from a previous real 429 is still active. Returns the snapshot used for
    the decision when the request is allowed, so the caller can log it."""
    for dimension in _DIMENSION_TO_DEFAULT_KEY:
        maybe_relax_limit(user_id, dimension, tier)

    limits = database.get_quota_limits(user_id, get_default_limits(tier))

    if limits["cooldown_until"]:
        cooldown_until = _parse_iso(limits["cooldown_until"])
        if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
            retry_after = max(1, int((cooldown_until - datetime.now(timezone.utc)).total_seconds()))
            raise QuotaExceeded("cooldown", {
                "message": "Gemini asked this key to back off; still inside that cooldown window.",
                "resets_in_seconds": retry_after,
            })

    minute_usage = database.get_usage_window(user_id, "1 minute")
    day_usage = database.get_usage_window(user_id, "1 day")

    if minute_usage["requests"] + 1 > limits["rpm_limit"] * SAFETY_MARGIN:
        raise QuotaExceeded("rpm", {
            "message": "This would exceed your requests-per-minute capacity.",
            "resets_in_seconds": _resets_in_seconds(minute_usage["oldest_timestamp"], 60),
        })
    if minute_usage["tokens"] + estimated_tokens > limits["tpm_limit"] * SAFETY_MARGIN:
        raise QuotaExceeded("tpm", {
            "message": "This would exceed your tokens-per-minute capacity.",
            "resets_in_seconds": _resets_in_seconds(minute_usage["oldest_timestamp"], 60),
        })
    if day_usage["requests"] + 1 > limits["rpd_limit"] * SAFETY_MARGIN:
        raise QuotaExceeded("rpd", {
            "message": "This would exceed your requests-per-day capacity.",
            "resets_in_seconds": _resets_in_seconds(day_usage["oldest_timestamp"], 86400),
        })

    return {"limits": limits, "minute_usage": minute_usage, "day_usage": day_usage}


def record_success(user_id, request_type, estimated_tokens, usage_metadata=None):
    """Writes the real token counts from Gemini's usageMetadata (included
    free in every response) instead of the pre-flight estimate, so the next
    rolling-window query reflects actual usage."""
    actual_tokens = getattr(usage_metadata, "total_token_count", None) if usage_metadata else None
    if actual_tokens is None:
        actual_tokens = estimated_tokens
    database.log_usage(user_id, request_type, "success",
                        estimated_tokens=estimated_tokens, actual_tokens=actual_tokens)


def record_preflight_rejection(user_id, request_type, estimated_tokens):
    """Logged for visibility only — excluded from rolling-window sums, since
    a request rejected before calling Gemini never touched real quota."""
    database.log_usage(user_id, request_type, "rejected_preflight", estimated_tokens=estimated_tokens)


def _dimension_from_violation(violation):
    haystack = " ".join(filter(None, [violation.get("metric"), violation.get("quota_id")])).lower()
    if "perday" in haystack or "per day" in haystack:
        return "rpd_limit"
    if "token" in haystack:
        return "tpm_limit"
    if "request" in haystack:
        return "rpm_limit"
    return None


def parse_gemini_429(exc):
    """Best-effort extraction of RetryInfo.retryDelay and QuotaFailure
    violations from a google.genai ClientError raised for a 429. Returns
    {"retry_after_seconds": int|None, "quota_violations": [{"metric", "quota_id"}, ...]}
    — empty/None fields if the error body doesn't match the expected shape,
    so a parsing miss never crashes the request path."""
    body = getattr(exc, "details", None) or {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]

    retry_after_seconds = None
    quota_violations = []
    details = body.get("details") if isinstance(body, dict) else None
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            type_url = item.get("@type", "")
            if type_url.endswith("RetryInfo"):
                retry_after_seconds = _parse_retry_delay(item.get("retryDelay"))
            elif type_url.endswith("QuotaFailure"):
                for violation in item.get("violations", []) or []:
                    quota_violations.append({
                        "metric": violation.get("quotaMetric") or violation.get("subject"),
                        "quota_id": violation.get("quotaId"),
                    })
    return {"retry_after_seconds": retry_after_seconds, "quota_violations": quota_violations}


def _parse_retry_delay(value):
    """RetryInfo.retryDelay is a protobuf Duration, usually serialized as a
    string like "19s" or "1.500s"."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    if isinstance(value, str):
        v = value.strip()
        if v.endswith("s"):
            v = v[:-1]
        try:
            return max(0, int(round(float(v))))
        except ValueError:
            return None
    return None


def record_rejection_by_gemini(user_id, request_type, estimated_tokens, parsed_429):
    """The authoritative path: Gemini itself said no. Logs the attempt,
    starts a cooldown from RetryInfo if one was given, and tightens whichever
    local limit QuotaFailure blamed — since our assumption for that
    dimension was evidently too generous."""
    database.log_usage(user_id, request_type, "rate_limited", estimated_tokens=estimated_tokens)

    retry_after_seconds = parsed_429.get("retry_after_seconds")
    if retry_after_seconds is not None:
        until = datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
        database.set_quota_cooldown(user_id, until.strftime("%Y-%m-%d %H:%M:%S"))

    for violation in parsed_429.get("quota_violations", []):
        dimension = _dimension_from_violation(violation)
        if dimension == "rpm_limit":
            window = database.get_usage_window(user_id, "1 minute")
            database.update_quota_limit(user_id, "rpm_limit", max(window["requests"], 1))
        elif dimension == "tpm_limit":
            window = database.get_usage_window(user_id, "1 minute")
            database.update_quota_limit(user_id, "tpm_limit", max(window["tokens"], 1))
        elif dimension == "rpd_limit":
            window = database.get_usage_window(user_id, "1 day")
            database.update_quota_limit(user_id, "rpd_limit", max(window["requests"], 1))


def is_rate_limit_error(exc):
    if getattr(exc, "code", None) == 429:
        return True
    return getattr(exc, "status", None) == "RESOURCE_EXHAUSTED"


def _dimension_status(limit, used, oldest_timestamp, window_seconds):
    return {
        "limit": limit,
        "used": used,
        "remaining": max(limit - used, 0),
        "resets_in_seconds": _resets_in_seconds(oldest_timestamp, window_seconds),
    }


def compute_quota_status(user_id, tier="free"):
    """Assembles the GET /api/quota payload: current used/remaining/reset
    for each of the three dimensions Gemini actually enforces."""
    limits = database.get_quota_limits(user_id, get_default_limits(tier))
    minute_usage = database.get_usage_window(user_id, "1 minute")
    day_usage = database.get_usage_window(user_id, "1 day")
    return {
        "tier": limits["tier"],
        "cooldown_until": limits["cooldown_until"],
        "rpm": _dimension_status(limits["rpm_limit"], minute_usage["requests"], minute_usage["oldest_timestamp"], 60),
        "tpm": _dimension_status(limits["tpm_limit"], minute_usage["tokens"], minute_usage["oldest_timestamp"], 60),
        "rpd": _dimension_status(limits["rpd_limit"], day_usage["requests"], day_usage["oldest_timestamp"], 86400),
    }
