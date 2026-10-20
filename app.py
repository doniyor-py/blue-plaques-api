from flask import Flask, make_response, jsonify, request, g
from flask_cors import CORS
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from dotenv import load_dotenv
import os
import re
import jwt
import bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import math
import secrets
import smtplib
import requests
from email.message import EmailMessage

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise SystemExit("SECRET_KEY missing: create a .env file (see instructions)")

TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET")          # Cloudflare human check
EMAIL_MODE = os.getenv("EMAIL_MODE", "console")           # "console" (CW1) or "smtp" (CW2)
SMTP_USER = os.getenv("SMTP_USER")                        # only needed for "smtp"
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

app = Flask(__name__)
CORS(app)

limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


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

# MongoDB connection
client = MongoClient("mongodb://localhost:27017")
db = client.plaquesDB
plaques = db.plaques
users = db.users
blacklist = db.blacklist

reports = db.reports

otps = db.otps

# MongoDB deletes expired codes automatically (TTL index)
otps.create_index("expires_at", expireAfterSeconds=0)

users.create_index("username", unique=True)
users.create_index("email", unique=True)

plaques.create_index([("location", "2dsphere")])
plaques.create_index([("views", -1), ("plaque_ref", 1)])     # new: most-viewed sort

ALLOWED_COLOURS = [
    "belgian blue stone", "black", "blue", "brass", "bronze", "brown",
    "brushed metal", "claret", "clear", "film cell", "gold", "green",
    "green and red", "grey", "marble", "maroon", "multicoloured", "orange",
    "pink", "purple", "red", "red and black", "slate", "stone",
    "terracotta", "unknown", "white", "wood", "yellow"
]


def get_pagination():
    try:
        page = int(request.args.get("pn", 1))
        size = int(request.args.get("ps", 10))
    except ValueError:
        return None, None, make_response(jsonify({"error": "pn and ps must be whole numbers"}), 400)
    if page < 1:
        return None, None, make_response(jsonify({"error": "pn must be 1 or more"}), 400)
    if size < 1 or size > 100:
        return None, None, make_response(jsonify({"error": "ps must be between 1 and 100"}), 400)
    return page, size, None


def clean_plaque(plaque):
    plaque["_id"] = str(plaque["_id"])
    for visit in plaque.get("visits", []):
        visit["_id"] = str(visit["_id"])
    return plaque



# ===================== EMAIL, OTP AND HUMAN CHECK HELPERS =====================

def send_email(to, subject, body):
    """Console mode prints the email in the terminal; smtp mode really sends it via Gmail."""
    if EMAIL_MODE == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        print("\n" + "=" * 50)
        print("EMAIL TO:", to)
        print("SUBJECT: ", subject)
        print(body)
        print("=" * 50 + "\n", flush=True)


def create_otp(email, purpose, data=None):
    """Make a 6-digit code, save its HASH for 10 minutes, and return the code."""
    code = f"{secrets.randbelow(1000000):06d}"           # 000000 - 999999
    otps.delete_many({"email": email, "purpose": purpose})   # only the newest code counts
    otps.insert_one({
        "email": email,
        "purpose": purpose,                               # "register" or "reset"
        "code": bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "attempts": 0,
        "data": data or {}
    })
    return code


def check_otp(email, purpose, code):
    """Return (saved_otp, None) if the code is right, otherwise (None, error message)."""
    saved = otps.find_one({"email": email, "purpose": purpose})
    if saved is None:
        return None, "No code found - request a new one"

    expires = saved["expires_at"]
    if expires.tzinfo is None:                            # MongoDB returns times without a timezone
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        otps.delete_one({"_id": saved["_id"]})
        return None, "Code has expired - request a new one"

    if not bcrypt.checkpw(code.encode("utf-8"), saved["code"]):
        tries_left = 4 - saved["attempts"]
        if tries_left <= 0:
            otps.delete_one({"_id": saved["_id"]})
            return None, "Too many wrong codes - request a new one"
        otps.update_one({"_id": saved["_id"]}, {"$inc": {"attempts": 1}})
        return None, f"Wrong code, {tries_left} tries left"

    otps.delete_one({"_id": saved["_id"]})                # a code works only once
    return saved, None


def is_human(captcha_token):
    """Ask Cloudflare Turnstile whether the captcha token is genuine."""
    if not captcha_token:
        return False
    try:
        reply = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET,
                  "response": captcha_token,
                  "remoteip": request.remote_addr},
            timeout=5)
        return reply.json().get("success", False)
    except requests.RequestException:
        return False



