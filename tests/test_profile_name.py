import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import database


def _client(db_path):
    application = app_module.create_app(test_config={"TESTING": True})
    application.config.update(TESTING=True)
    return application.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_update_name_persists_and_is_returned(db_path, user_id):
    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/api/user/settings", json={"name": "Ada Lovelace"})

    assert res.status_code == 200
    assert res.get_json()["name"] == "Ada Lovelace"
    assert database.get_user_by_id(user_id)["name"] == "Ada Lovelace"


def test_update_name_rejects_blank(db_path, user_id):
    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/api/user/settings", json={"name": "   "})

    assert res.status_code == 400


def test_update_name_rejects_over_max_length(db_path, user_id):
    client = _client(db_path)
    _login(client, user_id)

    res = client.post("/api/user/settings", json={"name": "x" * 81})

    assert res.status_code == 400


def test_get_user_settings_includes_name(db_path, user_id):
    database.update_user_name(user_id, "Grace Hopper")
    client = _client(db_path)
    _login(client, user_id)

    res = client.get("/api/user/settings")

    assert res.status_code == 200
    assert res.get_json()["name"] == "Grace Hopper"
