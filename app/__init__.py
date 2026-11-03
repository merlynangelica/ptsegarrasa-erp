import os
from flask import Flask, jsonify, render_template, session
from dotenv import load_dotenv
from .db import init_db, seed_db

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
    app.config["DATABASE"] = os.getenv("DATABASE_PATH", "instance/segarrasa_erp.db")
    app.config["DEBUG"] = os.getenv("FLASK_DEBUG", "0") == "1"
    os.makedirs(os.path.dirname(app.config["DATABASE"]) or ".", exist_ok=True)
    init_db(app)
    with app.app_context():
        seed_db()
    from .routes import bp
    app.register_blueprint(bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/session")
    def current_session():
        if not session.get("user_id"):
            return jsonify({"authenticated": False})
        return jsonify({"authenticated": True, "user": session.get("user")})

    return app
