import os
from flask import Flask, render_template
from db import close_db, init_db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["DATABASE"] = os.path.join(app.instance_path, "em_os.db")
    os.makedirs(app.instance_path, exist_ok=True)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.route("/")
    def home():
        return render_template("home.html")

    return app


if __name__ == "__main__":
    create_app().run(debug=True)