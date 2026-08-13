from flask import Blueprint, jsonify

from routes.utils import _parse_request_payload, admin_required
import database
import quota

admin_bp = Blueprint("admin_bp", __name__)

_EDITABLE_LIMIT_FIELDS = ("rpm_limit", "tpm_limit", "rpd_limit")


@admin_bp.route("/api/admin/quota-limits", methods=["GET"])
@admin_required
def list_quota_limits():
    return jsonify(database.get_all_quota_limits()), 200


@admin_bp.route("/api/admin/quota-limits/<int:user_id>", methods=["GET"])
@admin_required
def get_quota_limits_route(user_id):
    if not database.get_user_by_id(user_id):
        return jsonify({"error": "User not found."}), 404
    return jsonify(quota.compute_quota_status(user_id, database.get_user_api_key(user_id))), 200


@admin_bp.route("/api/admin/quota-limits/<int:user_id>", methods=["PATCH"])
@admin_required
def update_quota_limits_route(user_id):
    if not database.get_user_by_id(user_id):
        return jsonify({"error": "User not found."}), 404

    payload = _parse_request_payload()

    # Quota rows are per API key, so an edit needs a specific key to apply
    # to — the user's currently-saved one, which is the only key the app
    # would use for their next request. A user with no key saved has no
    # quota to adjust; the row is seeded from tier defaults once they save
    # one and make a request.
    api_key = database.get_user_api_key(user_id)
    if not api_key:
        return jsonify({"error": "User has no Gemini API key configured; quota is tracked per key."}), 400
    key_hash = database.hash_api_key(api_key)

    # Seed a row first (mirrors quota.check_capacity's own lazy-init) so the
    # UPDATEs below have a row to affect, even for a user who has never made
    # a summarise request yet.
    database.get_quota_limits(user_id, key_hash, quota.get_default_limits())

    updated = {}

    for field in _EDITABLE_LIMIT_FIELDS:
        if field not in payload:
            continue
        try:
            value = int(payload[field])
        except (TypeError, ValueError):
            return jsonify({"error": f"{field} must be an integer."}), 400
        if value <= 0:
            return jsonify({"error": f"{field} must be positive."}), 400
        # relax_quota_limit (not update_quota_limit) so a manual override
        # also clears `_corrected_at` — otherwise maybe_relax_limit would
        # eventually mistake this for a stale downward correction and
        # quietly reset it back to TIER_DEFAULTS after 24h.
        database.relax_quota_limit(user_id, key_hash, field, value)
        updated[field] = value

    if "tier" in payload:
        tier = (payload.get("tier") or "").strip()
        if not tier:
            return jsonify({"error": "tier must be a non-empty string."}), 400
        database.set_quota_tier(user_id, key_hash, tier)
        updated["tier"] = tier

    if "cooldown_until" in payload:
        # An explicit null/empty value clears an active cooldown early.
        cooldown_value = (payload.get("cooldown_until") or None)
        database.set_quota_cooldown(user_id, key_hash, cooldown_value)
        updated["cooldown_until"] = cooldown_value

    if not updated:
        return jsonify({"error": "No recognised fields provided. Use rpm_limit, tpm_limit, rpd_limit, tier, and/or cooldown_until."}), 400

    return jsonify(quota.compute_quota_status(user_id, api_key)), 200
