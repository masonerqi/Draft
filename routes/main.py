from flask import Blueprint, render_template
from routes.utils import get_current_user

main_bp = Blueprint("main_bp", __name__)


@main_bp.route("/")
def index():
    # Renders the main UI template (uses static assets under /static)
    # If a user is logged in, provide their display name and email to the template.
    current_user = get_current_user()
    user_name = None
    user_email = None
    if current_user:
        # Prefer stored `name`, fall back to username (which often contains the email)
        user_name = current_user.get("name") or current_user.get("username")
        user_email = current_user.get("username")
    return render_template("index.html", user_name=user_name, user_email=user_email)
