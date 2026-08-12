import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import database
import quota


def _insert_usage_at(user_id, timestamp, request_type="text", status="success", estimated_tokens=10, actual_tokens=10):
    """Inserts a usage_logs row at an explicit timestamp — unlike
    database.log_usage (always "now"), so tests can put usage inside the
    1-day RPD window while keeping it outside the 1-minute RPM/TPM window."""
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute(
        "INSERT INTO usage_logs (user_id, timestamp, request_type, estimated_tokens, actual_tokens, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, timestamp, request_type, estimated_tokens, actual_tokens, status),
    )
    conn.commit()
    conn.close()


# --- Pre-flight rejection when estimate exceeds remaining capacity --------

def test_check_capacity_allows_a_fresh_user(db_path, user_id):
    result = quota.check_capacity(user_id, estimated_tokens=1000)
    assert result["limits"]["rpm_limit"] == quota.get_default_limits()["rpm"]


def test_check_capacity_rejects_when_rpm_would_be_exceeded(db_path, user_id):
    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    allowed_requests = int(limits["rpm_limit"] * quota.SAFETY_MARGIN)
    for _ in range(allowed_requests):
        database.log_usage(user_id, "text", "success", estimated_tokens=100, actual_tokens=100)

    with pytest.raises(quota.QuotaExceeded) as exc_info:
        quota.check_capacity(user_id, estimated_tokens=100)

    assert exc_info.value.dimension == "rpm"
    assert exc_info.value.detail["resets_in_seconds"] >= 0


def test_check_capacity_rejects_when_tpm_would_be_exceeded(db_path, user_id):
    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    tpm_ceiling = int(limits["tpm_limit"] * quota.SAFETY_MARGIN)
    database.log_usage(user_id, "audio", "success", estimated_tokens=tpm_ceiling, actual_tokens=tpm_ceiling)

    with pytest.raises(quota.QuotaExceeded) as exc_info:
        quota.check_capacity(user_id, estimated_tokens=1000)

    assert exc_info.value.dimension == "tpm"


def test_check_capacity_rejects_when_rpd_would_be_exceeded(db_path, user_id):
    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    allowed_requests = int(limits["rpd_limit"] * quota.SAFETY_MARGIN)
    # Backdated by 5 minutes so these land inside the 1-day RPD window but
    # outside the 1-minute RPM/TPM window — otherwise the (much lower) RPM
    # limit would trip first and the test wouldn't isolate RPD.
    five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(allowed_requests):
        _insert_usage_at(user_id, five_minutes_ago)

    with pytest.raises(quota.QuotaExceeded) as exc_info:
        quota.check_capacity(user_id, estimated_tokens=10)

    assert exc_info.value.dimension == "rpd"


def test_check_capacity_defers_to_an_active_gemini_cooldown(db_path, user_id):
    database.get_quota_limits(user_id, quota.get_default_limits())
    until = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    database.set_quota_cooldown(user_id, until)

    with pytest.raises(quota.QuotaExceeded) as exc_info:
        quota.check_capacity(user_id, estimated_tokens=10)

    assert exc_info.value.dimension == "cooldown"
    assert exc_info.value.detail["resets_in_seconds"] > 0


def test_check_capacity_ignores_an_expired_cooldown(db_path, user_id):
    database.get_quota_limits(user_id, quota.get_default_limits())
    expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    database.set_quota_cooldown(user_id, expired)

    # Should not raise — the cooldown is in the past.
    quota.check_capacity(user_id, estimated_tokens=10)


# --- Counter reconciliation after a real Gemini call -----------------------

def test_record_success_writes_actual_tokens_not_the_estimate(db_path, user_id):
    class FakeUsageMetadata:
        total_token_count = 4321

    quota.record_success(user_id, "text", estimated_tokens=9999, usage_metadata=FakeUsageMetadata())

    window = database.get_usage_window(user_id, "1 minute")
    assert window["tokens"] == 4321
    assert window["requests"] == 1


def test_record_success_falls_back_to_estimate_without_usage_metadata(db_path, user_id):
    quota.record_success(user_id, "audio", estimated_tokens=2500, usage_metadata=None)

    window = database.get_usage_window(user_id, "1 minute")
    assert window["tokens"] == 2500


def test_preflight_rejection_is_excluded_from_rolling_window(db_path, user_id):
    quota.record_preflight_rejection(user_id, "text", estimated_tokens=5000)

    window = database.get_usage_window(user_id, "1 minute")
    assert window["requests"] == 0
    assert window["tokens"] == 0


# --- 429 parsing (RetryInfo / QuotaFailure) with a mocked Gemini error -----

