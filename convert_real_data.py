"""
Convert the real Open Plaques London data into the Blue Plaques Explorer structure.

Input : london_plaques_raw.json   Open Plaques data (2021 dump, public domain),
                                  downloaded from londontravelgeek.co.uk/plaques.json
Output: plaques.json              ready for:  mongoimport ... --jsonArray --drop

From the real data : plaque_ref, title, inscription, colour, organisation,
                     erected_year, address, category, location, people[].name
Derived            : people[].born / died  (read from the inscription, e.g. "1900-1971")
                     people[].roles        (the plaque's category)
                     borough               (looked up from the coordinates with postcodes.io)
App data           : views, photos, visits (seeded TEST check-ins for the demo)

Run:  python convert_real_data.py              (looks up boroughs online)
      python convert_real_data.py --offline    (address matching only, no internet)
"""
import json
import os
import random
import re
import sys

import requests
from bson import ObjectId

random.seed(661)                         # same test visits every run

RAW_FILE = "london_plaques_raw.json"
OUT_FILE = "plaques.json"
CACHE_FILE = "borough_cache.json"        # saves postcodes.io answers so re-runs are fast

BOROUGHS = [
    "City of London", "Barking and Dagenham", "Barnet", "Bexley", "Brent", "Bromley",
    "Camden", "Croydon", "Ealing", "Enfield", "Greenwich", "Hackney",
    "Hammersmith and Fulham", "Haringey", "Harrow", "Havering", "Hillingdon",
    "Hounslow", "Islington", "Kensington and Chelsea", "Kingston upon Thames",
    "Lambeth", "Lewisham", "Merton", "Newham", "Redbridge", "Richmond upon Thames",
    "Southwark", "Sutton", "Tower Hamlets", "Waltham Forest", "Wandsworth", "Westminster",
]
SHORT_NAMES = {"Kensington": "Kensington and Chelsea", "Chelsea": "Kensington and Chelsea",
               "Hammersmith": "Hammersmith and Fulham", "Fulham": "Hammersmith and Fulham",
               "Richmond": "Richmond upon Thames", "Kingston": "Kingston upon Thames"}

USERNAMES = ["user" + str(i) for i in range(10)]          # match create_users.py
NOTES = ["Easy to find", "Hidden behind a tree", "Lovely street",
         "Worth the walk", "Plaque needs cleaning", "Great history"]


# ---------- borough ----------

def borough_from_address(address):
    text = (address or "").lower()
    for name in sorted(BOROUGHS, key=len, reverse=True):
        if name.lower() in text:
            return name
    for short, full in SHORT_NAMES.items():
        if re.search(r"\b" + short.lower() + r"\b", text):
            return full
    return None


