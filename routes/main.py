from flask import Blueprint, render_template

main_bp = Blueprint("main_bp", __name__)


@main_bp.route("/")
def index():
    # Renders the main UI template (uses static assets under /static)
    return render_template("index.html")
