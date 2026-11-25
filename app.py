"""London Blue Plaques Explorer API: creates the app and plugs in every Blueprint."""
import os

from flask import Flask, make_response, jsonify, request
from flask_cors import CORS
from globals import UPLOAD_FOLDER
from extensions import limiter
from blueprints.plaques.plaques import plaques_bp
from blueprints.visits.visits import visits_bp
from blueprints.photos.photos import photos_bp
from blueprints.auth.auth import auth_bp
from blueprints.users.users import users_bp
from blueprints.analytics.analytics import analytics_bp
from blueprints.reports.reports import reports_bp

app = Flask(__name__)
# only our own front end may call the API from a browser
CORS(app, origins=os.getenv("FRONTEND_ORIGINS", "http://localhost:4200,http://127.0.0.1:4200").split(","))
limiter.init_app(app)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024        # 5 MB upload limit

for blueprint in [plaques_bp, visits_bp, photos_bp, auth_bp, users_bp, analytics_bp, reports_bp]:
    app.register_blueprint(blueprint)


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    # answers to logged-in requests and logins hold personal data or a token: never store them
    if "x-access-token" in request.headers or request.path == "/api/v1.0/login":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(413)
def too_large(e):
    return make_response(jsonify({"error": "File is too large, maximum 5 MB"}), 413)


@app.errorhandler(429)
def too_many_requests(e):
    return make_response(jsonify({"error": "Too many requests, please try again later",
                                  "limit": str(e.description)}), 429)


@app.errorhandler(404)
def not_found(e):
    return make_response(jsonify({"error": "Route not found - check the URL"}), 404)


@app.errorhandler(405)
def method_not_allowed(e):
    return make_response(jsonify({"error": "Method not allowed on this route"}), 405)


@app.errorhandler(500)
def server_error(e):
    return make_response(jsonify({"error": "Something went wrong on the server"}), 500)


@app.route("/", methods=["GET"])
def index():
    return make_response(jsonify({
        "name": "London Blue Plaques Explorer API",
        "version": "1.0",
        "plaques": request.host_url + "api/v1.0/plaques",
        "search": request.host_url + "api/v1.0/plaques/search?q=dickens",
        "nearby": request.host_url + "api/v1.0/plaques/nearby?lat=51.508&lng=-0.128"
    }), 200)


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=5001)
