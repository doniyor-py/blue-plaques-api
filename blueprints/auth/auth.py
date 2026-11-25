"""Sign-up with email code, Google sign-in, password reset, login and logout."""
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import partial

import bcrypt
import jwt
from flask import Blueprint, request, make_response, jsonify, g
from pymongo.errors import DuplicateKeyError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from globals import SECRET_KEY, GOOGLE_CLIENT_ID, users
from decorators import jwt_required, blacklist_token
from extensions import limiter
from helpers import password_too_long
from otp import send_email, create_otp, check_otp, is_human

auth_bp = Blueprint("auth_bp", __name__)


def issue_token(user):
    """Our own 30-minute login token, used by both ways of logging in."""
    return jwt.encode({
        "username": user["username"],
        "admin": user.get("admin", False),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
    }, SECRET_KEY, algorithm="HS256")


def random_username():
    """A neutral public name like explorer40517, so it doesn't reveal the email address."""
    while True:
        name = f"explorer{secrets.randbelow(100000):05d}"
        if not users.find_one({"username": name}):
            return name


# checked when the user doesn't exist, so the reply takes as long as for a real account
DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


def email_in_form():
    return request.form.get("email", "").strip().lower()


def code_was_sent(response):
    return response.status_code == 200


@auth_bp.route("/api/v1.0/register", methods=["POST"])
@limiter.limit("30 per hour")
@limiter.limit("5 per hour", key_func=email_in_form, deduct_when=code_was_sent)
def register():
    username = request.form.get("username", "").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    accept_terms = request.form.get("accept_terms", "").lower()

    # 1. check the input (free, so it runs first)
    if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
        return make_response(jsonify({"error": "username must be 3-20 letters, numbers or _"}), 400)
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return make_response(jsonify({"error": "A valid email is required"}), 400)
    if len(password) < 8 or password_too_long(password):
        return make_response(jsonify({"error": "password must be 8-72 characters"}), 400)
    if accept_terms != "true":
        return make_response(jsonify({"error": "You must accept the terms of use and privacy policy"}), 400)

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
    code = create_otp(email, "register", {
        "username": username,
        "password": hashed,
        "terms_accepted_at": datetime.now(timezone.utc)      # proof of when they agreed
    })
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
    if not re.fullmatch(r"\d{6}", code):
        return make_response(jsonify({"error": "code must be 6 digits"}), 400)

    saved, error = check_otp(email, "register", code)
    if error:
        return make_response(jsonify({"error": error}), 400)

    try:
        users.insert_one({
            "username": saved["data"]["username"],
            "email": email,
            "password": saved["data"]["password"],
            "terms_accepted_at": saved["data"].get("terms_accepted_at"),
            "created_at": datetime.now(timezone.utc),
            "admin": False             # new accounts are never admins
        })
    except DuplicateKeyError:          # someone took the name while you were checking email
        return make_response(jsonify({"error": "Username or email already taken"}), 409)
    return make_response(jsonify({"message": "Account created, you can now log in"}), 201)


@auth_bp.route("/api/v1.0/password/forgot", methods=["POST"])
@limiter.limit("3 per hour")
@limiter.limit("3 per hour", key_func=email_in_form)
def forgot_password():
    email = request.form.get("email", "").strip().lower()
    if users.find_one({"email": email}):
        code = create_otp(email, "reset")
        send_email(email, "Your Blue Plaques password reset code",
                   f"Your code is {code}. It expires in 10 minutes. "
                   f"If you didn't ask for this, ignore this email.")
    return make_response(jsonify({
        "message": "If that email is registered, we've sent a 6-digit code."
    }), 200)


@auth_bp.route("/api/v1.0/password/reset", methods=["POST"])
@limiter.limit("10 per hour")
def reset_password():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()
    new_password = request.form.get("new_password", "")

    if not re.fullmatch(r"\d{6}", code):
        return make_response(jsonify({"error": "code must be 6 digits"}), 400)
    if len(new_password) < 8 or password_too_long(new_password):
        return make_response(jsonify({"error": "new_password must be 8-72 characters"}), 400)

    saved, error = check_otp(email, "reset", code)
    if error:
        return make_response(jsonify({"error": error}), 400)

    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())
    users.update_one({"email": email},
                     {"$set": {"password": hashed, "password_changed_at": datetime.now(timezone.utc)},
                      "$unset": {"password_set": ""}})
    return make_response(jsonify({"message": "Password reset, you can now log in"}), 200)


@auth_bp.route("/api/v1.0/login", methods=["GET"])
@limiter.limit("10 per minute")
def login():
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return make_response(jsonify({"error": "Username and password required (Basic Auth)"}), 401)

    identifier = auth.username.strip().lower()
    if "@" in identifier:
        user = users.find_one({"email": identifier})
    else:
        user = users.find_one({"username": identifier})

    stored_hash = user["password"] if user else DUMMY_HASH
    if (password_too_long(auth.password)
            or not bcrypt.checkpw(auth.password.encode("utf-8"), stored_hash)
            or user is None):
        return make_response(jsonify({"error": "Wrong email, username or password"}), 401)

    return make_response(jsonify({"token": issue_token(user)}), 200)


@auth_bp.route("/api/v1.0/login/google", methods=["POST"])
@limiter.limit("10 per minute")
def login_google():
    if not GOOGLE_CLIENT_ID:
        return make_response(jsonify({"error": "Google sign-in is not set up on this server"}), 503)

    # 1. ask Google's library to check the ID token: signature, our client ID (aud) and expiry.
    #    It downloads Google's certificates and would wait up to 120 seconds; 10 is enough.
    google_request = partial(google_requests.Request(), timeout=10)
    try:
        info = id_token.verify_oauth2_token(request.form.get("credential", ""),
                                            google_request, GOOGLE_CLIENT_ID)
    except ValueError:
        return make_response(jsonify({"error": "Google sign-in failed, please try again"}), 401)
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return make_response(jsonify({"error": "Google sign-in failed, please try again"}), 401)
    if not info.get("email_verified"):
        return make_response(jsonify({"error": "Your Google email address is not verified"}), 403)

    google_sub = info["sub"]                       # Google's permanent ID for this person
    email = info["email"].strip().lower()

    # 2. find the account: by Google ID first, then by email (links an existing account)
    user = users.find_one({"google_sub": google_sub})
    if user is None:
        user = users.find_one({"email": email})
        if user is not None:
            users.update_one({"_id": user["_id"]}, {"$set": {"google_sub": google_sub}})

    # 3. no account yet: create one (they agreed to the terms next to the Google button)
    if user is None:
        if request.form.get("accept_terms", "").lower() != "true":
            return make_response(jsonify({"error": "You must accept the terms of use and privacy policy"}), 400)
        random_password = secrets.token_urlsafe(32)          # nobody knows it; "Forgot password" can set a real one
        user = {
            "username": random_username(),
            "email": email,
            "password_set": False,
            "password": bcrypt.hashpw(random_password.encode("utf-8"), bcrypt.gensalt()),
            "google_sub": google_sub,
            "terms_accepted_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
            "admin": False
        }
        try:
            users.insert_one(user)
        except DuplicateKeyError:
            return make_response(jsonify({"error": "Please try again"}), 409)

    return make_response(jsonify({"token": issue_token(user)}), 200)


@auth_bp.route("/api/v1.0/logout", methods=["GET"])
@jwt_required
def logout():
    token = request.headers.get("x-access-token")
    blacklist_token(token, g.user["exp"])
    return make_response(jsonify({"message": "Logged out"}), 200)