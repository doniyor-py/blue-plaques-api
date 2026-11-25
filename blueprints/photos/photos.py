"""Photos: upload (cleaned with Pillow), list, view and delete."""
import io
import os
from datetime import date

from bson import ObjectId
from flask import Blueprint, request, make_response, jsonify, g, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import NotFound

from globals import UPLOAD_FOLDER, plaques
from decorators import jwt_required, current_username
from extensions import limiter
from helpers import add_photo_urls, delete_photo_file

photos_bp = Blueprint("photos_bp", __name__)


MAX_PHOTO_SIDE = 1600                  # longest side in pixels after resizing
THUMB_SIDE = 480                       # longest side of the small version used in the gallery grid
WEBP_QUALITY = 80

MAX_PHOTOS = 10                         # most photos one plaque can have
MAX_PIXELS = 40_000_000                 # largest image we accept (width x height)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS     # Pillow's own safety net
ONE_YEAR = 365 * 24 * 60 * 60           # seconds


def to_webp(img):
    output = io.BytesIO()
    img.save(output, "WEBP", quality=WEBP_QUALITY)
    return output.getvalue()


def clean_image(data):
    """Open the upload with Pillow and return (photo, thumbnail) as fresh WebP files
    with no hidden data, or None."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ("JPEG", "MPO", "PNG", "WEBP"):
                return None
            if img.width * img.height > MAX_PIXELS:
                return None
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
            img = img.convert("RGB")
            photo = to_webp(img)
            img.thumbnail((THUMB_SIDE, THUMB_SIDE))         # shrink the same image again
            return photo, to_webp(img)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return None


@photos_bp.route("/api/v1.0/plaques/<string:id>/photos", methods=["POST"])
@jwt_required
@limiter.limit("30 per day", key_func=current_username)
@limiter.limit("10 per minute")
def add_photo(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)
    plaque = plaques.find_one({"_id": ObjectId(id)}, {"photos._id": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)
    if len(plaque.get("photos", [])) >= MAX_PHOTOS:                 # quick check before the slow image work
        return make_response(jsonify({"error": f"This plaque already has {MAX_PHOTOS} photos"}), 409)

    file = request.files.get("photo")
    if file is None or file.filename == "":
        return make_response(jsonify({"error": "Attach an image in the 'photo' field"}), 400)

    cleaned = clean_image(file.read())
    if cleaned is None:
        return make_response(jsonify({"error": "Photo must be a JPG, PNG or WEBP image"}), 400)
    photo_data, thumb_data = cleaned

    caption = request.form.get("caption", "").strip()
    if len(caption) > 200:
        return make_response(jsonify({"error": "caption must be 200 characters or fewer"}), 400)

    photo_id = ObjectId()
    new_photo = {
        "_id": photo_id,
        "filename": str(photo_id) + ".webp",
        "thumb_filename": str(photo_id) + "-thumb.webp",
        "caption": caption,
        "username": g.user["username"],
        "uploaded": date.today().isoformat()
    }
    for filename, data in [(new_photo["filename"], photo_data), (new_photo["thumb_filename"], thumb_data)]:
        with open(os.path.join(UPLOAD_FOLDER, filename), "wb") as f:
            f.write(data)

    result = plaques.update_one({"_id": ObjectId(id), f"photos.{MAX_PHOTOS - 1}": {"$exists": False}},
                                {"$push": {"photos": new_photo}})
    if result.matched_count == 0:
        delete_photo_file(new_photo)
        return make_response(jsonify({"error": f"Plaque not found or already has {MAX_PHOTOS} photos"}), 409)

    return make_response(jsonify(add_photo_urls(new_photo)), 201)


@photos_bp.route("/api/v1.0/plaques/<string:id>/photos", methods=["GET"])
def get_photos(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)
    plaque = plaques.find_one({"_id": ObjectId(id)}, {"photos": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    data_to_return = []
    for photo in plaque.get("photos", []):
        data_to_return.append(add_photo_urls(photo))
    return make_response(jsonify(data_to_return), 200)


@photos_bp.route("/api/v1.0/photos/<string:filename>", methods=["GET"])
def show_photo_file(filename):
    # file names are unique and never reused, so browsers may keep a photo for a year
    try:
        response = send_from_directory(UPLOAD_FOLDER, filename, max_age=ONE_YEAR)
    except NotFound:
        return make_response(jsonify({"error": "Photo not found"}), 404)
    response.cache_control.immutable = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@photos_bp.route("/api/v1.0/plaques/<string:id>/photos/<string:pid>", methods=["DELETE"])
@jwt_required
def delete_photo(id, pid):
    if not ObjectId.is_valid(id) or not ObjectId.is_valid(pid):
        return make_response(jsonify({"error": "Invalid plaque or photo ID"}), 400)
    plaque = plaques.find_one({"_id": ObjectId(id)}, {"photos": 1})
    if plaque is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    for photo in plaque.get("photos", []):
        if photo["_id"] == ObjectId(pid):
            if photo["username"] != g.user["username"] and not g.user.get("admin"):
                return make_response(jsonify({"error": "You can only delete your own photos"}), 403)
            plaques.update_one({"_id": ObjectId(id)},
                               {"$pull": {"photos": {"_id": ObjectId(pid)}}})
            delete_photo_file(photo)
            return make_response(jsonify({}), 204)

    return make_response(jsonify({"error": "Photo not found"}), 404)
