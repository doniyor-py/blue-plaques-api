from flask import Flask, make_response, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient, ReturnDocument
from bson import ObjectId
from datetime import date

app = Flask(__name__)
CORS(app)

# MongoDB connection
client = MongoClient("mongodb://localhost:27017")
db = client.plaquesDB
plaques = db.plaques

# geo index: lets MongoDB search plaques by distance
plaques.create_index([("location", "2dsphere")])

def clean_plaque(plaque):
    """Turn every ObjectId in a plaque (its own _id and each visit _id) into text for JSON."""
    plaque["_id"] = str(plaque["_id"])
    for visit in plaque.get("visits", []):
        visit["_id"] = str(visit["_id"])
    return plaque



@app.route("/", methods=["GET"])
def index():
    return make_response("<h1>Blue Plaques Explorer API</h1>", 200)


@app.route("/api/v1.0/plaques", methods=["GET"])
def show_all_plaques():
    page_num, page_size = 1, 10
    if request.args.get("pn"):
        page_num = int(request.args.get("pn"))
    if request.args.get("ps"):
        page_size = int(request.args.get("ps"))
    page_start = page_size * (page_num - 1)

    data_to_return = []
    for plaque in plaques.find().sort("plaque_ref", 1).skip(page_start).limit(page_size):
        data_to_return.append(clean_plaque(plaque))

    return make_response(jsonify(data_to_return), 200)


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
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return make_response(jsonify({"error": "lat must be -90 to 90, lng must be -180 to 180"}), 400)

    last = plaques.find_one(sort=[("plaque_ref", -1)])
    next_ref = last["plaque_ref"] + 1 if last else 1

    new_plaque = {
        "plaque_ref": next_ref,
        "inscription": request.form["inscription"],
        "colour": request.form["colour"],
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
def edit_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    changes = {}
    for field in ["inscription", "colour", "organisation", "address", "borough"]:
        if request.form.get(field):
            changes[field] = request.form[field]

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



# ===================== VISITS (sub-documents) =====================

def check_ratings(form):
    """Read the 3 ratings from the form. Returns (ratings, error)."""
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
def add_visit(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)
    if not request.form.get("username"):
        return make_response(jsonify({"error": "Missing field: username"}), 400)

    ratings, error = check_ratings(request.form)
    if error:
        return make_response(jsonify({"error": error}), 400)

    new_visit = {
        "_id": ObjectId(),
        "username": request.form["username"],
        "date": date.today().isoformat(),
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
def edit_visit(id, vid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(vid):
        return make_response(jsonify({"error": "Invalid plaque or visit ID"}), 400)

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
def delete_visit(id, vid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(vid):
        return make_response(jsonify({"error": "Invalid plaque or visit ID"}), 400)

    result = plaques.update_one({"_id": ObjectId(id)},
                                {"$pull": {"visits": {"_id": ObjectId(vid)}}})
    if result.modified_count == 0:
        return make_response(jsonify({"error": "Plaque or visit not found"}), 404)

    return make_response(jsonify({}), 204)



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


if __name__ == "__main__":
    app.run(debug=True, port=5001)