def lookup_boroughs(points):
    """Ask postcodes.io for the local authority of each (lng, lat). Returns {"lng,lat": name}."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            cache = json.load(f)

    todo = [p for p in points if f"{p[0]},{p[1]}" not in cache]
    print(f"Borough lookup: {len(points) - len(todo)} cached, {len(todo)} to fetch")

    for start in range(0, len(todo), 100):                # the API takes 100 points per request
        batch = todo[start:start + 100]
        body = {"geolocations": [{"longitude": lng, "latitude": lat, "radius": 1000, "limit": 1}
                                 for lng, lat in batch]}
        try:
            reply = requests.post("https://api.postcodes.io/postcodes", json=body, timeout=30)
            results = reply.json().get("result") or []
        except (requests.RequestException, ValueError) as e:
            print("  lookup failed for one batch, using addresses instead:", e)
            continue
        for (lng, lat), item in zip(batch, results):
            found = (item or {}).get("result") or []
            cache[f"{lng},{lat}"] = found[0].get("admin_district") if found else None
        print(f"  {min(start + 100, len(todo))}/{len(todo)}")

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)
    return cache


# ---------- one plaque ----------

def make_visit():
    return {
        "_id": {"$oid": str(ObjectId())},
        "username": random.choice(USERNAMES),
        "date": f"2026-{random.randint(1, 9):02d}-{random.randint(1, 28):02d}",
        "note": random.choice(NOTES),
        "ratings": {"history": random.randint(1, 5),
                    "condition": random.randint(1, 5),
                    "accessibility": random.randint(1, 5)},
    }


def clean_organisation(org):
    org = (org or "").strip()
    if not org:
        return None
    if org.startswith("administered by English Heritage"):
        return "English Heritage"
    return org


# ---- Step A: find every "Name 1812-1870" in the inscription -------------
NAME = r"((?:[A-Z][\w'’.\-]*\s+(?:(?:de|van|von|du|da|di|la|le)\s+)?){0,5}[A-Z][\w'’\-]+)"
PERSON = re.compile(NAME + r",?\s*\(?\s*(?:c\.\s*)?(\d{4})\s*[-–]\s*(?:c\.\s*)?(\d{4})\)?")
TITLES = ("Sir ", "Dame ", "Lady ", "Lord ", "Dr ", "Dr. ", "Private ", "Captain ")
STAY_WORDS = re.compile(r"\b(lived|here|from|worked|stayed|studied|between|founded|in|at|during|until)\W*$", re.I)


def find_people(inscription):
    people = []
    for match in PERSON.finditer(inscription):
        name, born, died = match.group(1).strip(), int(match.group(2)), int(match.group(3))
        before = inscription[max(0, match.start() - 25):match.start()]
        if STAY_WORDS.search(before) or STAY_WORDS.search(name):
            continue                          # "lived here 1915-1924" is not a lifetime
        if not 15 <= died - born <= 110:
            continue                          # not a believable lifetime
        name = re.split(r"(?<=\w{3})\.\s+", name)[-1]   # "Hampstead. Clarkson Stanfield" -> "Clarkson Stanfield"
        if name.startswith("The "):
            continue                          # "The Horniman Museum" is a place, not a person
        for title in TITLES:
            if name.startswith(title):
                name = name[len(title):]
        people.append({"name": name, "born": born, "died": died,
                       "start": match.start(), "end": match.end()})
    return people


# ---- Step B: find dated events like "lived here 1916-1925" ---------------
EVENT = re.compile(
    r"\b((?:lived|worked|stayed|studied|founded|wrote|composed|designed|built|"
    r"opened|played|performed|taught|died|was born|born)\b[^.;]{0,50}?)"
    r"\s*(?:in\s+|from\s+|between\s+)?(\d{4})(?:\s*(?:[-–]|and|to)\s*(\d{4}))?", re.I)


def find_events(text, address):
    events = []
    for match in EVENT.finditer(text):
        words = re.sub(r"\s+(in|from|between|the years)$", "", match.group(1).strip(), flags=re.I)
        words = words.rstrip(",")
        event = {"year": int(match.group(2)), "event": words[0].lower() + words[1:]}
        if match.group(3):
            event["to"] = int(match.group(3))
        at_plaque = any(w in words.lower() for w in ("here", "this house", "this site"))
        event["place"] = address if at_plaque else None
        events.append(event)
    return events


# ---- Step C: put it together -------------------------------------------
def build_people(inscription, subject, category, address):
    found = find_people(inscription)
    if not found:                             # no dates: keep the subject name only
        if not subject:
            return []
        found = [{"name": subject.strip(), "born": None, "died": None,
                  "start": 0, "end": 0}]

    if len(found) == 1 and subject:
        found[0]["name"] = subject.strip()    # the dataset's own name is cleaner

    people = []
    for i, person in enumerate(found):
        # this person's text runs until the next person starts
        last = i == len(found) - 1
        stop = len(inscription) if last else found[i + 1]["start"]
        own_text = inscription[person["end"]:stop]
        events = []
        if person["born"]:
            events.append({"year": person["born"], "event": "born", "place": None})
        for event in find_events(own_text, address):
            if event["event"].lower().startswith(("born", "was born", "died")):
                continue                      # born/died already added
            events.append(event)
        if person["died"]:
            events.append({"year": person["died"], "event": "died", "place": None})
        people.append({"name": person["name"], "born": person["born"],
                       "died": person["died"],
                       "roles": [category] if category else [],
                       "life_events": events})

    # shared text after the last person ("... lived here") belongs to everyone
    if len(people) > 1:
        shared = [e for e in people[-1]["life_events"] if e["event"] not in ("born", "died")]
        for person in people[:-1]:
            person["life_events"][-1:-1] = [dict(e) for e in shared]
    return people


def convert(raw, borough_lookup):
    try:
        lat, lng = float(raw["lat"]), float(raw["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    inscription = (raw.get("inscription") or "").strip()
    if not inscription:
        return None

    erected = str(raw.get("erected") or "").strip()

    people = build_people(inscription, raw.get("subject"),
                          raw.get("category"), raw.get("address"))
    for person in people:
        person.pop("start", None)
        person.pop("end", None)

    borough = (borough_lookup.get(f"{lng},{lat}")
               or borough_from_address(raw.get("address"))
               or "Unknown")

    return {
        "plaque_ref": raw["id"],
        "title": raw.get("title"),
        "inscription": inscription,
        "colour": (raw.get("colour") or "").strip().lower() or "unknown",
        "erected_year": int(erected) if erected.isdigit() else None,
        "organisation": clean_organisation(raw.get("org")),
        "address": raw.get("address"),
        "borough": borough,
        "category": raw.get("category"),
        "location": {"type": "Point", "coordinates": [lng, lat]},
        "views": 0,
        "people": people,
        "photos": [],
        "visits": [make_visit() for _ in range(random.randint(0, 5))],
    }


if __name__ == "__main__":
    with open(RAW_FILE, encoding="utf-8") as f:
        data = json.load(f)
    raw_plaques = data["plaques"] if isinstance(data, dict) else data

    points = [(float(r["lng"]), float(r["lat"])) for r in raw_plaques
              if r.get("lat") not in (None, "") and r.get("lng") not in (None, "")]
    lookup = {} if "--offline" in sys.argv else lookup_boroughs(points)

    converted = [p for p in (convert(r, lookup) for r in raw_plaques) if p]
    converted = [p for p in converted if p["borough"] in BOROUGHS]   # Greater London only
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(converted, f, indent=2, ensure_ascii=False)

    def count(test):
        return sum(1 for p in converted if test(p))

    print(f"\nRead {len(raw_plaques)} plaques, kept {len(converted)}")
    print(f"  with a person ............ {count(lambda p: p['people'])}")
    print(f"  people in total .......... {sum(len(p['people']) for p in converted)}")
    print(f"  plaques with 2+ people ... {count(lambda p: len(p['people']) > 1)}")
    print(f"  life events in total ..... {sum(len(x['life_events']) for p in converted for x in p['people'])}")
    print(f"  with erected year ........ {count(lambda p: p['erected_year'])}")
    print(f"  borough found ............ {count(lambda p: p['borough'] != 'Unknown')}")
    print(f"  test visits created ...... {sum(len(p['visits']) for p in converted)}")
    print(f"Saved to {OUT_FILE}")
