import json
import os
from functools import wraps
from flask import request, session, jsonify
from database import get_user_by_id

# Comma-separated allowlist of login emails permitted to use the /api/admin/*
# routes. There's no is_admin column/role system in this app — for a
# single-operator deployment, an env-configured allowlist is enough gating
# without the schema migration a full role system would need.
_ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def _parse_request_payload():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.get_json(force=True, silent=True)
    if payload is None:
        raw_body = request.get_data(as_text=True)
        if raw_body:
            try:
                payload = json.loads(raw_body)
            except (ValueError, TypeError):
                payload = None
    if payload is None:
        payload = request.form.to_dict()
    return payload or {}


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return jsonify({"error": "Authentication required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        current_user = get_current_user()
        if not current_user:
            return jsonify({"error": "Authentication required"}), 401
        email = (current_user.get("username") or "").strip().lower()
        if not email or email not in _ADMIN_EMAILS:
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _mask_api_key(api_key):
    if not api_key:
        return None
    if len(api_key) <= 8:
        return api_key[0:2] + "..."
    return f"{api_key[:4]}...{api_key[-4:]}"
