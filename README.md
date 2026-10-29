# London Blue Plaques Explorer API

A REST API for exploring London's commemorative plaques: who lived where, when, and what visitors think of them today.

Built with **Python Flask** and **MongoDB** for COM661 Full Stack Strategies and Development (Coursework 1, back end).

---

## Features

- **3,375 real London plaques** from Open Plaques, each with the people it commemorates and their life events
- **Search and filters**: free text, borough, colour, category, year erected, and "someone on the plaque was alive in year X"
- **Nearby plaques** within a radius of a location, with distance and walking time (`$geoNear`)
- **Visits**: users check in and rate a plaque (history, condition, accessibility), with pagination, sorting and partial edits
- **Photos**: users upload photos of plaques; images are re-encoded so location data (EXIF/GPS) is removed
- **Analytics** built with MongoDB aggregation pipelines: plaques per borough and decade, average ratings, top-rated plaques, professions, ages, and personal stats and progress
- **Accounts**: sign-up with a human check and an emailed 6-digit code, login with JWT, logout, password reset, admin role
- **Favourites** and **problem reports** reviewed by admins
- Structured with **Flask Blueprints**; rate limiting and validation on every input

---

## Tech stack

| Area | Technology |
|---|---|
| Language | Python 3.11+ |
| Web framework | Flask, Flask Blueprints, Flask-CORS, Flask-Limiter |
| Database | MongoDB 7 with PyMongo |
| Authentication | JWT (PyJWT), bcrypt password hashing |
| Human check | Cloudflare Turnstile |
| Images | Pillow |

---

## Project structure

```
blue-plaques-api/
├── app.py                  creates the app, error handlers, registers the Blueprints
├── globals.py              settings from .env, MongoDB connection, collections and indexes
├── extensions.py           rate limiter shared by the Blueprints
├── decorators.py           @jwt_required, @admin_required
├── helpers.py              pagination, input checks, shared helpers
├── otp.py                  email sending, one-time codes, Turnstile check
├── boroughs.py             the 33 London boroughs
├── blueprints/
│   ├── plaques/plaques.py      plaques, search, nearby, random, most viewed
│   ├── visits/visits.py        check-ins and ratings (sub-documents)
│   ├── photos/photos.py        photo upload and delete
│   ├── auth/auth.py            register, login, logout, password reset
│   ├── users/users.py          profile, favourites, personal stats, admin user list
│   ├── analytics/analytics.py  aggregation pipelines
│   └── reports/reports.py      problem reports
├── convert_real_data.py    turns the raw Open Plaques file into plaques.json
├── create_users.py         creates the test accounts
├── london_plaques_raw.json raw Open Plaques data
├── borough_cache.json      borough of each plaque (from postcodes.io)
├── plaques.json            the dataset to import
├── requirements.txt
└── .env.example
```

---

## Setup

You need **Python 3.11+** and **MongoDB** (with the MongoDB Database Tools for `mongoimport`).

### 1. Install

```bash
git clone https://github.com/doniyor-py/blue-plaques-api.git
cd blue-plaques-api
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
python -m pip install -r requirements.txt
```

### 2. Settings

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the printed value after `SECRET_KEY=` in `.env`. The app refuses to start with a missing or weak key.

| Setting | Meaning |
|---|---|
| `SECRET_KEY` | signs login tokens; 32+ random characters |
| `TURNSTILE_SECRET` | Cloudflare Turnstile key. The example value is Cloudflare's **test key**, which always passes |
| `EMAIL_MODE` | `console` prints emails in the terminal; `smtp` sends them through Gmail |
| `SMTP_USER`, `SMTP_PASSWORD` | only needed for `smtp` |
| `FLASK_DEBUG` | `1` for development, `0` otherwise |

### 3. Database

Start MongoDB, then load the data and the test accounts:

```bash
mongod --dbpath ~/data/db --fork --logpath ~/data/mongod.log
mongoimport --db plaquesDB --collection plaques --file plaques.json --jsonArray --drop
python create_users.py
```

> `create_users.py` **deletes all existing users** before creating the test accounts.

### 4. Run

```bash
python app.py
```

