"""Visits: check-ins with ratings, stored as sub-documents inside each plaque."""
from datetime import date

from bson import ObjectId
from flask import Blueprint, request, make_response, jsonify, g

from globals import plaques
from decorators import jwt_required
from helpers import get_text

visits_bp = Blueprint("visits_bp", __name__)


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


@visits_bp.route("/api/v1.0/plaques/<string:id>/visits", methods=["GET"])
def get_all_visits(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    plaque = plaques.find_one({"_id": ObjectId(id)}, {"visits": 1, "_id": 0})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    for visit in plaque["visits"]:
        visit["_id"] = str(visit["_id"])
    return make_response(jsonify(plaque["visits"]), 200)


@visits_bp.route("/api/v1.0/plaques/<string:id>/visits", methods=["POST"])
@jwt_required
def add_visit(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    ratings, error = check_ratings(request.form)
    if error:
        return make_response(jsonify({"error": error}), 400)
    text, error = get_text(request.form, ["note"])
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
        "note": text["note"],
        "ratings": ratings
    }

    result = plaques.update_one({"_id": ObjectId(id)}, {"$push": {"visits": new_visit}})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    new_link = request.host_url + "api/v1.0/plaques/" + id + "/visits/" + str(new_visit["_id"])
    return make_response(jsonify({"url": new_link}), 201)


@visits_bp.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["GET"])
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


@visits_bp.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["PUT"])
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

    text, error = get_text(request.form, ["note"])
    if error:
        return make_response(jsonify({"error": error}), 400)

    changes = {"visits.$.ratings": ratings}
    if "note" in request.form:
        changes["visits.$.note"] = text["note"]

    result = plaques.update_one({"_id": ObjectId(id), "visits._id": ObjectId(vid)},
                                {"$set": changes})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque or visit not found"}), 404)

    edited_link = request.host_url + "api/v1.0/plaques/" + id + "/visits/" + vid
    return make_response(jsonify({"url": edited_link}), 200)


@visits_bp.route("/api/v1.0/plaques/<string:id>/visits/<string:vid>", methods=["DELETE"])
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
