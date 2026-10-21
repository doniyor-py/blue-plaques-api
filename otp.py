"""Email sending, one-time codes (OTP) and the Cloudflare Turnstile human check."""
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import bcrypt
import requests
from flask import request

from globals import (EMAIL_MODE, SMTP_USER, SMTP_PASSWORD, TURNSTILE_SECRET, otps)


def send_email(to, subject, body):
    """Console mode prints the email in the terminal; smtp mode really sends it via Gmail."""
    if EMAIL_MODE == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        print("\n" + "=" * 50)
        print("EMAIL TO:", to)
        print("SUBJECT: ", subject)
        print(body)
        print("=" * 50 + "\n", flush=True)


def create_otp(email, purpose, data=None):
    """Make a 6-digit code, save its HASH for 10 minutes, and return the code."""
    code = f"{secrets.randbelow(1000000):06d}"           # 000000 - 999999
    otps.delete_many({"email": email, "purpose": purpose})   # only the newest code counts
    otps.insert_one({
        "email": email,
        "purpose": purpose,                               # "register" or "reset"
        "code": bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "attempts": 0,
        "data": data or {}
    })
    return code


def check_otp(email, purpose, code):
    """Return (saved_otp, None) if the code is right, otherwise (None, error message)."""
    saved = otps.find_one({"email": email, "purpose": purpose})
    if saved is None:
        return None, "No code found - request a new one"

    expires = saved["expires_at"]
    if expires.tzinfo is None:                            # MongoDB returns times without a timezone
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        otps.delete_one({"_id": saved["_id"]})
        return None, "Code has expired - request a new one"

    if not bcrypt.checkpw(code.encode("utf-8"), saved["code"]):
        tries_left = 4 - saved["attempts"]
        if tries_left <= 0:
            otps.delete_one({"_id": saved["_id"]})
            return None, "Too many wrong codes - request a new one"
        otps.update_one({"_id": saved["_id"]}, {"$inc": {"attempts": 1}})
        return None, f"Wrong code, {tries_left} tries left"

    otps.delete_one({"_id": saved["_id"]})                # a code works only once
    return saved, None


def is_human(captcha_token):
    """Ask Cloudflare Turnstile whether the captcha token is genuine."""
    if not captcha_token:
        return False
    try:
        reply = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET,
                  "response": captcha_token,
                  "remoteip": request.remote_addr},
            timeout=5)
        return reply.json().get("success", False)
    except requests.RequestException:
        return False
