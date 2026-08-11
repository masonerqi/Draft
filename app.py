import os
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Safety cap on upload size (imported media files, via /summarise). Flask
# has no limit by default, which would otherwise let a request buffer an
# unbounded amount of data into memory/disk before the route even runs.
MAX_UPLOAD_BYTES = 300 * 1024 * 1024

# Ensure environment variables from .env are loaded before any module below
# reads them at import time (database.py resolves DATABASE_PATH as soon as
# it's imported) — loading dotenv after that import was a real bug: .env's
# DATABASE_PATH was silently never applied, and DB_PATH fell back to a
# cwd-relative default that could differ between how/where the server was
# launched, making data look like it "disappeared" across runs.
load_dotenv()

# Import database init function
from database import init_db
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

    # Register blueprints
    from routes.auth import auth_bp
    from routes.summaries import summaries_bp
    from routes.main import main_bp
    from routes.folders import folders_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(summaries_bp)
    app.register_blueprint(folders_bp)

    return app


if __name__ == "__main__":
    # Run development server
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)

