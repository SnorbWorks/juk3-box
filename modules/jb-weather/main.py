"""Weather: the weather where the jukebox is, from Open-Meteo (free, no
account). An installed module (docs/contracts/installed-modules.md): it runs in
its sandbox with only the internet and its own bus login, publishes
jukebox/jb-weather/current (retained) for its Weather block, and fetches
again when its settings change or every few minutes.

The cabinet's lights (docs/contracts/lights.md, since 1.4): a palette for
the weather (lights/signal, retained) tints the light show, and while it
storms, rains or snows, now and then a cue (lights/cue): lightning, rain,
snow. Its Weather show (shows/weather.json) is an idle look made for them.
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path

import aiohttp

from shared.bus import Bus
from shared.module_sdk import module_id, settings

MODULE = module_id() or "jb-weather"
TOPIC = f"jukebox/{MODULE}/current"
SIGNAL_TOPIC = f"jukebox/{MODULE}/lights/signal"
CUE_TOPIC = f"jukebox/{MODULE}/lights/cue"
MANIFEST = json.loads((Path(__file__).parent / "module.json").read_text())
DEFAULTS = {k: v.get("default") for k, v in MANIFEST.get("owner_settings", {}).items()}

log = logging.getLogger(MODULE)

# WMO weather codes (Open-Meteo): what to say, and the icon by day / night.
CODES = [
    ({0}, "Clear", "sun", "moon"),
    ({1}, "Mostly clear", "cloud-sun", "cloud-moon"),
    ({2}, "Partly cloudy", "cloud-sun", "cloud-moon"),
    ({3}, "Cloudy", "cloud", "cloud"),
    ({45, 48}, "Fog", "cloud-fog", "cloud-fog"),
    ({51, 53, 55, 56, 57}, "Drizzle", "cloud-drizzle", "cloud-drizzle"),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "Rain", "cloud-rain", "cloud-rain"),
    ({71, 73, 75, 77, 85, 86}, "Snow", "cloud-snow", "cloud-snow"),
    ({95, 96, 99}, "Thunderstorms", "cloud-lightning", "cloud-lightning"),
]


# The weather's colours for the lights: (codes, by day, by night).
PALETTES = [
    ({0, 1}, ["#FFD27A", "#7EC8FF", "#FFF4E0"], ["#1A2A6C", "#3B4CCA", "#C0C8FF"]),
    ({2}, ["#9CC9FF", "#FFE2A8", "#D8DDE6"], ["#2B3560", "#5A6390", "#A0A8C8"]),
    ({3}, ["#8A96A8", "#C3CAD6", "#5F6B7D"], ["#3A4458", "#5F6B7D", "#8A96A8"]),
    ({45, 48}, ["#B8C0C8", "#8F99A3"], ["#5F6870", "#8F99A3"]),
    ({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}, ["#2A5CAA", "#4A8FD8", "#9CC4FF"], ["#163A78", "#2A5CAA", "#6C9AE0"]),
    ({71, 73, 75, 77, 85, 86}, ["#FFFFFF", "#CFE6FF", "#9AB8E0"], ["#CFE6FF", "#9AB8E0", "#5A78B0"]),
    ({95, 96, 99}, ["#3A2A6E", "#5B3FB0", "#1D1B3A"], ["#2A1F55", "#4B3390", "#120F28"]),
]
# What the lights get now and then, by the weather: (codes, cue, seconds apart (from, to), strength (from, to)).
CUES = [
    ({95, 96, 99}, "lightning", (12, 50), (0.6, 1.0)),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "rain", (25, 60), (0.4, 0.7)),
    ({51, 53, 55, 56, 57}, "rain", (45, 90), (0.3, 0.5)),
    ({71, 73, 75, 77, 85, 86}, "snow", (30, 70), (0.4, 0.6)),
]


def signal(code: int, is_day: bool) -> dict:
    """The weather's palette for the cabinet's lights (fog dims them a little)."""
    for codes, day, night in PALETTES:
        if code in codes:
            return {"v": 1, "palette": day if is_day else night, "intensity": 0.8 if code in (45, 48) else 1.0}
    return {"v": 1, "palette": ["#C3CAD6"], "intensity": 1.0}


def cue_for(code: int):
    for codes, cue, apart, strength in CUES:
        if code in codes:
            return cue, apart, strength
    return None


async def send_cues(bus, now: dict):
    """While the weather calls for it, a cue every so often (now: the latest
    reading, updated by run())."""
    while True:
        c = cue_for(now.get("code", -1))
        if c is None:
            await asyncio.sleep(30)
            continue
        cue, apart, strength = c
        await asyncio.sleep(random.uniform(*apart))
        if cue_for(now.get("code", -1)) == c:
            await bus.publish(CUE_TOPIC, {"v": 1, "cue": cue, "strength": round(random.uniform(*strength), 2)})


def describe(code: int, is_day: bool) -> tuple[str, str]:
    for codes, text, day, night in CODES:
        if code in codes:
            return text, day if is_day else night
    return "", "cloud"


# Until the owner picks a location in the admin.
DEFAULT_PLACE = {"name": "New York", "latitude": 40.7143, "longitude": -74.006}


async def where(session: aiohttp.ClientSession, s: dict) -> dict:
    """The place to report: the location the owner picked in the admin
    (exact: name and coordinates), else a "place" name from version 1.0
    (looked up), else New York."""
    loc = s.get("location")
    if isinstance(loc, dict) and isinstance(loc.get("latitude"), (int, float)):
        return loc
    if isinstance(s.get("place"), str) and s["place"].strip():
        async with session.get("https://geocoding-api.open-meteo.com/v1/search",
                               params={"name": s["place"], "count": 1}) as r:
            found = (await r.json()).get("results") or []
        if found:
            return found[0]
    return DEFAULT_PLACE


async def fetch(session: aiohttp.ClientSession, p: dict, units: str) -> dict:
    async with session.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": p["latitude"], "longitude": p["longitude"],
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit" if units == "fahrenheit" else "celsius",
        "timezone": "auto", "forecast_days": 1,
    }) as r:
        w = await r.json()
    now, day = w["current"], w["daily"]
    text, icon = describe(int(now["weather_code"]), bool(now.get("is_day", 1)))
    deg = "°"
    return {
        "v": 1,
        "place": p.get("name", ""),
        "temp": f"{round(now['temperature_2m'])}{deg}",
        "condition": text,
        "icon": icon,
        "high": f"{round(day['temperature_2m_max'][0])}{deg}",
        "low": f"{round(day['temperature_2m_min'][0])}{deg}",
        "units": units,
        # For other modules (Weather Mix): the raw reading.
        "code": int(now["weather_code"]),
        "is_day": bool(now.get("is_day", 1)),
        "temp_c": round(now["temperature_2m"] if units != "fahrenheit" else (now["temperature_2m"] - 32) * 5 / 9, 1),
        "updated": time.time(),
    }


async def run():
    while True:
        try:
            async with Bus(MODULE) as bus, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                heartbeat = asyncio.create_task(bus.run_heartbeat())
                changed = asyncio.Event()

                async def listen():
                    # The admin names a module when its settings change.
                    async for topic, payload in await bus.subscribe("jukebox/jb-admin/changed"):
                        if f"module:{MODULE}" in (payload.get("what") or []):
                            changed.set()

                listener = asyncio.create_task(listen())
                now = {}
                cues = asyncio.create_task(send_cues(bus, now))
                try:
                    while True:
                        s = settings(DEFAULTS)
                        try:
                            data = await fetch(session, await where(session, s), str(s.get("units")))
                            await bus.publish(TOPIC, data, retain=True)
                            await bus.publish(SIGNAL_TOPIC, signal(data["code"], data["is_day"]), retain=True)
                            now.update(code=data["code"])
                            log.info("%s: %s %s", data["place"], data["temp"], data["condition"])
                        except Exception as e:      # offline, a bad place: try again later
                            log.warning("weather: %s", e)
                        changed.clear()
                        try:
                            await asyncio.wait_for(changed.wait(), timeout=max(5, float(s.get("refresh") or 60)) * 60)
                        except asyncio.TimeoutError:
                            pass
                finally:
                    heartbeat.cancel()
                    listener.cancel()
                    cues.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # the bus away: keep trying
            log.warning("bus: %s; retrying", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    asyncio.run(run())
