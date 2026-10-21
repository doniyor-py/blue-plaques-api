"""Login checks used on protected routes."""
from datetime import timezone
from functools import wraps

import jwt
from flask import request, make_response, jsonify, g

from globals import SECRET_KEY, users, blacklist


def jwt_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get("x-access-token")
        if not token:
            return make_response(jsonify({"error": "Token is missing"}), 401)
        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return make_response(jsonify({"error": "Token has expired, please log in again"}), 401)
        except jwt.InvalidTokenError:
            return make_response(jsonify({"error": "Token is invalid"}), 401)
        if blacklist.find_one({"token": token}):
            return make_response(jsonify({"error": "Token has been logged out"}), 401)
        g.user = data            # who is logged in, available inside the route

        user = users.find_one({"username": data["username"]}, {"password_changed_at": 1})
        if user is None:
            return make_response(jsonify({"error": "Account no longer exists"}), 401)
        changed = user.get("password_changed_at")
        if changed:
            if changed.tzinfo is None:
                changed = changed.replace(tzinfo=timezone.utc)
            if data.get("iat", 0) < int(changed.timestamp()):
                return make_response(jsonify({"error": "Password was changed, please log in again"}), 401)
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not g.user.get("admin"):
            return make_response(jsonify({"error": "Admin access required"}), 403)
        return func(*args, **kwargs)
    return wrapper


def current_username():
    """Rate-limit key for logged-in routes: count per account, not per IP address."""
    return g.user["username"]