The API runs at **http://127.0.0.1:5001**. Indexes are created automatically on startup.

---

## Test accounts

| Username | Password | Role |
|---|---|---|
| `admin1` | `AdminPass123` | admin |
| `user0` … `user9` | `UserPass123` | user |

**Logging in:** `GET /api/v1.0/login` with Basic Auth returns a token. Send it in the `x-access-token` header on protected routes. Tokens last 30 minutes.

**Signing up in test mode:** send `captcha_token=XXXX.DUMMY.TOKEN.XXXX` with `POST /register`. With `EMAIL_MODE=console`, the 6-digit code appears in the terminal running the app.

---

## Endpoints

All routes start with `/api/v1.0` except `/`. **User** means any logged-in account; **Admin** means an admin account. Paginated routes accept `pn` (page) and `ps` (page size, 1–100) and return `total`, `page`, `page_size` and `pages`.

### Plaques

| Method | URL | Access | Description |
|---|---|---|---|
| GET | `/` | Public | API information |
| GET | `/plaques` | Public | All plaques, paginated |
| GET | `/plaques/<id>` | Public | One plaque (adds 1 to its views) |
| POST | `/plaques` | Admin | Add a plaque |
| PUT | `/plaques/<id>` | Admin | Edit a plaque (send only the fields to change) |
| DELETE | `/plaques/<id>` | Admin | Delete a plaque, its photos, reports and favourites |
| GET | `/plaques/search` | Public | Search: `q`, `borough`, `colour`, `category`, `from_year`, `to_year`, `alive_in`, `sort` (`ref`, `year`, `views`), `order` |
| GET | `/plaques/nearby` | Public | `lat`, `lng`, `radius` (metres), `limit`; adds distance and walking minutes |
| GET | `/plaques/random` | Public | One random plaque; optional `borough` |
| GET | `/plaques/most-viewed` | Public | Most viewed plaques; `limit` 1–50 |
| GET | `/boroughs` | Public | The 33 accepted borough names |

### Visits

| Method | URL | Access | Description |
|---|---|---|---|
| GET | `/plaques/<id>/visits` | Public | Visits to a plaque, paginated; `sort` (`date`, `rating`), `order` (`asc`, `desc`) |
| POST | `/plaques/<id>/visits` | User | Check in with `history`, `condition`, `accessibility` (1–5) and an optional `note`; once per day per plaque |
| GET | `/plaques/<id>/visits/<vid>` | Public | One visit |
| PUT | `/plaques/<id>/visits/<vid>` | Owner or admin | Edit any of the ratings or the note |
| DELETE | `/plaques/<id>/visits/<vid>` | Owner or admin | Delete a visit |

### Photos

| Method | URL | Access | Description |
|---|---|---|---|
| POST | `/plaques/<id>/photos` | User | Upload a JPG, PNG or WEBP (`photo`, optional `caption`); max 5 MB, 10 per plaque |
| GET | `/plaques/<id>/photos` | Public | A plaque's photos |
| GET | `/photos/<filename>` | Public | The image file |
| DELETE | `/plaques/<id>/photos/<pid>` | Owner or admin | Delete a photo and its file |

### Accounts

| Method | URL | Access | Description |
|---|---|---|---|
| POST | `/register` | Public | `username`, `email`, `password`, `captcha_token`; emails a 6-digit code |
| POST | `/register/verify` | Public | `email`, `code`; creates the account |
| POST | `/password/forgot` | Public | `email`; emails a reset code |
| POST | `/password/reset` | Public | `email`, `code`, `new_password` |
| GET | `/login` | Basic Auth | Returns a JWT |
| GET | `/logout` | User | Ends the token |
| GET | `/users/me` | User | Your profile |
| PUT | `/users/me/password` | User | `old_password`, `new_password` |
| DELETE | `/users/me` | User | `password`; deletes your account, visits and photos |
| GET | `/users` | Admin | All users, paginated |
| DELETE | `/users/<username>` | Admin | Delete a user and their data |

### Favourites and personal stats