# ===================== AUTH DECORATORS =====================

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


@app.route("/", methods=["GET"])
def index():
    return make_response("<h1>Blue Plaques Explorer API</h1>", 200)


@app.route("/api/v1.0/plaques", methods=["GET"])
def show_all_plaques():
    page, size, error = get_pagination()
    if error:
        return error

    total = plaques.count_documents({})
    data_to_return = []
    for plaque in plaques.find().sort("plaque_ref", 1).skip((page - 1) * size).limit(size):
        data_to_return.append(clean_plaque(plaque))

    return make_response(jsonify({
        "total": total,
        "page": page,
        "page_size": size,
        "pages": math.ceil(total / size),
        "plaques": data_to_return
    }), 200)


@app.route("/api/v1.0/plaques/<string:id>", methods=["GET"])
def show_one_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    plaque = plaques.find_one_and_update(
        {"_id": ObjectId(id)},
        {"$inc": {"views": 1}},
        return_document=ReturnDocument.AFTER
    )

    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    return make_response(jsonify(clean_plaque(plaque)), 200)


@app.route("/api/v1.0/plaques", methods=["POST"])
@jwt_required
@admin_required
def add_plaque():
    required = ["inscription", "colour", "erected_year", "organisation",
                "address", "borough", "lat", "lng"]
    missing = [field for field in required if not request.form.get(field)]
    if missing:
        return make_response(jsonify({"error": "Missing fields: " + ", ".join(missing)}), 400)

    try:
        erected_year = int(request.form["erected_year"])
        lat = float(request.form["lat"])
        lng = float(request.form["lng"])
    except ValueError:
        return make_response(jsonify({"error": "erected_year must be a whole number, lat and lng must be numbers"}), 400)

    if erected_year < 1800 or erected_year > 2026:
        return make_response(jsonify({"error": "erected_year must be between 1800 and 2026"}), 400)

    if request.form["colour"].lower() not in ALLOWED_COLOURS:
        return make_response(jsonify({"error": "colour must be one of: " + ", ".join(ALLOWED_COLOURS)}), 400)
    
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return make_response(jsonify({"error": "lat must be -90 to 90, lng must be -180 to 180"}), 400)

    last = plaques.find_one(sort=[("plaque_ref", -1)])
    next_ref = last["plaque_ref"] + 1 if last else 1
    colour = request.form["colour"].lower()
    title = request.form.get("title") or f"{colour.capitalize()} plaque at {request.form['address']}"
    category = request.form.get("category", "other").lower()

    new_plaque = {
        "plaque_ref": next_ref,
        "title": title, 
        "inscription": request.form["inscription"],
        "colour": request.form["colour"].lower(),
        "erected_year": erected_year,
        "organisation": request.form["organisation"],
        "address": request.form["address"],
        "borough": request.form["borough"],
        "category": category,
        "location": {"type": "Point", "coordinates": [lng, lat]},
        "views": 0,
        "people": [],
        "photos": [],
        "visits": []
    }

    new_id = plaques.insert_one(new_plaque).inserted_id
    new_link = "http://127.0.0.1:5001/api/v1.0/plaques/" + str(new_id)
    return make_response(jsonify({"url": new_link}), 201)


@app.route("/api/v1.0/plaques/<string:id>", methods=["PUT"])
@jwt_required
@admin_required
def edit_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    changes = {}
    for field in ["title", "inscription", "colour", "organisation", "address", "borough", "category"]:
        if request.form.get(field):
            changes[field] = request.form[field]

    if "colour" in changes:
        changes["colour"] = changes["colour"].lower()
        if changes["colour"] not in ALLOWED_COLOURS:
            return make_response(jsonify({"error": "colour must be one of: " + ", ".join(ALLOWED_COLOURS)}), 400)

    if request.form.get("erected_year"):
        try:
            erected_year = int(request.form["erected_year"])
        except ValueError:
            return make_response(jsonify({"error": "erected_year must be a whole number"}), 400)
        if erected_year < 1800 or erected_year > 2026:
            return make_response(jsonify({"error": "erected_year must be between 1800 and 2026"}), 400)
        changes["erected_year"] = erected_year

    if request.form.get("lat") or request.form.get("lng"):
        try:
            lat = float(request.form["lat"])
            lng = float(request.form["lng"])
        except (KeyError, ValueError):
            return make_response(jsonify({"error": "Send both lat and lng as numbers"}), 400)
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return make_response(jsonify({"error": "lat must be -90 to 90, lng must be -180 to 180"}), 400)
        changes["location"] = {"type": "Point", "coordinates": [lng, lat]}

    if not changes:
        return make_response(jsonify({"error": "No fields to update"}), 400)

    result = plaques.update_one({"_id": ObjectId(id)}, {"$set": changes})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    edited_link = "http://127.0.0.1:5001/api/v1.0/plaques/" + id
    return make_response(jsonify({"url": edited_link}), 200)


