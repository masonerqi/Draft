"""
Optional Firebase Admin initialization helper.
If the project uses Firebase Admin, provide the service account credentials via
one of:
  - FIREBASE_SERVICE_ACCOUNT_JSON: the service account JSON itself (for hosts
    like Vercel where a credentials file can't be mounted onto the container)
  - FIREBASE_SERVICE_ACCOUNT: a filesystem path to the service account JSON
  - a firebase-credentials.json file in the project root (local dev fallback)
FIREBASE_PROJECT_ID sets the project id.
This module safely does nothing if firebase_admin isn't available or no credentials are found.
"""
import json
import os

try:
    import firebase_admin
    from firebase_admin import credentials
except Exception:
    firebase_admin = None


def init_firebase():
    if not firebase_admin:
        return None
    project = os.environ.get("FIREBASE_PROJECT_ID")

    svc_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if svc_json:
        cred = credentials.Certificate(json.loads(svc_json))
    else:
        svc_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
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
