from flask import Blueprint, session, jsonify, render_template, request, redirect, url_for
from routes.utils import _parse_request_payload, get_current_user, login_required, _mask_api_key
from database import (
    create_user,
    get_user_by_username,
    get_user_api_key,
    set_user_api_key,
    create_or_get_user_from_firebase,
)

# Import Firebase Admin SDK auth gracefully. If it is not available, the server
# will still start but auth routes will return a clear error message.
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


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Render the register page or handle a registration POST request."""
    if request.method == 'GET':
        return render_template('auth.html', show_register=True)

    payload = _parse_request_payload()
    token = (payload.get('token') or '').strip()

    # If the client created a Firebase account, handle the token-based path.
    if token:
        if not firebase_auth:
            return jsonify({'message': 'Firebase Admin SDK not configured on server.'}), 500

        try:
            decoded = firebase_auth.verify_id_token(token)
        except Exception as e:
            print(f'❌ REGISTRATION ERROR: Firebase token verification failed: {e}')
            return jsonify({'message': str(e)}), 400

        uid = decoded.get('uid') or decoded.get('sub')
        email = decoded.get('email') or payload.get('email')
        name = payload.get('name') or decoded.get('name')

        if not uid:
            return jsonify({'message': 'Could not determine user UID from token.'}), 400

        try:
            user = create_or_get_user_from_firebase(uid, email=email, name=name)
        except Exception as e:
            print(f'❌ REGISTRATION ERROR: {e}')
            return jsonify({'message': str(e)}), 500

        session['user_id'] = user['id']
        session['user_name'] = user.get('name') or user.get('username')
        session['user_email'] = user.get('username')

        if request.form:
            return redirect(url_for('main_bp.index'))
        return jsonify({'id': user['id'], 'username': user['username']}), 201

    # Legacy form-based registration path.
    identifier = (payload.get('email') or payload.get('username') or '').strip()
    password = (payload.get('password') or '').strip()
    name = (payload.get('name') or '').strip()

    if not identifier or not password:
        if request.form:
            return render_template('auth.html', show_register=True, register_error='Email and password are required.')
        return jsonify({'message': 'Email and password are required.'}), 400

    if get_user_by_username(identifier):
        if request.form:
            return render_template('auth.html', show_register=True, register_error='An account with this email already exists.')
        return jsonify({'message': 'Account already exists.'}), 400

    try:
        user_id = create_user(identifier, password, name=name)
    except Exception as e:
        print(f'❌ REGISTRATION ERROR: {e}')
        if request.form:
            return render_template('auth.html', show_register=True, register_error=str(e))
        return jsonify({'message': str(e)}), 500

    session['user_id'] = user_id
    session['user_name'] = name or identifier.split('@')[0].title()
    session['user_email'] = identifier

    if request.form:
        return redirect(url_for('main_bp.index'))
    return jsonify({'id': user_id, 'username': identifier}), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate using a Firebase ID token and set the session."""
    payload = _parse_request_payload()
    id_token = (payload.get('token') or payload.get('idToken') or '').strip()

    if not id_token:
        return jsonify({'message': 'ID token is required.'}), 400

    if not firebase_auth:
        return jsonify({'message': 'Firebase Admin SDK not configured on server.'}), 500

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        print(f'❌ LOGIN ERROR: Firebase token verification failed: {e}')
        return jsonify({'message': f'Invalid Firebase token: {str(e)}'}), 401

    uid = decoded.get('uid') or decoded.get('sub')
    email = decoded.get('email')
    name = decoded.get('name')

    if not uid:
        return jsonify({'message': 'Could not determine user UID from token.'}), 400

    try:
        user = create_or_get_user_from_firebase(uid, email=email, name=name)
    except Exception as e:
        print(f'❌ LOGIN ERROR: {e}')
        return jsonify({'message': str(e)}), 500

    session['user_id'] = user['id']
    session['user_name'] = user.get('name') or email or user.get('username')
    session['user_email'] = email or user.get('username')

    return jsonify({'id': user['id'], 'email': session['user_email'], 'name': session['user_name']}), 200


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out'}), 200


@auth_bp.route('/me', methods=['GET'])
def me():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Not authenticated'}), 401
    return jsonify({
        'id': current_user['id'],
        'username': current_user['username'],
        'email': current_user['username'],
        'name': current_user.get('name'),
    }), 200


@auth_bp.route('/api/user/settings', methods=['GET'])
@login_required
def get_user_settings():
    current_user = get_current_user()
    # Retrieve only the API key for the authenticated user.
    # This endpoint cannot read keys for any other user.
    api_key = get_user_api_key(current_user['id'])
    return jsonify({
        'api_key_saved': bool(api_key),
        'api_key_masked': _mask_api_key(api_key),
    }), 200


@auth_bp.route('/api/user/settings', methods=['POST'])
@login_required
def update_user_settings():
    current_user = get_current_user()
    payload = _parse_request_payload()
    api_key = (payload.get('gemini_api_key') or '').strip()
    if not api_key:
        return jsonify({'error': 'Gemini API key is required.'}), 400
    # Store the API key only for the currently authenticated user.
    set_user_api_key(current_user['id'], api_key)
    return jsonify({'api_key_saved': True, 'api_key_masked': _mask_api_key(api_key)}), 200


@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    email = data.get('email')

    if not email:
        return jsonify({'message': 'Email is required'}), 400

    try:
        if not firebase_auth:
            return jsonify({'message': 'Firebase Admin SDK is not available.'}), 500

        link = firebase_auth.generate_password_reset_link(email)
        print(f'Generated Reset Link for {email}: {link}')
        return jsonify({'message': 'Password reset instructions sent to your email.'}), 200
    except Exception as e:
        return jsonify({'message': f'Error generating link: {str(e)}'}), 400
