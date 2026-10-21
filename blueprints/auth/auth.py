"""Sign-up with email code, password reset, login and logout."""
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, request, make_response, jsonify
from pymongo.errors import DuplicateKeyError

from globals import SECRET_KEY, users, blacklist
from decorators import jwt_required
from extensions import limiter
from helpers import password_too_long
from otp import send_email, create_otp, check_otp, is_human

auth_bp = Blueprint("auth_bp", __name__)


@auth_bp.route("/api/v1.0/register", methods=["POST"])
@limiter.limit("5 per hour")
def register():
    username = request.form.get("username", "").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    # 1. check the input (free, so it runs first)
    if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
        return make_response(jsonify({"error": "username must be 3-20 letters, numbers or _"}), 400)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return make_response(jsonify({"error": "A valid email is required"}), 400)
    if len(password) < 8 or password_too_long(password):
        return make_response(jsonify({"error": "password must be 8-72 characters"}), 400)

    # 2. are you human? (asks Cloudflare, so only once the input is valid)
    if not is_human(request.form.get("captcha_token")):
        return make_response(jsonify({"error": "Human check failed, please try again"}), 400)

    # 3. is the name or email already used?
    if users.find_one({"username": username}):
        return make_response(jsonify({"error": "Username already taken"}), 409)
    if users.find_one({"email": email}):
        return make_response(jsonify({"error": "Email already registered"}), 409)

    # 4. remember the details with the code, and email the code
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    code = create_otp(email, "register", {"username": username, "password": hashed})
    send_email(email, "Your Blue Plaques sign-up code",
               f"Your code is {code}. It expires in 10 minutes.")

    return make_response(jsonify({
        "message": "We've emailed you a 6-digit code. Send it to /register/verify within 10 minutes."
    }), 200)


@auth_bp.route("/api/v1.0/register/verify", methods=["POST"])
@limiter.limit("10 per hour")
def register_verify():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()

    saved, error = check_otp(email, "register", code)
    if error:
        return make_response(jsonify({"error": error}), 400)

    try:
        users.insert_one({
            "username": saved["data"]["username"],
            "email": email,
            "password": saved["data"]["password"],
            "admin": False             # new accounts are never admins
        })
    except DuplicateKeyError:          # someone took the name while you were checking email
        return make_response(jsonify({"error": "Username or email already taken"}), 409)
    return make_response(jsonify({"message": "Account created, you can now log in"}), 201)


@auth_bp.route("/api/v1.0/password/forgot", methods=["POST"])
@limiter.limit("3 per hour")
def forgot_password():
    email = request.form.get("email", "").strip().lower()
    if users.find_one({"email": email}):
        code = create_otp(email, "reset")
        send_email(email, "Your Blue Plaques password reset code",
                   f"Your code is {code}. It expires in 10 minutes. "
                   f"If you didn't ask for this, ignore this email.")
    # same answer either way, so nobody can find out which emails have accounts
    return make_response(jsonify({
        "message": "If that email is registered, we've sent a 6-digit code."
    }), 200)


@auth_bp.route("/api/v1.0/password/reset", methods=["POST"])
@limiter.limit("10 per hour")
def reset_password():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()
    new_password = request.form.get("new_password", "")

    if len(new_password) < 8 or password_too_long(new_password):
        return make_response(jsonify({"error": "new_password must be 8-72 characters"}), 400)

    saved, error = check_otp(email, "reset", code)
    if error:
        return make_response(jsonify({"error": error}), 400)

    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())
    users.update_one({"email": email},
                     {"$set": {"password": hashed, "password_changed_at": datetime.now(timezone.utc)}})
    return make_response(jsonify({"message": "Password reset, you can now log in"}), 200)


@auth_bp.route("/api/v1.0/login", methods=["GET"])
@limiter.limit("10 per minute")
def login():
    auth = request.authorization        
    if not auth or not auth.username or not auth.password:
        return make_response(jsonify({"error": "Username and password required (Basic Auth)"}), 401)

    user = users.find_one({"username": auth.username.strip().lower()})
    if (user is None or password_too_long(auth.password)
            or not bcrypt.checkpw(auth.password.encode("utf-8"), user["password"])):
        return make_response(jsonify({"error": "Wrong username or password"}), 401)

    token = jwt.encode({
        "username": user["username"],
        "admin": user.get("admin", False),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
    }, SECRET_KEY, algorithm="HS256")
    return make_response(jsonify({"token": token}), 200)


@auth_bp.route("/api/v1.0/logout", methods=["GET"])
@jwt_required
def logout():
    token = request.headers.get("x-access-token")
    blacklist.insert_one({"token": token})
    return make_response(jsonify({"message": "Logged out"}), 200)