@app.route("/api/v1.0/plaques/<string:id>", methods=["DELETE"])
@jwt_required
@admin_required
def delete_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    result = plaques.delete_one({"_id": ObjectId(id)})
    if result.deleted_count == 0:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    return make_response(jsonify({}), 204)


@app.route("/api/v1.0/plaques/most-viewed", methods=["GET"])
def most_viewed():
    try:
        limit = int(request.args.get("limit", 10))
    except ValueError:
        return make_response(jsonify({"error": "limit must be a whole number"}), 400)
    if limit < 1 or limit > 50:
        return make_response(jsonify({"error": "limit must be between 1 and 50"}), 400)

    fields = {"plaque_ref": 1, "title": 1, "inscription": 1, "borough": 1,
              "colour": 1, "location": 1, "views": 1}

    data_to_return = []
    for plaque in plaques.find({}, fields).sort([("views", -1), ("plaque_ref", 1)]).limit(limit):
        data_to_return.append(clean_plaque(plaque))
    return make_response(jsonify(data_to_return), 200)



# ===================== AUTH ROUTES =====================

@app.route("/api/v1.0/register", methods=["POST"])
@limiter.limit("5 per hour")
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    # 1. are you human?
    if not is_human(request.form.get("captcha_token")):
        return make_response(jsonify({"error": "Human check failed, please try again"}), 400)

    # 2. check the input
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return make_response(jsonify({"error": "username must be 3-20 letters, numbers or _"}), 400)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return make_response(jsonify({"error": "A valid email is required"}), 400)
    if len(password) < 8:
        return make_response(jsonify({"error": "password must be at least 8 characters"}), 400)
    if users.find_one({"username": username}):
        return make_response(jsonify({"error": "Username already taken"}), 409)
    if users.find_one({"email": email}):
        return make_response(jsonify({"error": "Email already registered"}), 409)

    # 3. remember the details with the code, and email the code
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    code = create_otp(email, "register", {"username": username, "password": hashed})
    send_email(email, "Your Blue Plaques sign-up code",
               f"Your code is {code}. It expires in 10 minutes.")

    return make_response(jsonify({
        "message": "We've emailed you a 6-digit code. Send it to /register/verify within 10 minutes."
    }), 200)

@app.route("/api/v1.0/register/verify", methods=["POST"])
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



# ===================== FORGOT PASSWORD =====================

@app.route("/api/v1.0/password/forgot", methods=["POST"])
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


@app.route("/api/v1.0/password/reset", methods=["POST"])
@limiter.limit("10 per hour")
def reset_password():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()
    new_password = request.form.get("new_password", "")

    if len(new_password) < 8:
        return make_response(jsonify({"error": "new_password must be at least 8 characters"}), 400)

    saved, error = check_otp(email, "reset", code)
    if error:
        return make_response(jsonify({"error": error}), 400)

    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())
    users.update_one({"email": email},
                     {"$set": {"password": hashed, "password_changed_at": datetime.now(timezone.utc)}})
    return make_response(jsonify({"message": "Password reset, you can now log in"}), 200)



@app.route("/api/v1.0/login", methods=["GET"])
@limiter.limit("10 per minute")
def login():
    auth = request.authorization        
    if not auth or not auth.username or not auth.password:
        return make_response(jsonify({"error": "Username and password required (Basic Auth)"}), 401)

    user = users.find_one({"username": auth.username})
    if user is None or not bcrypt.checkpw(auth.password.encode("utf-8"), user["password"]):
        return make_response(jsonify({"error": "Wrong username or password"}), 401)

    token = jwt.encode({
        "username": user["username"],
        "admin": user.get("admin", False),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
    }, SECRET_KEY, algorithm="HS256")
    return make_response(jsonify({"token": token}), 200)


@app.route("/api/v1.0/logout", methods=["GET"])
@jwt_required
def logout():
    token = request.headers.get("x-access-token")
    blacklist.insert_one({"token": token})
    return make_response(jsonify({"message": "Logged out"}), 200)


