"""Reports: users report a problem with a plaque, admins review them."""
import math
from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, request, make_response, jsonify, g

from globals import plaques, reports
from decorators import jwt_required, admin_required, current_username
from extensions import limiter
from helpers import get_pagination

reports_bp = Blueprint("reports_bp", __name__)


REPORT_TYPES = ["missing", "damaged", "wrong_info", "other"]


REPORT_STATUSES = ["open", "in_progress", "resolved"]


def clean_report(report):
    report["_id"] = str(report["_id"])
    report["plaque_id"] = str(report["plaque_id"])
    report["created"] = report["created"].isoformat()
    return report


@reports_bp.route("/api/v1.0/plaques/<string:id>/reports", methods=["POST"])
@jwt_required
@limiter.limit("5 per hour", key_func=current_username)
def report_problem(id):
    if not ObjectId.is_valid(id):
        return make_response(jsonify({"error": "Invalid plaque ID"}), 400)

    report_type = request.form.get("type", "")
    message = request.form.get("message", "").strip()
    if report_type not in REPORT_TYPES:
        return make_response(jsonify({"error": "type must be one of: " + ", ".join(REPORT_TYPES)}), 400)
    if len(message) < 5 or len(message) > 500:
        return make_response(jsonify({"error": "message must be 5-500 characters"}), 400)
    if plaques.find_one({"_id": ObjectId(id)}, {"_id": 1}) is None:
        return make_response(jsonify({"error": "Plaque not found"}), 404)

    already = reports.find_one({"plaque_id": ObjectId(id), "username": g.user["username"],
                                "status": {"$ne": "resolved"}})
    if already:
        return make_response(jsonify({"error": "You already reported this plaque, an admin is checking it"}), 409)
    new_id = reports.insert_one({
        "plaque_id": ObjectId(id),
        "username": g.user["username"],
        "type": report_type,
        "message": message,
        "status": "open",
        "created": datetime.now(timezone.utc)
    }).inserted_id
    return make_response(jsonify({"message": "Thanks, an admin will check it", "report_id": str(new_id)}), 201)


@reports_bp.route("/api/v1.0/reports", methods=["GET"])
@jwt_required
@admin_required
def list_reports():
    page, size, error = get_pagination()
    if error:
        return error

    query = {}
    status = request.args.get("status")
    if status:
        if status not in REPORT_STATUSES:
            return make_response(jsonify({"error": "status must be one of: " + ", ".join(REPORT_STATUSES)}), 400)
        query["status"] = status

    total = reports.count_documents(query)
    data_to_return = [clean_report(r) for r in
                      reports.find(query).sort("created", -1).skip((page - 1) * size).limit(size)]
    return make_response(jsonify({
        "total": total,
        "page": page,
        "pages": math.ceil(total / size),
        "reports": data_to_return
    }), 200)


@reports_bp.route("/api/v1.0/reports/<string:rid>", methods=["PUT"])
@jwt_required
@admin_required
def update_report(rid):
    if not ObjectId.is_valid(rid):
        return make_response(jsonify({"error": "Invalid report ID"}), 400)
    status = request.form.get("status", "")
    if status not in REPORT_STATUSES:
        return make_response(jsonify({"error": "status must be one of: " + ", ".join(REPORT_STATUSES)}), 400)

    result = reports.update_one({"_id": ObjectId(rid)}, {"$set": {"status": status}})
    if result.matched_count == 0:
        return make_response(jsonify({"error": "Report not found"}), 404)
    return make_response(jsonify({"message": "Report status updated to " + status}), 200)
