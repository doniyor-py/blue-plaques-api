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

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise SystemExit("SECRET_KEY missing: create a .env file (see instructions)")

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

users.create_index("username", unique=True)
users.create_index("email", unique=True)

plaques.create_index([("location", "2dsphere")])


ALLOWED_COLOURS = ["blue", "green", "brown", "black", "red", "white",
                   "grey", "yellow", "purple", "pink", "bronze", "stone"]


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

    new_plaque = {
        "plaque_ref": next_ref,
        "inscription": request.form["inscription"],
        "colour": request.form["colour"].lower(),
        "erected_year": erected_year,
        "organisation": request.form["organisation"],
        "address": request.form["address"],
        "borough": request.form["borough"],
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
    for field in ["inscription", "colour", "organisation", "address", "borough"]:
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
    data_to_return = []
    for plaque in plaques.find().sort("views", -1).limit(10):
        data_to_return.append(clean_plaque(plaque))
    return make_response(jsonify(data_to_return), 200)



# ===================== AUTH ROUTES =====================

@app.route("/api/v1.0/register", methods=["POST"])
@limiter.limit("5 per hour")
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

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

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    try:
        users.insert_one({
            "username": username,
            "email": email,
            "password": hashed,
            "admin": False             # new accounts are never admins
        })
    except DuplicateKeyError:          # two sign-ups with the same name at the same moment
        return make_response(jsonify({"error": "Username or email already taken"}), 409)
    return make_response(jsonify({"message": "Account created, you can now log in"}), 201)


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

if __name__ == "__main__":
    app.run(debug=True, port=5001)