@app.route("/api/v1.0/users/me", methods=["DELETE"])
@jwt_required
def delete_my_account():
    user = users.find_one({"username": g.user["username"]})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)
    if user.get("admin"):
        return make_response(jsonify({"error": "Admin accounts cannot be deleted here"}), 403)

    password = request.form.get("password", "")
    if not bcrypt.checkpw(password.encode("utf-8"), user["password"]):
        return make_response(jsonify({"error": "Password is incorrect"}), 401)

    remove_user_and_data(user["username"])
    blacklist.insert_one({"token": request.headers.get("x-access-token")})  
    return make_response(jsonify({}), 204)


@app.route("/api/v1.0/users/<string:username>", methods=["DELETE"])
@jwt_required
@admin_required
def admin_delete_user(username):
    user = users.find_one({"username": username})
    if user is None:
        return make_response(jsonify({"error": "User not found"}), 404)
    if user.get("admin"):
        return make_response(jsonify({"error": "Admin accounts cannot be deleted"}), 403)

    remove_user_and_data(username)
    return make_response(jsonify({}), 204)


def remove_user_and_data(username):
    users.delete_one({"username": username})
    plaques.update_many({}, {"$pull": {"visits": {"username": username}}})


# ===================== ACCOUNT =====================

@app.route("/api/v1.0/users/me", methods=["GET"])
@jwt_required
def my_profile():
    user = users.find_one({"username": g.user["username"]},
                          {"_id": 0, "password": 0})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)
    return make_response(jsonify(user), 200)

@app.route("/api/v1.0/users/me/password", methods=["PUT"])
@jwt_required
def change_password():
    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    user = users.find_one({"username": g.user["username"]})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)
    if not bcrypt.checkpw(old_password.encode("utf-8"), user["password"]):
        return make_response(jsonify({"error": "Old password is incorrect"}), 401)
    if len(new_password) < 8:
        return make_response(jsonify({"error": "new_password must be at least 8 characters"}), 400)
    if new_password == old_password:
        return make_response(jsonify({"error": "New password must be different from the old one"}), 400)

    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())
    users.update_one({"username": g.user["username"]},
                     {"$set": {"password": hashed, "password_changed_at": datetime.now(timezone.utc)}})

    # log out this token, so the user must log in again with the new password
    blacklist.insert_one({"token": request.headers.get("x-access-token")})
    return make_response(jsonify({"message": "Password changed, please log in again"}), 200)


@app.route("/api/v1.0/users", methods=["GET"])
@jwt_required
@admin_required
def list_users():
    page, size, error = get_pagination()
    if error:
        return error

    total = users.count_documents({})
    data_to_return = list(users.find({}, {"_id": 0, "password": 0})
                          .sort("username", 1).skip((page - 1) * size).limit(size))
    return make_response(jsonify({
        "total": total,
        "page": page,
        "pages": math.ceil(total / size),
        "users": data_to_return
    }), 200)


# ===================== GEO =====================

@app.route("/api/v1.0/plaques/nearby", methods=["GET"])
def plaques_nearby():
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError):
        return make_response(jsonify({"error": "lat and lng are required numbers, e.g. ?lat=51.53&lng=-0.12"}), 400)
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return make_response(jsonify({"error": "lat must be -90 to 90, lng must be -180 to 180"}), 400)

    try:
        radius = int(request.args.get("radius", 1000))
        limit = int(request.args.get("limit", 10))
    except ValueError:
        return make_response(jsonify({"error": "radius and limit must be whole numbers"}), 400)
    if radius < 1 or radius > 50000:
        return make_response(jsonify({"error": "radius must be between 1 and 50000 metres"}), 400)
    if limit < 1 or limit > 100:
        return make_response(jsonify({"error": "limit must be between 1 and 100"}), 400)

    pipeline = [
        {"$geoNear": {
            "near": {"type": "Point", "coordinates": [lng, lat]},
            "distanceField": "distance_m",
            "maxDistance": radius,
            "spherical": True
        }},
        {"$limit": limit},
        {"$project": {
            "inscription": 1, "borough": 1, "colour": 1, "location": 1, "views": 1,
            "distance_m": {"$round": ["$distance_m", 0]},
            "walking_minutes": {"$ceil": {"$divide": ["$distance_m", 80]}}
        }}
    ]

    data_to_return = []
    for plaque in plaques.aggregate(pipeline):
        plaque["_id"] = str(plaque["_id"])
        data_to_return.append(plaque)

    return make_response(jsonify(data_to_return), 200)


# ===================== VISITS (sub-documents) =====================

