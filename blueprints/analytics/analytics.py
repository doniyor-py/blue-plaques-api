"""Analytics: aggregation pipelines over all plaques and visits."""
from flask import Blueprint, request, make_response, jsonify

from globals import plaques

analytics_bp = Blueprint("analytics_bp", __name__)


@analytics_bp.route("/api/v1.0/analytics/boroughs", methods=["GET"])
def borough_stats():
    pipeline = [
        {"$group": {
            "_id": "$borough",
            "plaques": {"$sum": 1},
            "total_views": {"$sum": "$views"},
            "avg_views": {"$avg": "$views"}
        }},
        {"$sort": {"plaques": -1}},
        {"$project": {
            "_id": 0,
            "borough": "$_id",
            "plaques": 1,
            "total_views": 1,
            "avg_views": {"$round": ["$avg_views", 1]}
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@analytics_bp.route("/api/v1.0/analytics/ratings", methods=["GET"])
def rating_stats():
    pipeline = [
        {"$unwind": "$visits"},
        {"$group": {
            "_id": "$borough",
            "visits": {"$sum": 1},
            "avg_history": {"$avg": "$visits.ratings.history"},
            "avg_condition": {"$avg": "$visits.ratings.condition"},
            "avg_accessibility": {"$avg": "$visits.ratings.accessibility"}
        }},
        {"$project": {
            "_id": 0,
            "borough": "$_id",
            "visits": 1,
            "avg_history": {"$round": ["$avg_history", 2]},
            "avg_condition": {"$round": ["$avg_condition", 2]},
            "avg_accessibility": {"$round": ["$avg_accessibility", 2]},
            "avg_overall": {"$round": [
                {"$avg": ["$avg_history", "$avg_condition", "$avg_accessibility"]}, 2
            ]}
        }},
        {"$sort": {"avg_overall": -1}}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@analytics_bp.route("/api/v1.0/analytics/decades", methods=["GET"])
def decade_stats():
    pipeline = [
        {"$match": {"erected_year": {"$type": "number"}}},
        {"$group": {
            "_id": {"$subtract": ["$erected_year", {"$mod": ["$erected_year", 10]}]},
            "plaques": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id": 0,
            "decade": "$_id",
            "plaques": 1
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@analytics_bp.route("/api/v1.0/analytics/top-rated", methods=["GET"])
def top_rated():
    try:
        min_visits = int(request.args.get("min_visits", 3))
        limit = int(request.args.get("limit", 10))
    except ValueError:
        return make_response(jsonify({"error": "min_visits and limit must be whole numbers"}), 400)
    if min_visits < 1 or limit < 1 or limit > 50:
        return make_response(jsonify({"error": "min_visits must be 1+, limit must be 1-50"}), 400)

    pipeline = [
        {"$unwind": "$visits"},
        {"$group": {
            "_id": "$_id",
            "inscription": {"$first": "$inscription"},
            "borough": {"$first": "$borough"},
            "visits": {"$sum": 1},
            "avg_rating": {"$avg": {"$avg": [
                "$visits.ratings.history",
                "$visits.ratings.condition",
                "$visits.ratings.accessibility"
            ]}}
        }},
        {"$match": {"visits": {"$gte": min_visits}}},
        {"$sort": {"avg_rating": -1, "visits": -1}},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "plaque_id": {"$toString": "$_id"},
            "inscription": 1,
            "borough": 1,
            "visits": 1,
            "avg_rating": {"$round": ["$avg_rating", 2]}
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@analytics_bp.route("/api/v1.0/analytics/professions", methods=["GET"])
def profession_stats():
    pipeline = [
        {"$unwind": "$people"},
        {"$unwind": "$people.roles"},
        {"$group": {"_id": "$people.roles", "people": {"$sum": 1}}},
        {"$sort": {"people": -1}},
        {"$project": {"_id": 0, "role": "$_id", "people": 1}}
    ]
    results = list(plaques.aggregate(pipeline))
    return make_response(jsonify(results), 200)


@analytics_bp.route("/api/v1.0/analytics/ages", methods=["GET"])
def age_stats():
    pipeline = [
        {"$unwind": "$people"},
        {"$match": {"people.born": {"$ne": None}, "people.died": {"$ne": None}}},
        {"$project": {"age": {"$subtract": ["$people.died", "$people.born"]}}},
        {"$group": {
            "_id": None,
            "people": {"$sum": 1},
            "avg_age": {"$avg": "$age"},
            "youngest": {"$min": "$age"},
            "oldest": {"$max": "$age"}
        }},
        {"$project": {
            "_id": 0,
            "people": 1,
            "avg_age": {"$round": ["$avg_age", 1]},
            "youngest": 1,
            "oldest": 1
        }}
    ]
    results = list(plaques.aggregate(pipeline))
    if not results:
        return make_response(jsonify({"people": 0}), 200)
    return make_response(jsonify(results[0]), 200)
