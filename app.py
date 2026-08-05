import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

# Import database init function
from database import init_db
from firebase_config import init_firebase

# Ensure environment variables from .env are loaded early
load_dotenv()

# Blueprints will be imported and registered in the factory


def create_app(test_config=None):
    """Application factory for the Draft meeting summarizer app."""
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Load configuration from environment; provide a safe default for local dev
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

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

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(summaries_bp)

    return app


if __name__ == "__main__":
    # Run development server
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
