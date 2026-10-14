"""What the weather sounds like: each mood's Last.fm tags (weighted), the
library genres to fall back on without Last.fm, and the names a mix can
take. Names vary with the time of day and the season and are never the
same as the last few, so the mix doesn't feel stale.
"""

import random

# mood -> tags (Last.fm, with weights), genres (fallback), names
MOODS = {
    "hot": {
        "tags": {"summer": 1.0, "beach": 0.8, "feel good": 0.8, "party": 0.5},
        "genres": ["Pop", "Dance", "Reggae", "Latin", "Hip-Hop/Rap"],
        "names": ["Heat Wave", "Sun's Out, Songs Out", "Hot Hits", "Too Hot to Sit Still", "Summer State of Mind",
                  "Sizzle Sessions", "Pool Party", "Scorcher Sounds", "Endless Summer"],
    },
    "sunny": {
        "tags": {"happy": 1.0, "feel good": 1.0, "sunny": 0.8, "upbeat": 0.7},
        "genres": ["Pop", "Dance", "Reggae", "Country", "Rock"],
        "names": ["Sunny Tunes", "Sunshine Sounds", "Blue Sky Bops", "On the Bright Side", "Here Comes the Sun",
                  "Clear Skies Ahead", "Vitamin D Sides", "Good Day Grooves", "Sun-Kissed Songs", "Bright Spot"],
    },
    "crisp": {
        "tags": {"feel good": 0.9, "indie": 0.8, "acoustic": 0.7, "mellow": 0.5},
        "genres": ["Alternative", "Singer/Songwriter", "Indie Rock", "Country", "Pop"],
        "names": ["Crisp & Clear", "Cool Blue Sky", "Fresh Air Favorites", "Bright and Brisk", "Clear Day Classics",
                  "Sweater Weather", "Chill in the Air"],
    },
    "night": {
        "tags": {"night": 1.0, "chill": 0.8, "smooth": 0.7, "downtempo": 0.6},
        "genres": ["R&B/Soul", "Electronic", "Jazz", "Alternative"],
        "names": ["Night Drive", "Under the Stars", "Moonlit Mix", "After Dark", "Starry Night Sounds",
                  "Clear Night Grooves", "Midnight Hour", "Late Night Lounge"],
    },
    "partly": {
        "tags": {"chill": 1.0, "indie pop": 0.8, "feel good": 0.7, "mellow": 0.5},
        "genres": ["Alternative", "Pop", "Indie Rock", "Singer/Songwriter"],
        "names": ["Partly Sunny Picks", "Sun & Clouds", "Silver Linings", "Breaks in the Clouds", "Half & Half",
                  "Patchy Sunshine", "Easy Breezy"],
    },
    "cloudy": {
        "tags": {"chill": 1.0, "indie": 0.9, "dreamy": 0.7, "mellow": 0.7},
        "genres": ["Alternative", "Indie Rock", "Singer/Songwriter", "Electronic"],
        "names": ["Gray Sky Grooves", "Overcast & Easy", "Cloud Cover", "Cloudy with a Chance of Bops",
                  "Gray Day Gold", "Under the Clouds", "Soft Light Sessions", "Shades of Gray"],
    },
    "fog": {
        "tags": {"ambient": 1.0, "dreamy": 1.0, "atmospheric": 0.8, "ethereal": 0.7},
        "genres": ["Electronic", "Alternative", "Instrumental", "Soundtrack"],
        "names": ["Foggy Notions", "Into the Mist", "Fog Bank", "Haze Days", "Low Visibility", "Soft Focus"],
    },
    "drizzle": {
        "tags": {"rainy day": 1.0, "mellow": 0.9, "acoustic": 0.8, "chill": 0.6},
        "genres": ["Singer/Songwriter", "Alternative", "Indie Rock", "R&B/Soul"],
        "names": ["Drizzle Days", "Light Rain Listening", "Sprinkle Sessions", "Misty Window Music",
                  "Pitter-Patter Picks", "Damp & Dreamy"],
    },
    "rain": {
        "tags": {"rainy day": 1.0, "melancholy": 0.8, "mellow": 0.8, "sad": 0.5},
        "genres": ["Alternative", "Singer/Songwriter", "R&B/Soul", "Indie Rock"],
        "names": ["Rainy Day Records", "Raindrops & Records", "Puddle Jumpers", "Rain on the Roof",
                  "Window Seat Songs", "Umbrella Anthems", "Stormy Monday Mood", "Wet Weather Wax", "Rain Check"],
    },
    "storm": {
        "tags": {"epic": 1.0, "dark": 0.8, "intense": 0.8, "hard rock": 0.6},
        "genres": ["Rock", "Hard Rock", "Metal", "Alternative", "Soundtrack"],
        "names": ["Storm Watch", "Thunder Road Trip", "Lightning Round", "Rolling Thunder", "Riders on the Storm",
                  "Batten Down the Hatches", "Electric Skies"],
    },
    "snow": {
        "tags": {"winter": 1.0, "cozy": 0.9, "acoustic": 0.7, "mellow": 0.6},
        "genres": ["Singer/Songwriter", "Alternative", "Jazz", "Vocal", "Holiday"],
        "names": ["Snow Day Spins", "Flurry of Favorites", "Let It Snow", "Snowbound Sounds", "Fireside Flurries",
                  "White Out", "Cozy Cabin Mix"],
    },
}

