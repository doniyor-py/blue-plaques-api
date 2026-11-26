"""Card fields (address, category, photo_thumb, rating, review_count) in the list endpoints.

Runs against a throwaway database, never the real one:
    venv/bin/python -m unittest discover -s tests -t .
"""
import os
os.environ["MONGO_DB"] = "plaquesDB_test"          # must be set before the app (and globals) are imported

import unittest
import bcrypt
from bson import ObjectId

from app import app
from globals import client, db, plaques, users

DICKENS = ObjectId()
SMITH = ObjectId()


def visit(ratings=None):
    found = {"_id": ObjectId(), "username": "user1", "date": "2026-10-28", "note": ""}
    if ratings:
        found["ratings"] = dict(zip(["history", "condition", "accessibility"], ratings))
    return found


class CardFieldsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        assert db.name == "plaquesDB_test", "refusing to run against " + db.name
        plaques.delete_many({})                   # empty, but keep the indexes the app made on import
        users.delete_many({})
        plaques.insert_many([
            {"_id": DICKENS, "plaque_ref": 1, "title": "Charles Dickens blue plaque", "colour": "blue",
             "inscription": "Charles Dickens 1812-1870 novelist lived here", "borough": "Camden",
             "address": "48 Doughty Street, Camden, WC1", "category": "literature", "erected_year": 1903,
             "location": {"type": "Point", "coordinates": [-0.1163, 51.5235]}, "views": 0,
             "people": [{"name": "Charles Dickens", "born": 1812, "died": 1870, "life_events": []}],
             "photos": [{"_id": ObjectId(), "filename": "a.jpg", "thumb_filename": "a_thumb.jpg", "username": "user1"},
                        {"_id": ObjectId(), "filename": "b.jpg", "username": "user1"}],
             "visits": [visit([5, 4, 5]), visit([4, 4, 4]), visit()]},
            {"_id": SMITH, "plaque_ref": 2, "title": "Sydney Smith brown plaque", "colour": "brown",
             "inscription": "Sydney Smith author and wit lived here", "borough": "Camden",
             "address": "14 Doughty Street, Camden, WC1", "category": "literature",
             "location": {"type": "Point", "coordinates": [-0.1165, 51.5226]}, "views": 0,
             "people": [], "photos": [], "visits": []}
        ])
        users.insert_one({"username": "user1", "email": "user1@example.com", "admin": False,
                          "password": bcrypt.hashpw(b"UserPass123", bcrypt.gensalt()),
                          "favourites": [DICKENS, SMITH]})
        cls.web = app.test_client()
        login = cls.web.get("/api/v1.0/login", auth=("user1", "UserPass123"))
        cls.token = login.get_json()["token"]

    @classmethod
    def tearDownClass(cls):
        client.drop_database("plaquesDB_test")

    def check_cards(self, cards):
        by_id = {card["_id"]: card for card in cards}
        dickens, smith = by_id[str(DICKENS)], by_id[str(SMITH)]

        self.assertEqual(dickens["address"], "48 Doughty Street, Camden, WC1")
        self.assertEqual(dickens["category"], "literature")
        self.assertTrue(dickens["photo_thumb"].endswith("/api/v1.0/photos/a_thumb.jpg"))
        self.assertEqual(dickens["rating"], 4.3)            # (4.67 + 4) / 2, the unrated visit is left out
        self.assertEqual(dickens["review_count"], 2)

        self.assertIsNone(smith["photo_thumb"])
        self.assertIsNone(smith["rating"])
        self.assertEqual(smith["review_count"], 0)

        for card in cards:                                   # the big arrays stay out of lists
            self.assertNotIn("visits", card)
            self.assertNotIn("photos", card)
            self.assertNotIn("photo_file", card)

    def test_search(self):
        response = self.web.get("/api/v1.0/plaques/search?borough=camden")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["total"], 2)
        self.check_cards(body["plaques"])
        self.assertNotIn("life_events", body["plaques"][0]["people"][0])

    def test_search_keeps_its_order(self):
        refs = lambda order: [p["plaque_ref"] for p in self.web.get(
            "/api/v1.0/plaques/search?sort=ref&order=" + order).get_json()["plaques"]]
        self.assertEqual(refs("asc"), [1, 2])
        self.assertEqual(refs("desc"), [2, 1])

    def test_nearby(self):
        response = self.web.get("/api/v1.0/plaques/nearby?lat=51.523&lng=-0.1164&radius=1000")
        self.assertEqual(response.status_code, 200)
        cards = response.get_json()
        self.assertEqual(len(cards), 2)
        self.assertIn("distance_m", cards[0])
        self.check_cards(cards)

    def test_favourites(self):
        response = self.web.get("/api/v1.0/users/me/favourites", headers={"x-access-token": self.token})
        self.assertEqual(response.status_code, 200)
        self.check_cards(response.get_json())


if __name__ == "__main__":
    unittest.main()
