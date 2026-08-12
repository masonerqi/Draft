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


def test_export_docx_returns_a_word_document_for_a_new_format_session(db_path, user_id):
    session_id = database.save_session(
        input_type="transcript",
        filename="Q3 Planning Sync",
        summary="The team reviewed Q3 roadmap slippage.",
        decisions=["**Scoped Launch Approved:** Ship the 5-widget dashboard on the original date."],
        action_items=["**Priya:** Draft the mockups by Friday."],
        transcript="Priya: Let's start.\nDavid: Sounds good.",
        user_id=user_id,
        detected_type="Business Meeting",
        key_concepts=["**Analytics Dashboard Scope:** Reduced from 12 to 5 widgets."],
    )

    client = _client(db_path)
    _login(client, user_id)

    res = client.get(f"/sessions/{session_id}/export/docx")

    assert res.status_code == 200
    assert res.mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert res.data[:2] == b"PK"  # docx is a zip container


def test_export_docx_returns_a_word_document_for_an_old_format_session(db_path, user_id):
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="An old-format note with no detected_type.",
        decisions=["We will migrate the auth system to Firebase next sprint."],
        action_items=["Set up the Firebase project."],
        transcript=None,
        user_id=user_id,
    )

    client = _client(db_path)
    _login(client, user_id)

    res = client.get(f"/sessions/{session_id}/export/docx")

    assert res.status_code == 200
    assert res.data[:2] == b"PK"


def test_export_docx_404s_for_an_unknown_session(db_path, user_id):
    client = _client(db_path)
    _login(client, user_id)

    res = client.get("/sessions/999999/export/docx")

    assert res.status_code == 404


def test_export_docx_404s_for_another_users_session(db_path, user_id):
    other_user_id = database.create_user("other-user@example.com", "password123", name="Other User")
    session_id = database.save_session(
        input_type="transcript",
        filename="paste",
        summary="Belongs to someone else.",
        decisions=[],
        action_items=[],
        transcript=None,
        user_id=other_user_id,
    )

    client = _client(db_path)
    _login(client, user_id)

    res = client.get(f"/sessions/{session_id}/export/docx")

    assert res.status_code == 404
