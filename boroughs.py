"""The 33 London boroughs (32 boroughs + the City of London). Shared by app.py and convert_real_data.py."""

LONDON_BOROUGHS = [
    "Barking and Dagenham", "Barnet", "Bexley", "Brent", "Bromley", "Camden",
    "City of London", "Croydon", "Ealing", "Enfield", "Greenwich", "Hackney",
    "Hammersmith and Fulham", "Haringey", "Harrow", "Havering", "Hillingdon",
    "Hounslow", "Islington", "Kensington and Chelsea", "Kingston upon Thames",
    "Lambeth", "Lewisham", "Merton", "Newham", "Redbridge", "Richmond upon Thames",
    "Southwark", "Sutton", "Tower Hamlets", "Waltham Forest", "Wandsworth", "Westminster",
]


def find_borough(name):
    """Return the official spelling of a borough ("camden" -> "Camden"), or None."""
    for borough in LONDON_BOROUGHS:
        if borough.lower() == name.strip().lower():
            return borough
    return None