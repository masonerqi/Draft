from datetime import datetime, timezone

from flask import Blueprint, render_template
from routes.utils import get_current_user

main_bp = Blueprint("main_bp", __name__)


@main_bp.route("/")
def index():
    current_user = get_current_user()

    # Logged-out visitors see the public landing page; logged-in users go
    # straight to their workspace.
    if not current_user:
        return render_template("landing.html", current_year=datetime.now(timezone.utc).year)

    # Prefer stored `name`, fall back to username (which often contains the email)
    user_name = current_user.get("name") or current_user.get("username")
    user_email = current_user.get("username")
    return render_template(
        "index.html",
        user_name=user_name,
        user_email=user_email,
        current_year=datetime.now(timezone.utc).year,
    )