# Time of day: a tag nudged in, and names that only fit then.
DAYPARTS = {
    "morning": {"tag": "morning", "names": {"sunny": ["Morning Sunshine", "Rise & Shine"], "rain": ["Rainy Morning Coffee"],
                                            "cloudy": ["Slow Gray Morning"], "crisp": ["Crisp Morning Start"]}},
    "afternoon": {"tag": None, "names": {"sunny": ["Sunny Afternoon"], "hot": ["Lazy Hot Afternoon"]}},
    "evening": {"tag": "evening", "names": {"sunny": ["Golden Hour", "Sunset Sessions"], "crisp": ["Golden Hour"],
                                            "partly": ["Pink Sky Playlist"], "rain": ["Rainy Evening In"]}},
    "night": {"tag": "late night", "names": {"rain": ["Rain After Midnight"], "storm": ["Midnight Thunder"],
                                             "cloudy": ["Starless Night"], "snow": ["Silent Night Snow"]}},
}

SEASONS = {
    "winter": {"tag": "winter", "names": {"snow": ["Winter Wonderland"], "rain": ["Winter Rain"], "crisp": ["Winter Sun"]}},
    "spring": {"tag": "spring", "names": {"rain": ["April Showers"], "sunny": ["Spring Fever"], "partly": ["Spring Sprinkle"]}},
    "summer": {"tag": "summer", "names": {"night": ["Summer Nights"], "storm": ["Summer Storm"], "sunny": ["Summertime Sides"]}},
    "autumn": {"tag": "autumn", "names": {"crisp": ["Fall Colors"], "rain": ["Autumn Rain"], "cloudy": ["Falling Leaves"]}},
}

ICONS = {"hot": "sun", "sunny": "sun", "crisp": "sun", "night": "moon", "partly": "cloud-sun", "cloudy": "cloud",
         "fog": "cloud-fog", "drizzle": "cloud-drizzle", "rain": "cloud-rain", "storm": "cloud-lightning", "snow": "cloud-snow"}


def mood_of(weather: dict) -> str:
    """The mood for a Weather reading (WMO code, day/night, °C)."""
    code = int(weather.get("code", -1))
    day = bool(weather.get("is_day", True))
    temp = weather.get("temp_c")
    if code in (95, 96, 99):
        return "storm"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (45, 48):
        return "fog"
    if code == 3:
        return "cloudy"
    if code in (1, 2):
        return "partly" if day else "night"
    if not day:
        return "night"
    if isinstance(temp, (int, float)):
        if temp >= 27:
            return "hot"
        if temp < 13:
            return "crisp"
    return "sunny"


def daypart(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(month, "autumn")


def recipe(mood: str, part: str, when: str) -> dict[str, float]:
    """The weighted tags for a mix: the mood's, with the time of day and the
    season nudged in lightly."""
    tags = dict(MOODS[mood]["tags"])
    for extra, w in ((DAYPARTS[part]["tag"], 0.4), (SEASONS[when]["tag"], 0.3)):
        if extra and extra not in tags:
            tags[extra] = w
    return tags


def name_for(mood: str, part: str, when: str, recent: list[str], rng: random.Random) -> str:
    """A name for the mix: one that fits the moment if there is one, and not
    one used in the last several mixes."""
    special = DAYPARTS[part]["names"].get(mood, []) + SEASONS[when]["names"].get(mood, [])
    pool = special * 2 + MOODS[mood]["names"]      # fitting names twice as likely
    fresh = [n for n in pool if n not in recent] or pool
    return rng.choice(fresh)