def check_ratings(form):
    ratings = {}
    for name in ["history", "condition", "accessibility"]:
        try:
            value = int(form[name])
        except (KeyError, ValueError):
            return None, name + " must be a whole number from 1 to 5"
        if value < 1 or value > 5:
            return None, name + " must be between 1 and 5"
        ratings[name] = value
    return ratings, None


def visit_owner_check(id, vid):
    plaque = plaques.find_one({"_id": ObjectId(id)}, {"visits": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)
    for visit in plaque["visits"]:
        if visit["_id"] == ObjectId(vid):
            if visit["username"] != g.user["username"] and not g.user.get("admin"):
                return make_response(jsonify({"error": "You can only change your own visits"}), 403)
            return None     
    return make_response(jsonify({"error": "Visit not found"}), 404)


@app.route("/api/v1.0/plaques/<string:id>/visits", methods=["GET"])
def get_all_visits(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    plaque = plaques.find_one({"_id": ObjectId(id)}, {"visits": 1, "_id": 0})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    for visit in plaque["visits"]:
        visit["_id"] = str(visit["_id"])
    return make_response(jsonify(plaque["visits"]), 200)


@app.route("/api/v1.0/plaques/<string:id>/visits", methods=["POST"])
@jwt_required
def add_visit(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    ratings, error = check_ratings(request.form)
    if error:
        return make_response(jsonify({"error": error}), 400)

    today = date.today().isoformat()
    already = plaques.find_one({
        "_id": ObjectId(id),
        "visits": {"$elemMatch": {"username": g.user["username"], "date": today}}
    })
    if already:
        return make_response(jsonify({"error": "You already checked in here today"}), 409)

    new_visit = {
        "_id": ObjectId(),
        "username": g.user["username"],
        "date": today,
        "note": request.form.get("note", ""),
        "ratings": ratings
    }

    result = plaques.update_one({"_id": ObjectId(id)}, {"$push": {"visits": new_visit}})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    new_link = "http://127.0.0.1:5001/api/v1.0/plaques/" + id + "/visits/" + str(new_visit["_id"])
    return make_response(jsonify({"url": new_link}), 201)


@app.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["GET"])
def get_one_visit(id, vid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(vid):
        return make_response(jsonify({"error": "Invalid plaque or visit ID"}), 400)

    plaque = plaques.find_one({"_id": ObjectId(id)}, {"visits": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    for visit in plaque["visits"]:
        if visit["_id"] == ObjectId(vid):
            visit["_id"] = str(visit["_id"])
            return make_response(jsonify(visit), 200)

    return make_response(jsonify({"error": "Visit not found"}), 404)


@app.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["PUT"])
@jwt_required
def edit_visit(id, vid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(vid):
        return make_response(jsonify({"error": "Invalid plaque or visit ID"}), 400)
    error = visit_owner_check(id, vid)
    if error:
        return error

    ratings, error = check_ratings(request.form)
    if error:
        return make_response(jsonify({"error": error}), 400)

    changes = {"visits.$.ratings": ratings}
    if "note" in request.form:
        changes["visits.$.note"] = request.form["note"]

    result = plaques.update_one({"_id": ObjectId(id), "visits._id": ObjectId(vid)},
                                {"$set": changes})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque or visit not found"}), 404)

    edited_link = "http://127.0.0.1:5001/api/v1.0/plaques/" + id + "/visits/" + vid
    return make_response(jsonify({"url": edited_link}), 200)


@app.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["DELETE"])
@jwt_required
def delete_visit(id, vid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(vid):
        return make_response(jsonify({"error": "Invalid plaque or visit ID"}), 400)
    error = visit_owner_check(id, vid)
    if error:
        return error

    result = plaques.update_one({"_id": ObjectId(id)},
                                {"$pull": {"visits": {"_id": ObjectId(vid)}}})
    if result.modified_count == 0:
        return make_response(jsonify({"error": "Plaque or visit not found"}), 404)

    return make_response(jsonify({}), 204)


# ===================== ANALYTICS =====================
@app.route("/api/v1.0/analytics/boroughs", methods=["GET"])
def borough_stats():
    pipeline = [
        {"$group": {
            "_id": "$borough",
            "plaques": {"$sum": 1},
            "total_views": {"$sum": "$views"},
            "avg_views": {"$avg": "$views"}
        }},
        {"$sort": {"plaques": -1}},
        {"$project": {
            "_id": 0,
            "borough": "$_id",
            "plaques": 1,
            "total_views": 1,
            "avg_views": {"$round": ["$avg_views", 1]}
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@app.route("/api/v1.0/analytics/ratings", methods=["GET"])
def rating_stats():
    pipeline = [
        {"$unwind": "$visits"},
        {"$group": {
            "_id": "$borough",
            "visits": {"$sum": 1},
            "avg_history": {"$avg": "$visits.ratings.history"},
            "avg_condition": {"$avg": "$visits.ratings.condition"},
            "avg_accessibility": {"$avg": "$visits.ratings.accessibility"}
        }},
        {"$project": {
            "_id": 0,
            "borough": "$_id",
            "visits": 1,
            "avg_history": {"$round": ["$avg_history", 2]},
            "avg_condition": {"$round": ["$avg_condition", 2]},
            "avg_accessibility": {"$round": ["$avg_accessibility", 2]},
            "avg_overall": {"$round": [
                {"$avg": ["$avg_history", "$avg_condition", "$avg_accessibility"]}, 2
            ]}
        }},
        {"$sort": {"avg_overall": -1}}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@app.route("/api/v1.0/users/me/stats", methods=["GET"])
@jwt_required
def my_stats():
    pipeline = [
        {"$unwind": "$visits"},
        {"$match": {"visits.username": g.user["username"]}},
        {"$group": {
            "_id": None,
            "visits": {"$sum": 1},
            "avg_rating_given": {"$avg": {"$avg": [
                "$visits.ratings.history",
                "$visits.ratings.condition",
                "$visits.ratings.accessibility"
            ]}},
            "boroughs": {"$addToSet": "$borough"}
        }},
        {"$project": {
            "_id": 0,
            "visits": 1,
            "avg_rating_given": {"$round": ["$avg_rating_given", 2]},
            "boroughs": 1,
            "boroughs_visited": {"$size": "$boroughs"}
        }}
    ]
    results = list(plaques.aggregate(pipeline))

    if not results:
        return make_response(jsonify({"visits": 0, "avg_rating_given": None,
                                      "boroughs": [], "boroughs_visited": 0}), 200)
    return make_response(jsonify(results[0]), 200)

@app.route("/api/v1.0/analytics/decades", methods=["GET"])
def decade_stats():
    pipeline = [
        {"$match": {"erected_year": {"$ne": None}}},
        {"$group": {
            "_id": {"$multiply": [
                {"$floor": {"$divide": ["$erected_year", 10]}},
                10
            ]},
            "plaques": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id": 0,
            "decade": "$_id",
            "plaques": 1
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@app.route("/api/v1.0/analytics/top-rated", methods=["GET"])
def top_rated():
    try:
        min_visits = int(request.args.get("min_visits", 3))
        limit = int(request.args.get("limit", 10))
    except ValueError:
        return make_response(jsonify({"error": "min_visits and limit must be whole numbers"}), 400)
    if min_visits < 1 or limit < 1 or limit > 50:
        return make_response(jsonify({"error": "min_visits must be 1+, limit must be 1-50"}), 400)

    pipeline = [
        {"$unwind": "$visits"},
        {"$group": {
            "_id": "$_id",
            "inscription": {"$first": "$inscription"},
            "borough": {"$first": "$borough"},
            "visits": {"$sum": 1},
            "avg_rating": {"$avg": {"$avg": [
                "$visits.ratings.history",
                "$visits.ratings.condition",
                "$visits.ratings.accessibility"
            ]}}
        }},
        {"$match": {"visits": {"$gte": min_visits}}},
        {"$sort": {"avg_rating": -1, "visits": -1}},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "plaque_id": {"$toString": "$_id"},
            "inscription": 1,
            "borough": 1,
            "visits": 1,
            "avg_rating": {"$round": ["$avg_rating", 2]}
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@app.route("/api/v1.0/analytics/professions", methods=["GET"])
def profession_stats():
    pipeline = [
        {"$unwind": "$people"},
        {"$unwind": "$people.roles"},
        {"$group": {"_id": "$people.roles", "people": {"$sum": 1}}},
        {"$sort": {"people": -1}},
        {"$project": {"_id": 0, "role": "$_id", "people": 1}}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@app.route("/api/v1.0/analytics/ages", methods=["GET"])
def age_stats():
    pipeline = [
        {"$unwind": "$people"},
        {"$match": {"people.born": {"$ne": None}, "people.died": {"$ne": None}}},
        {"$project": {"age": {"$subtract": ["$people.died", "$people.born"]}}},
        {"$group": {
            "_id": None,
            "people": {"$sum": 1},
            "avg_age": {"$avg": "$age"},
            "youngest": {"$min": "$age"},
            "oldest": {"$max": "$age"}
        }},
        {"$project": {
            "_id": 0,
            "people": 1,
            "avg_age": {"$round": ["$avg_age", 1]},
            "youngest": 1,
            "oldest": 1
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    if not results:
        return make_response(jsonify({"people": 0}), 200)
    return make_response(jsonify(results[0]), 200)


@app.route("/api/v1.0/users/me/progress", methods=["GET"])
@jwt_required
def my_progress():
    username = g.user["username"]
    pipeline = [
        {"$group": {
            "_id": "$borough",
            "total": {"$sum": 1},
            "visited": {"$sum": {"$cond": [
                {"$in": [username, "$visits.username"]}, 1, 0
            ]}}
        }},
        {"$project": {
            "_id": 0,
            "borough": "$_id",
            "total": 1,
            "visited": 1,
            "percent": {"$round": [
                {"$multiply": [{"$divide": ["$visited", "$total"]}, 100]}, 1
            ]}
        }},
        {"$sort": {"percent": -1}}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


# ===================== FAVOURITES =====================

@app.route("/api/v1.0/users/me/favourites/<string:id>", methods=["POST"])
@jwt_required
def add_favourite(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)
    if plaques.find_one({"_id": ObjectId(id)}, {"_id": 1}) is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    result = users.update_one({"username": g.user["username"]},
                              {"$addToSet": {"favourites": ObjectId(id)}})
    if result.modified_count == 0:
        return make_response(jsonify({"error": "Already in your favourites"}), 409)
    return make_response(jsonify({"message": "Added to favourites"}), 201)


@app.route("/api/v1.0/users/me/favourites", methods=["GET"])
@jwt_required
def get_favourites():
    user = users.find_one({"username": g.user["username"]}, {"favourites": 1})
    ids = user.get("favourites", []) if user else []

    data_to_return = []
    for plaque in plaques.find({"_id": {"$in": ids}},
                               {"inscription": 1, "borough": 1, "colour": 1, "location": 1, "views": 1}):
        plaque["_id"] = str(plaque["_id"])
        data_to_return.append(plaque)
    return make_response(jsonify(data_to_return), 200)


@app.route("/api/v1.0/users/me/favourites/<string:id>", methods=["DELETE"])
@jwt_required
def remove_favourite(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    result = users.update_one({"username": g.user["username"]},
                              {"$pull": {"favourites": ObjectId(id)}})
    if result.modified_count == 0:
        return make_response(jsonify({"error": "Not in your favourites"}), 404)
    return make_response(jsonify({}), 204)


# ===================== RANDOM PLAQUE =====================

@app.route("/api/v1.0/plaques/random", methods=["GET"])
def random_plaque():
    pipeline = []
    if request.args.get("borough"):
        pipeline.append({"$match": {"borough": request.args["borough"]}})
    pipeline.append({"$sample": {"size": 1}})

    results = list(plaques.aggregate(pipeline))
    if not results:
        return make_response(jsonify({"error": "No plaques found"}), 404)
    return make_response(jsonify(clean_plaque(results[0])), 200)

# ===================== REPORT A PROBLEM =====================

REPORT_TYPES = ["missing", "damaged", "wrong_info", "other"]
REPORT_STATUSES = ["open", "in_progress", "resolved"]


def clean_report(report):
    report["_id"] = str(report["_id"])
    report["plaque_id"] = str(report["plaque_id"])
    report["created"] = report["created"].isoformat()
    return report


@app.route("/api/v1.0/plaques/<string:id>/reports", methods=["POST"])
@jwt_required
def report_problem(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    report_type = request.form.get("type", "")
    message = request.form.get("message", "").strip()
    if report_type not in REPORT_TYPES:
        return make_response(jsonify({"error": "type must be one of: " + ", ".join(REPORT_TYPES)}), 400)
    if len(message) < 5 or len(message) > 500:
        return make_response(jsonify({"error": "message must be 5-500 characters"}), 400)
    if plaques.find_one({"_id": ObjectId(id)}, {"_id": 1}) is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    new_id = reports.insert_one({
        "plaque_id": ObjectId(id),
        "username": g.user["username"],
        "type": report_type,
        "message": message,
        "status": "open",
        "created": datetime.now(timezone.utc)
    }).inserted_id
    return make_response(jsonify({"message": "Thanks, an admin will check it", "report_id": str(new_id)}), 201)


@app.route("/api/v1.0/reports", methods=["GET"])
@jwt_required
@admin_required
def list_reports():
    page, size, error = get_pagination()
    if error:
        return error

    query = {}
    status = request.args.get("status")
    if status:
        if status not in REPORT_STATUSES:
            return make_response(jsonify({"error": "status must be one of: " + ", ".join(REPORT_STATUSES)}), 400)
        query["status"] = status

    total = reports.count_documents(query)
    data_to_return = [clean_report(r) for r in
                      reports.find(query).sort("created", -1).skip((page - 1) * size).limit(size)]
    return make_response(jsonify({
        "total": total,
        "page": page,
        "pages": math.ceil(total / size),
        "reports": data_to_return
    }), 200)


@app.route("/api/v1.0/reports/<string:rid>", methods=["PUT"])
@jwt_required
@admin_required
def update_report(rid):
    if not ObjectId.is_valid(rid):
        return make_response(jsonify({"error": "Invalid report ID"}), 400)
    status = request.form.get("status", "")
    if status not in REPORT_STATUSES:
        return make_response(jsonify({"error": "status must be one of: " + ", ".join(REPORT_STATUSES)}), 400)

    result = reports.update_one({"_id": ObjectId(rid)}, {"$set": {"status": status}})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Report not found"}), 404)
    return make_response(jsonify({"message": "Report status updated to " + status}), 200)



# ===================== SEARCH AND FILTERS =====================

def split_list(value):
    """Turn "music, Literature" into ["music", "literature"]."""
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def build_search_query(args):
    """Build a MongoDB query from the URL parameters. Returns (query, error)."""
    query = {}

    # 1. free text: title, inscription or a person's name
    if args.get("q"):
        words = re.escape(args["q"].strip())
        query["$or"] = [
            {"title": {"$regex": words, "$options": "i"}},
            {"inscription": {"$regex": words, "$options": "i"}},
            {"people.name": {"$regex": words, "$options": "i"}}
        ]

    # 2. borough(s), any capitalisation: "camden" finds "Camden"
    if args.get("borough"):
        names = [b.strip() for b in args["borough"].split(",") if b.strip()]
        query["borough"] = {"$in": [re.compile("^" + re.escape(n) + "$", re.IGNORECASE) for n in names]}

    # 3. colour(s), checked against the allowed list
    if args.get("colour"):
        colours = split_list(args["colour"])
        bad = [c for c in colours if c not in ALLOWED_COLOURS]
        if bad:
            return None, "Unknown colour(s): " + ", ".join(bad)
        query["colour"] = {"$in": colours}

    # 4. category / categories
    if args.get("category"):
        query["category"] = {"$in": split_list(args["category"])}

    # 5. erected between two years
    years = {}
    try:
        if args.get("from_year"):
            years["$gte"] = int(args["from_year"])
        if args.get("to_year"):
            years["$lte"] = int(args["to_year"])
    except ValueError:
        return None, "from_year and to_year must be whole numbers"
    if "$gte" in years and "$lte" in years and years["$gte"] > years["$lte"]:
        return None, "from_year must not be after to_year"
    if years:
        query["erected_year"] = years

    # 6. someone on the plaque was alive in this year (same person: $elemMatch)
    if args.get("alive_in"):
        try:
            year = int(args["alive_in"])
        except ValueError:
            return None, "alive_in must be a whole number"
        query["people"] = {"$elemMatch": {"born": {"$lte": year}, "died": {"$gte": year}}}

    return query, None


SORT_OPTIONS = {"ref": "plaque_ref", "year": "erected_year", "views": "views"}


@app.route("/api/v1.0/plaques/search", methods=["GET"])
def search_plaques():
    page, size, error = get_pagination()
    if error:
        return error

    query, error = build_search_query(request.args)
    if error:
        return make_response(jsonify({"error": error}), 400)

    sort_by = request.args.get("sort", "ref")
    if sort_by not in SORT_OPTIONS:
        return make_response(jsonify({"error": "sort must be one of: " + ", ".join(SORT_OPTIONS)}), 400)
    direction = -1 if request.args.get("order") == "desc" else 1

    fields = {"visits": 0, "photos": 0, "people.life_events": 0}
    total = plaques.count_documents(query)
    data_to_return = []
    for plaque in (plaques.find(query, fields)
                   .sort([(SORT_OPTIONS[sort_by], direction), ("plaque_ref", 1)])
                   .skip((page - 1) * size).limit(size)):
        data_to_return.append(clean_plaque(plaque))

    return make_response(jsonify({
        "total": total,
        "page": page,
        "pages": math.ceil(total / size),
        "plaques": data_to_return
    }), 200)




if __name__ == "__main__":
    app.run(debug=True, port=5001)
