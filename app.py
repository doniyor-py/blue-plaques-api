from flask import Flask, make_response, jsonify, request
from pymongo import MongoClient
from bson import ObjectId

app = Flask(__name__)

# MongoDB connection
client = MongoClient("mongodb://localhost:27017")
db = client.plaquesDB
plaques = db.plaques


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
        plaque["_id"] = str(plaque["_id"])
        data_to_return.append(plaque)

    return make_response(jsonify(data_to_return), 200)


@app.route("/api/v1.0/plaques/<string:id>", methods=["GET"])
def show_one_plaque(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    plaque = plaques.find_one({"_id": ObjectId(id)})

    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    plaque["_id"] = str(plaque["_id"])
    return make_response(jsonify(plaque), 200)



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
        "people": [],
        "photos": [],
        "visits": []
    }


    new_id = plaques.insert_one(new_plaque).inserted_id
    new_link = "http://127.0.0.1:5001/api/v1.0/plaques/" + str(new_id)
    return make_response(jsonify({"url": new_link}), 201)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
