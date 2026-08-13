import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import database
import quota


def _client(db_path):
    application = app_module.create_app(test_config={"TESTING": True})
    application.config.update(TESTING=True)
    return application.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_non_admin_is_rejected(db_path, user_id, monkeypatch):
    # _ADMIN_EMAILS is read from the env once at import time, so tests set
    # the module-level set directly rather than the env var itself.
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"someone-else@example.com"})

    client = _client(db_path)
    _login(client, user_id)

    res = client.get("/api/admin/quota-limits")
    assert res.status_code == 403


def test_logged_out_request_requires_auth(db_path):
    client = _client(db_path)
    res = client.get("/api/admin/quota-limits")
    assert res.status_code == 401


def test_admin_can_list_and_update_quota_limits(db_path, user_id, monkeypatch):
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"quota-test@example.com"})
    database.set_user_api_key(user_id, "fake-gemini-key")

    client = _client(db_path)
    _login(client, user_id)

    # Listing works before the user has ever triggered a summarise request —
    # quota fields come back None since quota_limits is seeded lazily.
    listing = client.get("/api/admin/quota-limits").get_json()
    row = next(r for r in listing if r["user_id"] == user_id)
    assert row["rpd_limit"] is None

    res = client.patch(f"/api/admin/quota-limits/{user_id}", json={"rpd_limit": 20, "tier": "tier_1"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["rpd"]["limit"] == 20
    assert body["tier"] == "tier_1"

    # The override must clear rpd_limit_corrected_at, otherwise
    # maybe_relax_limit would eventually reset it back to TIER_DEFAULTS.
    limits = database.get_quota_limits(user_id, database.hash_api_key("fake-gemini-key"), quota.get_default_limits())
    assert limits["rpd_limit"] == 20
    assert limits["rpd_limit_corrected_at"] is None


def test_admin_update_requires_the_user_to_have_an_api_key(db_path, user_id, monkeypatch):
    # Quota rows are per key, so there's nothing to edit until the user has
    # saved one.
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"quota-test@example.com"})

    client = _client(db_path)
    _login(client, user_id)

    res = client.patch(f"/api/admin/quota-limits/{user_id}", json={"rpd_limit": 20})
    assert res.status_code == 400
    assert "per key" in res.get_json()["error"]


def test_admin_listing_marks_the_active_key(db_path, user_id, monkeypatch):
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"quota-test@example.com"})
    database.get_quota_limits(user_id, database.hash_api_key("old-key"), quota.get_default_limits())
    database.get_quota_limits(user_id, database.hash_api_key("new-key"), quota.get_default_limits())
    database.set_user_api_key(user_id, "new-key")

    client = _client(db_path)
    _login(client, user_id)

    listing = client.get("/api/admin/quota-limits").get_json()
    rows = [r for r in listing if r["user_id"] == user_id]
    assert len(rows) == 2
    active = [r for r in rows if r["is_active_key"]]
    assert len(active) == 1
    assert active[0]["key_hash"] == database.hash_api_key("new-key")


def test_admin_update_rejects_invalid_and_missing_fields(db_path, user_id, monkeypatch):
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"quota-test@example.com"})
    database.set_user_api_key(user_id, "fake-gemini-key")

    client = _client(db_path)
    _login(client, user_id)

    res = client.patch(f"/api/admin/quota-limits/{user_id}", json={"rpd_limit": "not-a-number"})
    assert res.status_code == 400

    res = client.patch(f"/api/admin/quota-limits/{user_id}", json={})
    assert res.status_code == 400

    res = client.patch("/api/admin/quota-limits/999999", json={"rpd_limit": 5})
    assert res.status_code == 404


def test_admin_can_clear_a_cooldown(db_path, user_id, monkeypatch):
    monkeypatch.setattr("routes.utils._ADMIN_EMAILS", {"quota-test@example.com"})
    database.set_user_api_key(user_id, "fake-gemini-key")
    key_hash = database.hash_api_key("fake-gemini-key")
    database.get_quota_limits(user_id, key_hash, quota.get_default_limits())
    database.set_quota_cooldown(user_id, key_hash, "2026-08-12 23:59:00")

    client = _client(db_path)
    _login(client, user_id)

    res = client.patch(f"/api/admin/quota-limits/{user_id}", json={"cooldown_until": None})
    assert res.status_code == 200

    limits = database.get_quota_limits(user_id, key_hash, quota.get_default_limits())
    assert limits["cooldown_until"] is None
