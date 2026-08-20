import os
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Safety cap on upload size (imported media files, via /summarise). Flask
# has no limit by default, which would otherwise let a request buffer an
# unbounded amount of data into memory/disk before the route even runs.
MAX_UPLOAD_BYTES = 300 * 1024 * 1024

# Ensure environment variables from .env are loaded before any module below
# reads them at import time (database.py resolves DATABASE_URL as soon as
# it's imported) — loading dotenv after that import was a real bug: .env's
# DATABASE_URL was silently never applied, leaving the database unreachable.
load_dotenv()

# Import database init function
from database import init_db, start_maintenance_thread
from firebase_config import init_firebase

# Blueprints will be imported and registered in the factory


def create_app(test_config=None):
    """Application factory for the Draft meeting summarizer app."""
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Load configuration from environment; provide a safe default for local dev
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    )

    # Flask raises 413 before the route handler ever runs, so without this
    # handler the client gets Werkzeug's default HTML error page instead of
    # JSON — static/app.js's submitToAPI() expects a JSON body to read the
    # error message from.
    @app.errorhandler(413)
    def handle_upload_too_large(_error):
        max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return jsonify({"error": f"File exceeds the maximum allowed size ({max_mb}MB)."}), 413

    # Allow cross-origin requests from the frontend with credentials
    CORS(app, supports_credentials=True)

    # Initialize optional third-party services
    init_firebase()

    # Initialize or migrate the database
    init_db()

    # Periodic sweep of usage_logs rows older than the longest rolling quota
    # window (1 day), and of finished summary_jobs handoff rows. Skipped
    # under pytest/test_config so test runs don't leak background threads.
    if not (test_config or os.environ.get("PYTEST_CURRENT_TEST")):
        start_maintenance_thread()

    # Register blueprints
    from routes.auth import auth_bp
    from routes.summaries import summaries_bp
    from routes.main import main_bp
    from routes.folders import folders_bp
    from routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(summaries_bp)
    app.register_blueprint(folders_bp)
    app.register_blueprint(admin_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    # PORT is injected by container hosts (e.g. Vercel); default to 5000 locally.
    port = int(os.environ.get("PORT", 5000))
    # Only enable the interactive debugger/reloader for local development —
    # never in a deployed container, where it would leak stack traces.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port)

