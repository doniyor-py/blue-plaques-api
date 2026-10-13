"""
Blue Plaques Explorer - starter data generator.




"""
import json
import random

random.seed(661)  # same "random" data every time you run it

BOROUGHS = {
    # borough: (latitude, longitude) of a central point
    "Camden": (51.5390, -0.1426),
    "Westminster": (51.4975, -0.1357),
    "Kensington and Chelsea": (51.5020, -0.1947),
    "Islington": (51.5465, -0.1058),
    "Hackney": (51.5450, -0.0553),
    "Lambeth": (51.4607, -0.1163),
    "Greenwich": (51.4892, 0.0648),
    "Richmond upon Thames": (51.4613, -0.3037),
}
COLOURS = ["blue", "blue", "blue", "green", "brown", "black"]
ORGANISATIONS = ["English Heritage", "London County Council",
                 "Greater London Council", "Borough Council", "Local History Society"]
ROLES = ["novelist", "poet", "scientist", "engineer", "composer", "painter",
         "actor", "politician", "inventor", "physician", "musician", "philosopher"]
FIRST = {"male": ["Charles", "William", "Isaac", "George", "Thomas", "Samuel", "Edward"],
         "female": ["Mary", "Ada", "Virginia", "Florence", "Emmeline", "Jane", "Dorothy"]}
LAST = ["Hartley", "Ashford", "Pembroke", "Whitmore", "Calloway", "Fenwick",
        "Langley", "Thornbury", "Ellison", "Marlowe", "Kingsley", "Radcliffe"]
EVENTS = ["born", "moved to London", "first major work", "elected", "married",
          "founded a society", "major discovery", "retired", "died"]
VERBS = ["lived here", "worked here", "was born here", "died here", "founded here"]
NOTES = ["Easy to find", "Hidden behind a tree", "Lovely street",
         "Worth the walk", "Plaque needs cleaning", "Great history"]
USERNAMES = ["user1", "user2", "user3", "user4", "user5",
             "user6", "user7", "user8", "user9", "user0"]


def make_person():
    born = random.randint(1700, 1920)
    died = min(born + random.randint(30, 95), 2000)  # plaques need time after death
    events = [{"year": born, "event": "born", "place": "London"}]
    for _ in range(random.randint(1, 3)):
        events.append({
            "year": random.randint(born + 15, died - 1),
            "event": random.choice(EVENTS[1:-1]),
            "place": random.choice(list(BOROUGHS)),
        })
    events.append({"year": died, "event": "died", "place": "London"})
    events.sort(key=lambda e: e["year"])
    gender = random.choice(["male", "female"])
    return {
        "name": random.choice(FIRST[gender]) + " " + random.choice(LAST),
        "born": born,
        "died": died,
        "gender": gender,
        "roles": random.sample(ROLES, random.randint(1, 3)),
        "life_events": events,
    }


def make_visit():
    return {
        "username": random.choice(USERNAMES),
        "date": f"2026-{random.randint(1, 9):02d}-{random.randint(1, 28):02d}",
        "note": random.choice(NOTES),
        "ratings": {                      # numbers for $avg / $sum
            "history": random.randint(1, 5),
            "condition": random.randint(1, 5),
            "accessibility": random.randint(1, 5),
        },
    }


def generate_data(count=150):
    plaques = []
    for i in range(count):
        borough = random.choice(list(BOROUGHS))
        lat, lng = BOROUGHS[borough]
        lat += random.uniform(-0.012, 0.012)
        lng += random.uniform(-0.018, 0.018)
        people = [make_person() for _ in range(random.choice([1, 1, 1, 2]))]
        first = people[0]
        last_death = max(p["died"] for p in people)
        plaques.append({
            "plaque_ref": i + 1,
            "inscription": f"{first['name']} ({first['born']}-{first['died']}) "
                           f"{first['roles'][0]} {random.choice(VERBS)}",
            "colour": random.choice(COLOURS),
            "erected_year": random.randint(max(last_death + 20, 1867), 2025),
            "organisation": random.choice(ORGANISATIONS),
            "address": f"{random.randint(1, 200)} {random.choice(LAST)} Street",
            "borough": borough,
            "location": {                 # GeoJSON: [longitude, latitude]
                "type": "Point",
                "coordinates": [round(lng, 6), round(lat, 6)],
            },
            "people": people,
            "photos": [{"url": f"https://example.com/plaques/{i + 1}.jpg",
                        "photographer": random.choice(USERNAMES)}],
            "visits": [make_visit() for _ in range(random.randint(0, 5))],
        })
    return plaques


if __name__ == "__main__":
    data = generate_data()
    with open("plaques.json", "w", encoding="utf-8") as fout:
        json.dump(data, fout, indent=4)
    print(f"Saved {len(data)} plaques to plaques.json")
