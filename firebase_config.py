"""
Optional Firebase Admin initialization helper.
If the project uses Firebase Admin, set FIREBASE_SERVICE_ACCOUNT environment variable
to the path of the service account JSON and FIREBASE_PROJECT_ID to the project id.
This module safely does nothing if firebase_admin isn't available or env vars are missing.
"""
import os

try:
    import firebase_admin
    from firebase_admin import credentials
except Exception:
    firebase_admin = None


def init_firebase():
    if not firebase_admin:
        return None
    svc_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    project = os.environ.get("FIREBASE_PROJECT_ID")
    # If service account not provided via env, look for firebase-credentials.json in project root
    if not svc_path:
        possible = os.path.join(os.getcwd(), "firebase-credentials.json")
        if os.path.exists(possible):
            svc_path = possible
    if not svc_path or not os.path.exists(svc_path):
        # Nothing to initialise
        return None
    cred = credentials.Certificate(svc_path)
    try:
        app = firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(cred, {"projectId": project} if project else None)
    return firebase_admin
