"""Small helpers used by several blueprints."""
import os
from flask import request, make_response, jsonify
from globals import UPLOAD_FOLDER

ALLOWED_COLOURS = [
    "belgian blue stone", "black", "blue", "brass", "bronze", "brown",
    "brushed metal", "claret", "clear", "film cell", "gold", "green",
    "green and red", "grey", "marble", "maroon", "multicoloured", "orange",
    "pink", "purple", "red", "red and black", "slate", "stone",
    "terracotta", "unknown", "white", "wood", "yellow"
]

MAX_PASSWORD_BYTES = 72          


def password_too_long(password):
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


TEXT_LIMITS = {"title": 200, "inscription": 5000, "colour": 30, "organisation": 200,
               "address": 200, "borough": 50, "category": 50, "note": 500}


def get_text(form, fields):
    values = {}
    for field in fields:
        value = form.get(field, "").strip()
        if len(value) > TEXT_LIMITS[field]:
            return None, f"{field} must be {TEXT_LIMITS[field]} characters or fewer"
        values[field] = value
    return values, None


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


def photo_url(filename):
    return request.host_url + "api/v1.0/photos/" + filename


def clean_plaque(plaque):
    plaque["_id"] = str(plaque["_id"])
    for visit in plaque.get("visits", []):
        visit["_id"] = str(visit["_id"])
    for photo in plaque.get("photos", []):          
        photo["_id"] = str(photo["_id"]) 
        photo["url"] = photo_url(photo["filename"]) 
    return plaque


def delete_photo_file(photo):
    path = os.path.join(UPLOAD_FOLDER, photo["filename"])
    if os.path.exists(path):
        os.remove(path)