class FakeGeminiClientError(Exception):
    """Shaped like google.genai.errors.ClientError: .code/.status/.details."""

    def __init__(self, details):
        self.code = 429
        self.status = "RESOURCE_EXHAUSTED"
        self.details = details
        super().__init__("429 RESOURCE_EXHAUSTED")


def _mocked_429_body(retry_delay="19s", quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    return {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted.",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaMetric": "generate_content_requests", "quotaId": quota_id}],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
            ],
        }
    }


def test_parse_gemini_429_extracts_retry_delay_and_quota_failure():
    exc = FakeGeminiClientError(_mocked_429_body())
    parsed = quota.parse_gemini_429(exc)

    assert parsed["retry_after_seconds"] == 19
    assert len(parsed["quota_violations"]) == 1
    assert parsed["quota_violations"][0]["quota_id"] == "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def test_parse_gemini_429_handles_fractional_retry_delay():
    exc = FakeGeminiClientError(_mocked_429_body(retry_delay="1.5s"))
    parsed = quota.parse_gemini_429(exc)
    assert parsed["retry_after_seconds"] == 2  # rounds to nearest second


def test_parse_gemini_429_is_best_effort_on_unexpected_shape():
    exc = FakeGeminiClientError({"error": {"message": "something else", "status": "INTERNAL"}})
    parsed = quota.parse_gemini_429(exc)
    assert parsed == {"retry_after_seconds": None, "quota_violations": []}


def test_is_rate_limit_error_detects_429():
    assert quota.is_rate_limit_error(FakeGeminiClientError(_mocked_429_body())) is True
    assert quota.is_rate_limit_error(ValueError("unrelated")) is False


# --- Free-tier default values -----------------------------------------

def test_get_default_limits_free_tier_matches_cloud_console():
    defaults = quota.get_default_limits("free")
    assert defaults == {"tier": "free", "rpm": 5, "tpm": 250_000, "rpd": 20}


def test_check_capacity_rejects_at_90_percent_of_corrected_rpd(db_path, user_id):
    # 90% margin of the corrected rpd=20 is 18 — the 19th request in the
    # rolling day window should trip the RPD check.
    five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(18):
        _insert_usage_at(user_id, five_minutes_ago)

    with pytest.raises(quota.QuotaExceeded) as exc_info:
        quota.check_capacity(user_id, estimated_tokens=10)

    assert exc_info.value.dimension == "rpd"


def test_check_capacity_allows_below_90_percent_of_corrected_rpd(db_path, user_id):
    five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(17):
        _insert_usage_at(user_id, five_minutes_ago)

    # Should not raise.
    quota.check_capacity(user_id, estimated_tokens=10)


# --- Upward drift-recovery (maybe_relax_limit) ------------------------------

def test_maybe_relax_limit_resets_a_stale_correction_to_the_tier_default(db_path, user_id):
    database.get_quota_limits(user_id, quota.get_default_limits())
    database.update_quota_limit(user_id, "rpd_limit", 3)

    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("UPDATE quota_limits SET rpd_limit_corrected_at = ? WHERE user_id = ?", (stale, user_id))
    conn.commit()
    conn.close()

    quota.maybe_relax_limit(user_id, "rpd_limit")

    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    assert limits["rpd_limit"] == quota.get_default_limits()["rpd"]
    assert limits["rpd_limit_corrected_at"] is None


def test_maybe_relax_limit_leaves_a_recent_correction_alone(db_path, user_id):
    database.get_quota_limits(user_id, quota.get_default_limits())
    database.update_quota_limit(user_id, "rpd_limit", 3)

    quota.maybe_relax_limit(user_id, "rpd_limit")

    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    assert limits["rpd_limit"] == 3
    assert limits["rpd_limit_corrected_at"] is not None


def test_record_rejection_by_gemini_starts_cooldown_and_tightens_limit(db_path, user_id):
    database.get_quota_limits(user_id, quota.get_default_limits())
    database.log_usage(user_id, "text", "success", estimated_tokens=100, actual_tokens=100)

    parsed = quota.parse_gemini_429(_body_as_error())
    quota.record_rejection_by_gemini(user_id, "text", estimated_tokens=100, parsed_429=parsed)

    limits = database.get_quota_limits(user_id, quota.get_default_limits())
    assert limits["cooldown_until"] is not None
    # Tightened to the single successful request observed so far, since
    # Gemini said our RPM assumption was too generous.
    assert limits["rpm_limit"] == 1


def _body_as_error():
    return FakeGeminiClientError(_mocked_429_body())
