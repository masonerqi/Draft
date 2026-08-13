import psycopg2

from flask import Blueprint, session, jsonify, render_template, request, redirect, url_for
from routes.utils import _parse_request_payload, get_current_user, login_required, _mask_api_key
from database import (
    create_user,
    get_user_by_username,
    get_user_by_firebase_uid,
    get_user_api_key,
    set_user_api_key,
    get_user_summary_language,
    set_user_summary_language,
    create_or_get_user_from_firebase,
    update_user_email,
    update_user_name,
    delete_user_account,
)

# Mirrors the <input maxlength> in templates/settings.html's Full Name card.
MAX_NAME_LENGTH = 80

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


@auth_bp.route('/api/sync-session', methods=['POST'])
@login_required
def sync_session():
    """Refresh the Flask session's cached email/name from a fresh Firebase ID
    token. Firebase only updates its own record once the user confirms a
    verifyBeforeUpdateEmail link, so the client re-checks after reloads/
    re-auth and this endpoint pulls the latest claims into our session + DB.
    """
    payload = _parse_request_payload()
    id_token = (payload.get('token') or payload.get('idToken') or '').strip()

    if not id_token:
        return jsonify({'message': 'ID token is required.'}), 400

    if not firebase_auth:
        return jsonify({'message': 'Firebase Admin SDK not configured on server.'}), 500

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        print(f'❌ SYNC-SESSION ERROR: Firebase token verification failed: {e}')
        return jsonify({'message': f'Invalid Firebase token: {str(e)}'}), 401

    uid = decoded.get('uid') or decoded.get('sub')
    current_user = get_current_user()

    # The token must belong to the same account as the active Flask session —
    # otherwise a stale/foreign token could overwrite someone else's record.
    token_user = get_user_by_firebase_uid(uid) if uid else None
    if not token_user or token_user['id'] != current_user['id']:
        return jsonify({'message': 'Token does not match the current session.'}), 403

    email = decoded.get('email')
    name = decoded.get('name')

    if email and email != current_user.get('username'):
        try:
            update_user_email(current_user['id'], email)
        except psycopg2.IntegrityError:
            return jsonify({'message': 'This email address is already registered.'}), 409
        session['user_email'] = email
    elif email:
        session['user_email'] = email

    if name:
        session['user_name'] = name

    return jsonify({'success': True, 'email': session.get('user_email'), 'name': session.get('user_name')}), 200


@auth_bp.route('/api/account/delete-data', methods=['POST'])
def delete_account_data():
    """Purges everything owned by the current user (sessions/folders/user row)
    and clears the Flask session.

    The client now deletes the Firebase Auth account FIRST and only calls this
    afterward — so no local data is destroyed if Firebase rejects the deletion
    (e.g. auth/requires-recent-login). That ordering means the Flask session
    cookie is the normal way to identify the user here, but a Bearer ID token
    is accepted as a fallback (verify_id_token checks the token's signature,
    not whether the Firebase user still exists, so a token minted just before
    deletion remains valid for this one-time cleanup call).
    """
    current_user = get_current_user()

    if not current_user:
        auth_header = request.headers.get('Authorization', '')
        token = auth_header[7:].strip() if auth_header.lower().startswith('bearer ') else None
        if not token or not firebase_auth:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            decoded = firebase_auth.verify_id_token(token)
        except Exception as e:
            print(f'❌ DELETE-ACCOUNT ERROR: Firebase token verification failed: {e}')
            return jsonify({'error': 'Authentication required'}), 401
        uid = decoded.get('uid') or decoded.get('sub')
        current_user = get_user_by_firebase_uid(uid) if uid else None
        if not current_user:
            return jsonify({'error': 'Authentication required'}), 401

    delete_user_account(current_user['id'])
    session.clear()
    return jsonify({'success': True, 'message': 'All user data purged successfully.'}), 200


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
        'summary_language': get_user_summary_language(current_user['id']),
        'name': current_user.get('name'),
    }), 200


@auth_bp.route('/api/user/settings', methods=['POST'])
@login_required
def update_user_settings():
    current_user = get_current_user()
    payload = _parse_request_payload()

    # Each settings field is independently optional so the API key form, the
    # Summary language picker, and the Full Name field can each save without
    # touching the others.
    if 'gemini_api_key' not in payload and 'summary_language' not in payload and 'name' not in payload:
        return jsonify({'error': 'No settings provided.'}), 400

    response = {}

    if 'name' in payload:
        # Google-linked accounts have their name managed by Google — the
        # client hides/disables this field for them (see settings.js), the
        # same way it already handles the Email Address field. A plain
        # username/password account's name lives only in our own DB, so it's
        # always editable here.
        name = (payload.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name is required.'}), 400
        if len(name) > MAX_NAME_LENGTH:
            return jsonify({'error': f'Name must be {MAX_NAME_LENGTH} characters or fewer.'}), 400
        update_user_name(current_user['id'], name)
        session['user_name'] = name
        response['name'] = name

    if 'gemini_api_key' in payload:
        api_key = (payload.get('gemini_api_key') or '').strip()
        if not api_key:
            return jsonify({'error': 'Gemini API key is required.'}), 400
        # Store the API key only for the currently authenticated user.
        set_user_api_key(current_user['id'], api_key)
        response['api_key_saved'] = True
        response['api_key_masked'] = _mask_api_key(api_key)

    if 'summary_language' in payload:
        summary_language = (payload.get('summary_language') or '').strip() or None
        set_user_summary_language(current_user['id'], summary_language)
        response['summary_language'] = summary_language

    return jsonify(response), 200


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
