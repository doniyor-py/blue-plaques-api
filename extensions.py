"""Shared objects that blueprints need. They are connected to the app in app.py."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, storage_uri="memory://")
