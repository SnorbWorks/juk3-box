"""Finding a place for a "location" setting (docs/contracts/extensions.md,
owner_settings): a ZIP code, or a town with or without its state or
country, looked up with Open-Meteo's free geocoding. The owner picks one
of the matches, and exactly that (name, state, coordinates) is saved, so a
module never has to guess which Portland.
"""

import re

import aiohttp

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
ZIP_RE = re.compile(r"^\s*(\d{5})(-\d{4})?\s*$")

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def parse(query: str) -> tuple[dict, str | None]:
    """The geocoding query for what the owner typed, and the state or
    country to keep (from "Portland, OR" or "Paris, France")."""
    q = " ".join(str(query).split())[:100]
    m = ZIP_RE.match(q)
    if m:
        return {"name": m.group(1), "countryCode": "US"}, None
    if "," in q:
        name, _, region = q.rpartition(",")
        region = region.strip()
        return {"name": name.strip()}, US_STATES.get(region.upper(), region)
    return {"name": q}, None


def label(r: dict) -> str:
    parts = [r.get("name")]
    if r.get("admin1") and r.get("admin1") != r.get("name"):
        parts.append(r["admin1"])
    if r.get("country"):
        parts.append("US" if r.get("country_code") == "US" else r["country"])
    return ", ".join(p for p in parts if p)


async def find(query: str) -> list[dict]:
    params, region = parse(query)
    if len(params["name"]) < 2:
        return []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(GEOCODE, params={**params, "count": 10, "language": "en"}) as r:
            results = (await r.json()).get("results") or []
    if region:
        want = region.lower()
        narrowed = [r for r in results if want in (str(r.get("admin1", "")).lower(), str(r.get("country", "")).lower(),
                                                    str(r.get("country_code", "")).lower())]
        results = narrowed or results
    return [{"label": label(r), "name": r.get("name"), "admin1": r.get("admin1"), "country": r.get("country"),
             "latitude": r.get("latitude"), "longitude": r.get("longitude"), "timezone": r.get("timezone")}
            for r in results if isinstance(r.get("latitude"), (int, float)) and isinstance(r.get("longitude"), (int, float))][:8]


def clean(v) -> dict | None:
    """A saved location as a module may rely on it, or None if unusable."""
    if not isinstance(v, dict):
        return None
    lat, lon = v.get("latitude"), v.get("longitude")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if not isinstance(v.get("name"), str) or not v["name"].strip():
        return None
    out = {"name": v["name"][:100], "latitude": float(lat), "longitude": float(lon),
           "label": str(v.get("label") or v["name"])[:200]}
    for k in ("admin1", "country", "timezone"):
        if isinstance(v.get(k), str):
            out[k] = v[k][:100]
    return out
