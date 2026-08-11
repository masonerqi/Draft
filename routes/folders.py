from flask import Blueprint, jsonify
from routes.utils import _parse_request_payload, get_current_user, login_required
from database import (
    create_folder,
    get_folders,
    delete_folder,
    assign_sessions_to_folder,
    get_sessions_by_folder,
)

folders_bp = Blueprint("folders_bp", __name__)


@folders_bp.route("/folders", methods=["GET"])
@login_required
def list_folders():
    current_user = get_current_user()
    return jsonify(get_folders(current_user["id"])), 200


@folders_bp.route("/folders", methods=["POST"])
@login_required
def create_folder_route():
    current_user = get_current_user()
    payload = _parse_request_payload()
    name = (payload.get("name") or "").strip()
    icon = (payload.get("icon") or "").strip()

    if not name:
        return jsonify({"error": "Folder name is required."}), 400
    if not icon:
        return jsonify({"error": "Folder icon is required."}), 400

    folder = create_folder(current_user["id"], name, icon)
    return jsonify(folder), 201


@folders_bp.route("/folders/<int:folder_id>", methods=["DELETE"])
@login_required
def delete_folder_route(folder_id):
    current_user = get_current_user()
    delete_folder(folder_id, current_user["id"])
    return jsonify({"message": "Deleted"}), 200


@folders_bp.route("/folders/<int:folder_id>/sessions", methods=["GET"])
@login_required
def folder_sessions(folder_id):
    current_user = get_current_user()
    return jsonify(get_sessions_by_folder(current_user["id"], folder_id)), 200


@folders_bp.route("/sessions/folder", methods=["POST"])
@login_required
def assign_sessions_folder_route():
    current_user = get_current_user()
    payload = _parse_request_payload()
    session_ids = payload.get("session_ids") or []
    folder_id = payload.get("folder_id")

    if not isinstance(session_ids, list) or not session_ids:
        return jsonify({"error": "session_ids is required."}), 400

    try:
        session_ids = [int(sid) for sid in session_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "session_ids must be a list of ids."}), 400

    assign_sessions_to_folder(session_ids, current_user["id"], folder_id)
    return jsonify({"message": "Updated"}), 200
