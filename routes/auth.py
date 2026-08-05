from flask import Blueprint, session, jsonify, render_template, request
from routes.utils import _parse_request_payload, get_current_user, login_required, _mask_api_key
from database import create_user, authenticate_user, get_user_by_username, get_user_api_key, set_user_api_key, create_or_get_user_from_firebase
from firebase_admin import auth

# Import firebase auth; if not installed, handle gracefully
try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
except Exception:
    firebase_admin = None
    firebase_auth = None

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET'])
def login_page():
    return render_template('auth.html')


@auth_bp.route("/register", methods=["POST"])
def register():
    payload = _parse_request_payload()
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if get_user_by_username(username):
        return jsonify({"error": "Username already exists"}), 400

    user_id = create_user(username, password)
    session["user_id"] = user_id
    return jsonify({"id": user_id, "username": username}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    """Authenticate using Firebase ID token. Expects payload { "token": "<ID_TOKEN>" }"""
    if not firebase_auth:
        return jsonify({"error": "Firebase auth not configured on server."}), 500

    payload = _parse_request_payload()
    id_token = (payload.get("token") or payload.get("idToken") or "").strip()
    if not id_token:
        return jsonify({"error": "ID token is required"}), 400

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        return jsonify({"error": f"Invalid Firebase token: {str(e)}"}), 401

    uid = decoded.get("uid") or decoded.get("sub")
    email = decoded.get("email")

    if not uid:
        return jsonify({"error": "Could not determine user UID from token"}), 400

    try:
        local_user_id = create_or_get_user_from_firebase(uid, email=email)
    except Exception as e:
        return jsonify({"error": f"Failed to create or find user: {str(e)}"}), 500

    session["user_id"] = local_user_id
    return jsonify({"id": local_user_id, "email": email}), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"}), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"id": current_user["id"], "username": current_user["username"]}), 200


@auth_bp.route("/api/user/settings", methods=["GET"])
@login_required
def get_user_settings():
    current_user = get_current_user()
    api_key = get_user_api_key(current_user["id"])
    return jsonify({
        "api_key_saved": bool(api_key),
        "api_key_masked": _mask_api_key(api_key)
    }), 200


@auth_bp.route("/api/user/settings", methods=["POST"])
@login_required
def update_user_settings():
    current_user = get_current_user()
    payload = _parse_request_payload()
    api_key = (payload.get("gemini_api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "Gemini API key is required."}), 400
    set_user_api_key(current_user["id"], api_key)
    return jsonify({"api_key_saved": True, "api_key_masked": _mask_api_key(api_key)}), 200


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    email = data.get("email")

    if not email:
        return jsonify({"message": "Email is required"}), 400

    try:
        # Generate the password reset link via Firebase Admin SDK
        link = auth.generate_password_reset_link(email)

        # TODO: Send `link` to the user via your custom email service (SendGrid, Flask-Mail, etc.)
        print(f"Generated Reset Link for {email}: {link}")

        return (
            jsonify({"message": "Password reset instructions sent to your email."}),
            200,
        )
    except Exception as e:
        return jsonify({"message": f"Error generating link: {str(e)}"}), 400