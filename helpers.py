"""Small helpers used by several blueprints."""
import math
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

ALLOWED_CATEGORIES = [
    "literature", "art-design", "film-tv-theatre", "music", "politics-military",
    "science-medicine", "royalty", "sport", "other"
]

LONDON_LAT = (51.2, 51.8)
LONDON_LNG = (-0.6, 0.4)


def check_location(lat, lng):
    if not (LONDON_LAT[0] <= lat <= LONDON_LAT[1]) or not (LONDON_LNG[0] <= lng <= LONDON_LNG[1]):
        return "The location must be in Greater London: lat 51.2 to 51.8, lng -0.6 to 0.4"
    return None


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
    if page < 1 or page > 100000:
        return None, None, make_response(jsonify({"error": "pn must be between 1 and 100000"}), 400)
    if size < 1 or size > 100:
        return None, None, make_response(jsonify({"error": "ps must be between 1 and 100"}), 400)
    return page, size, None

def page_info(total, page, size):
    return {"total": total, "page": page, "page_size": size, "pages": math.ceil(total / size)}


def photo_url(filename):
    return request.host_url + "api/v1.0/photos/" + filename


def add_photo_urls(photo):
    """Full-size url, and a small thumb_url (older photos have no thumbnail, so they use the full one)."""
    photo["_id"] = str(photo["_id"])
    photo["url"] = photo_url(photo["filename"])
    photo["thumb_url"] = photo_url(photo.get("thumb_filename", photo["filename"]))
    return photo


def clean_plaque(plaque):
    plaque["_id"] = str(plaque["_id"])
    for visit in plaque.get("visits", []):
        visit["_id"] = str(visit["_id"])
    for photo in plaque.get("photos", []):
        add_photo_urls(photo)
    return plaque


def delete_photo_file(photo):
    """Delete the photo and its thumbnail (older photos have none) from the uploads folder."""
    for filename in [photo["filename"], photo.get("thumb_filename")]:
        if filename:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, filename))
            except FileNotFoundError:
                pass
