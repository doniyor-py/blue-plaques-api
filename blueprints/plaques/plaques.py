"""Plaques: list, view, create, edit, delete, nearby, random, most viewed and search."""
import math
import re
from datetime import date

from bson import ObjectId
from flask import Blueprint, request, make_response, jsonify
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from boroughs import LONDON_BOROUGHS, find_borough
from globals import plaques, users, reports
from decorators import jwt_required, admin_required
from helpers import ALLOWED_COLOURS, get_text, get_pagination, clean_plaque, delete_photo_file

plaques_bp = Blueprint("plaques_bp", __name__)


@plaques_bp.route("/api/v1.0/plaques", methods=["GET"])
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


@plaques_bp.route("/api/v1.0/plaques/<string:id>", methods=["GET"])
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


@plaques_bp.route("/api/v1.0/plaques", methods=["POST"])
@jwt_required
@admin_required
def add_plaque():
    text, error = get_text(request.form, ["title", "inscription", "colour", "organisation",
                                          "address", "borough", "category"])
    if error:
        return make_response(jsonify({"error": error}), 400)

    required = ["inscription", "colour", "organisation", "address", "borough"]
    missing = [field for field in required if not text[field]]
    missing += [field for field in ["erected_year", "lat", "lng"] if not request.form.get(field)]
    if missing:
        return make_response(jsonify({"error": "Missing fields: " + ", ".join(missing)}), 400)

    try:
        erected_year = int(request.form["erected_year"])
        lat = float(request.form["lat"])
        lng = float(request.form["lng"])
    except ValueError:
        return make_response(jsonify({"error": "erected_year must be a whole number, lat and lng must be numbers"}), 400)

    if erected_year < 1800 or erected_year > date.today().year:
        return make_response(jsonify({"error": f"erected_year must be between 1800 and {date.today().year}"}), 400)

    borough = find_borough(text["borough"])
    if borough is None:
        return make_response(jsonify({"error": "borough must be a London borough, see /api/v1.0/boroughs"}), 400)
    
    colour = text["colour"].lower()
    if colour not in ALLOWED_COLOURS:
        return make_response(jsonify({"error": "colour must be one of: " + ", ".join(ALLOWED_COLOURS)}), 400)
    
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return make_response(jsonify({"error": "lat must be -90 to 90, lng must be -180 to 180"}), 400)

    title = text["title"] or f"{colour.capitalize()} plaque at {text['address']}"
    category = text["category"].lower() or "other"

    new_plaque = {
        "title": title, 
        "inscription": text["inscription"],
        "colour": colour,
        "erected_year": erected_year,
        "organisation": text["organisation"],
        "address": text["address"],
        "borough": borough,
        "category": category,
        "location": {"type": "Point", "coordinates": [lng, lat]},
        "views": 0,
        "people": [],
        "photos": [],
        "visits": []
    }

    # Take the next free number. If another request took it at the same moment,
    # the unique index rejects ours, so we read the highest number again and retry.
    for attempt in range(3):
        last = plaques.find_one({}, {"plaque_ref": 1}, sort=[("plaque_ref", -1)])
        new_plaque["plaque_ref"] = last["plaque_ref"] + 1 if last else 1
        try:
            new_id = plaques.insert_one(new_plaque).inserted_id
            break
        except DuplicateKeyError:
            new_plaque.pop("_id", None)           # insert_one added an _id; get a fresh one next time
    else:
        return make_response(jsonify({"error": "Could not create the plaque, please try again"}), 409)

    new_link = request.host_url + "api/v1.0/plaques/" + str(new_id)
    return make_response(jsonify({"url": new_link}), 201)


@plaques_bp.route("/api/v1.0/plaques/<string:id>", methods=["PUT"])
@jwt_required
@admin_required
def edit_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    text, error = get_text(request.form, ["title", "inscription", "colour", "organisation",
                                          "address", "borough", "category"])
    if error:
        return make_response(jsonify({"error": error}), 400)
    changes = {field: value for field, value in text.items() if value}

    if "colour" in changes:
        changes["colour"] = changes["colour"].lower()
        if changes["colour"] not in ALLOWED_COLOURS:
            return make_response(jsonify({"error": "colour must be one of: " + ", ".join(ALLOWED_COLOURS)}), 400)

    if "category" in changes:
        changes["category"] = changes["category"].lower()

    if request.form.get("erected_year"):
        try:
            erected_year = int(request.form["erected_year"])
        except ValueError:
            return make_response(jsonify({"error": "erected_year must be a whole number"}), 400)
        if erected_year < 1800 or erected_year > date.today().year:
            return make_response(jsonify({"error": f"erected_year must be between 1800 and {date.today().year}"}), 400)
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

    if "borough" in changes:
        changes["borough"] = find_borough(changes["borough"])
        if changes["borough"] is None:
            return make_response(jsonify({"error": "borough must be a London borough, see /api/v1.0/boroughs"}), 400)

    if not changes:
        return make_response(jsonify({"error": "No fields to update"}), 400)

    result = plaques.update_one({"_id": ObjectId(id)}, {"$set": changes})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    edited_link = request.host_url + "api/v1.0/plaques/" + id
    return make_response(jsonify({"url": edited_link}), 200)


@plaques_bp.route("/api/v1.0/plaques/<string:id>", methods=["DELETE"])
@jwt_required
@admin_required
def delete_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    plaque = plaques.find_one({"_id": ObjectId(id)}, {"photos": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    for photo in plaque.get("photos", []):       
        delete_photo_file(photo)
    plaques.delete_one({"_id": ObjectId(id)})
    reports.delete_many({"plaque_id": ObjectId(id)})                      
    users.update_many({}, {"$pull": {"favourites": ObjectId(id)}})        

    return make_response(jsonify({}), 204)


@plaques_bp.route("/api/v1.0/boroughs", methods=["GET"])
def list_boroughs():
    return make_response(jsonify(LONDON_BOROUGHS), 200)


@plaques_bp.route("/api/v1.0/plaques/most-viewed", methods=["GET"])
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


@plaques_bp.route("/api/v1.0/plaques/nearby", methods=["GET"])
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


@plaques_bp.route("/api/v1.0/plaques/random", methods=["GET"])
def random_plaque():
    pipeline = []
    if request.args.get("borough"):
        borough = find_borough(request.args["borough"])
        if borough is None:
            return make_response(jsonify({"error": "Unknown borough (see /api/v1.0/boroughs)"}), 400)
        pipeline.append({"$match": {"borough": borough}})
    pipeline.append({"$sample": {"size": 1}})

    results = list(plaques.aggregate(pipeline))
    if not results:
        return make_response(jsonify({"error": "No plaques found"}), 404)
    return make_response(jsonify(clean_plaque(results[0])), 200)


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
        names = []
        for name in args["borough"].split(","):
            if name.strip():
                borough = find_borough(name)
                if borough is None:
                    return None, "Unknown borough: " + name.strip() + " (see /api/v1.0/boroughs)"
                names.append(borough)
        query["borough"] = {"$in": names}

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
    if any(not 0 <= y <= 3000 for y in years.values()):
        return None, "from_year and to_year must be between 0 and 3000"
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
        if not 0 <= year <= 3000:
                return None, "alive_in must be between 0 and 3000"
        query["people"] = {"$elemMatch": {"born": {"$lte": year}, "died": {"$gte": year}}}

    return query, None


SORT_OPTIONS = {"ref": "plaque_ref", "year": "erected_year", "views": "views"}


@plaques_bp.route("/api/v1.0/plaques/search", methods=["GET"])
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