| Method | URL | Access | Description |
|---|---|---|---|
| POST | `/users/me/favourites/<id>` | User | Save a plaque |
| GET | `/users/me/favourites` | User | Your saved plaques |
| DELETE | `/users/me/favourites/<id>` | User | Remove a saved plaque |
| GET | `/users/me/stats` | User | Your visits, average rating given, boroughs visited |
| GET | `/users/me/progress` | User | Percentage of each borough's plaques you have visited |

### Analytics

| Method | URL | Access | Description |
|---|---|---|---|
| GET | `/analytics/boroughs` | Public | Plaques and views per borough |
| GET | `/analytics/ratings` | Public | Average ratings per borough |
| GET | `/analytics/decades` | Public | Plaques erected per decade |
| GET | `/analytics/top-rated` | Public | Best average rating; `min_visits`, `limit` |
| GET | `/analytics/professions` | Public | People per category |
| GET | `/analytics/ages` | Public | Average, youngest and oldest age at death |

### Reports

| Method | URL | Access | Description |
|---|---|---|---|
| POST | `/plaques/<id>/reports` | User | `type` (`missing`, `damaged`, `wrong_info`, `other`), `message`; 5 per hour |
| GET | `/reports` | Admin | All reports, paginated; optional `status` |
| PUT | `/reports/<rid>` | Admin | `status` (`open`, `in_progress`, `resolved`) |

---

## Usage

```bash
# search: literature plaques in Camden for people alive in 1900
curl "http://127.0.0.1:5001/api/v1.0/plaques/search?category=literature&borough=camden&alive_in=1900"

# plaques within 500 m of Trafalgar Square
curl "http://127.0.0.1:5001/api/v1.0/plaques/nearby?lat=51.508&lng=-0.128&radius=500"

# log in and check in at a plaque
TOKEN=$(curl -s -u user1:UserPass123 http://127.0.0.1:5001/api/v1.0/login | python -c "import sys, json; print(json.load(sys.stdin)['token'])")
curl -X POST -H "x-access-token: $TOKEN" -F history=5 -F condition=4 -F accessibility=3 \
     http://127.0.0.1:5001/api/v1.0/plaques/<id>/visits
```

---

## Data

- **Source:** [Open Plaques](https://openplaques.org) London data (2021 dump), public domain.
- **Conversion:** `convert_real_data.py` keeps only plaques in the 33 London boroughs, finds each plaque's borough from its coordinates (postcodes.io, cached in `borough_cache.json`), splits plaques that name several people, and reads birth, death and life events from the inscriptions.
- **Test data:** the `visits`, ratings and `views` in `plaques.json` are generated for testing. Photos start empty.

To rebuild `plaques.json` from the raw file: `python convert_real_data.py` (add `--offline` to use only the cache).

---

## Security

- Passwords and one-time codes are hashed with bcrypt; plain passwords are never stored.
- Logins return a JWT signed with `SECRET_KEY` (32+ random characters, checked at startup) that expires after 30 minutes. Logout, password changes and account deletion invalidate older tokens.
- Admin-only routes are protected by `@admin_required`; users can only edit or delete their own visits and photos.
- Sign-up uses a Cloudflare Turnstile human check and an emailed 6-digit code (10-minute expiry, 5 attempts, single use). Password reset gives the same reply whether or not the email exists.
- Rate limits on login, sign-up, codes, photo uploads and reports.
- All input is validated (types, ranges, lengths, IDs, boroughs, colours); errors return JSON with a 4xx status.
- Uploaded images are re-encoded with Pillow, which removes location data (EXIF/GPS), and are limited in size, pixels and number per plaque.
- Secrets live in `.env`, which is excluded from Git; `.env.example` contains no real values.
- Dependencies are pinned in `requirements.txt` and checked with `pip-audit`.

---

## Known limitations

- Categories come from Open Plaques and are sometimes inaccurate; many plaques are in the category "other".
- Birth, death and life events are read from inscription text, so a small number of names or dates may be incomplete.
- Rate limits are stored in memory and reset when the app restarts.
- Uploaded photos are stored on the server's disk in `uploads/`.

---

## Author

Doniyor Mekhmonov, BSc (Hons) Computing Systems, Ulster University London.