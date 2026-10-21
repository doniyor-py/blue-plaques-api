"""Accounts: profile, password, deleting accounts, admin user list, favourites, personal stats."""
from datetime import datetime, timezone

import bcrypt
from bson import ObjectId
from flask import Blueprint, request, make_response, jsonify, g

from globals import plaques, users, blacklist
from decorators import jwt_required, admin_required
from helpers import password_too_long, get_pagination, delete_photo_file, page_info

users_bp = Blueprint("users_bp", __name__)


@users_bp.route("/api/v1.0/users/me", methods=["GET"])
@jwt_required
def my_profile():
    user = users.find_one({"username": g.user["username"]},
                          {"_id": 0, "password": 0})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)

    user["favourites"] = [str(f) for f in user.get("favourites", [])]
    return make_response(jsonify(user), 200)


@users_bp.route("/api/v1.0/users/me/password", methods=["PUT"])
@jwt_required
def change_password():
    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    user = users.find_one({"username": g.user["username"]})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)
    if password_too_long(old_password) or not bcrypt.checkpw(old_password.encode("utf-8"), user["password"]):
        return make_response(jsonify({"error": "Old password is incorrect"}), 401)
    if len(new_password) < 8 or password_too_long(new_password):
        return make_response(jsonify({"error": "new_password must be 8-72 characters"}), 400)
    if new_password == old_password:
        return make_response(jsonify({"error": "New password must be different from the old one"}), 400)

    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())
    users.update_one({"username": g.user["username"]},
                     {"$set": {"password": hashed, "password_changed_at": datetime.now(timezone.utc)}})

    # log out this token, so the user must log in again with the new password
    blacklist.insert_one({"token": request.headers.get("x-access-token")})
    return make_response(jsonify({"message": "Password changed, please log in again"}), 200)


@users_bp.route("/api/v1.0/users/me", methods=["DELETE"])
@jwt_required
def delete_my_account():
    user = users.find_one({"username": g.user["username"]})
    if user is None:
        return make_response(jsonify({"error": "Account not found"}), 404)
    if user.get("admin"):
        return make_response(jsonify({"error": "Admin accounts cannot be deleted here"}), 403)

    password = request.form.get("password", "")
    if password_too_long(password) or not bcrypt.checkpw(password.encode("utf-8"), user["password"]):
        return make_response(jsonify({"error": "Password is incorrect"}), 401)

    remove_user_and_data(user["username"])
    blacklist.insert_one({"token": request.headers.get("x-access-token")})  
    return make_response(jsonify({}), 204)


@users_bp.route("/api/v1.0/users/<string:username>", methods=["DELETE"])
@jwt_required
@admin_required
def admin_delete_user(username):
    username = username.strip().lower() 
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
    for plaque in plaques.find({"photos.username": username}, {"photos": 1}):  
        for photo in plaque["photos"]:                                          
            if photo["username"] == username:                                   
                delete_photo_file(photo)                                        
    plaques.update_many({}, {"$pull": {"photos": {"username": username}}})       


@users_bp.route("/api/v1.0/users", methods=["GET"])
@jwt_required
@admin_required
def list_users():
    page, size, error = get_pagination()
    if error:
        return error

    total = users.count_documents({})
    data_to_return = list(users.find({}, {"_id": 0, "password": 0, "favourites": 0})
                          .sort("username", 1).skip((page - 1) * size).limit(size))
    return make_response(jsonify({**page_info(total, page, size), "users": data_to_return}), 200)


@users_bp.route("/api/v1.0/users/me/favourites/<string:id>", methods=["POST"])
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


@users_bp.route("/api/v1.0/users/me/favourites", methods=["GET"])
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


@users_bp.route("/api/v1.0/users/me/favourites/<string:id>", methods=["DELETE"])
@jwt_required
def remove_favourite(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    result = users.update_one({"username": g.user["username"]},
                              {"$pull": {"favourites": ObjectId(id)}})
    if result.modified_count == 0:
        return make_response(jsonify({"error": "Not in your favourites"}), 404)
    return make_response(jsonify({}), 204)


@users_bp.route("/api/v1.0/users/me/stats", methods=["GET"])
@jwt_required
def my_stats():
    pipeline = [
        {"$match": {"visits.username": g.user["username"]}},  
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


@users_bp.route("/api/v1.0/users/me/progress", methods=["GET"])
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
