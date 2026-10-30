"""Values shared by every file: settings from .env, and the MongoDB connection."""
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or len(SECRET_KEY) < 32 or SECRET_KEY.startswith("change-"):
    raise SystemExit('SECRET_KEY must be 32+ random characters, make one with: '
                     'python -c "import secrets; print(secrets.token_hex(32))"')

TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET")          # Cloudflare human check
EMAIL_MODE = os.getenv("EMAIL_MODE", "console")           # "console" or "smtp"
SMTP_USER = os.getenv("SMTP_USER")                        # only needed for "smtp"
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


if not TURNSTILE_SECRET:
    raise SystemExit("TURNSTILE_SECRET missing: add it to .env (test key: 1x0000000000000000000000000000000AA)")
if EMAIL_MODE not in ("console", "smtp"):
    raise SystemExit('EMAIL_MODE must be "console" or "smtp"')
if EMAIL_MODE == "smtp" and not (SMTP_USER and SMTP_PASSWORD):
    raise SystemExit("EMAIL_MODE=smtp needs SMTP_USER and SMTP_PASSWORD in .env")


# Photo uploads are saved in the "uploads" folder next to this file
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

# MongoDB connection, collections and indexes
client = MongoClient("mongodb://localhost:27017")
db = client.plaquesDB
plaques = db.plaques
users = db.users
blacklist = db.blacklist

reports = db.reports

otps = db.otps

# MongoDB deletes expired codes automatically (TTL index)
otps.create_index("expires_at", expireAfterSeconds=0)

users.create_index("username", unique=True)
users.create_index("email", unique=True)
blacklist.create_index("token")

plaques.create_index([("location", "2dsphere")])
plaques.create_index([("views", -1), ("plaque_ref", 1)])
plaques.create_index("plaque_ref", unique=True)
plaques.create_index("visits.username")
plaques.create_index("borough")
plaques.create_index("category")
plaques.create_index("colour")
plaques.create_index("erected_year")
