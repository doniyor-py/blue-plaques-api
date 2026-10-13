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


if __name__ == "__main__":
    app.run(debug=True, port=5001)
