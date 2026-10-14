"""Weather: the weather where the jukebox is, from Open-Meteo (free, no
account). An installed module (docs/contracts/extensions.md): it runs in
its sandbox with only the internet and its own bus login, publishes
jukebox/jb-weather/current (retained) for its Weather block, and fetches
again when its settings change or every few minutes.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

from shared.bus import Bus
from shared.module_sdk import module_id, settings

MODULE = module_id() or "jb-weather"
TOPIC = f"jukebox/{MODULE}/current"
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
                try:
                    while True:
                        s = settings(DEFAULTS)
                        try:
                            data = await fetch(session, await where(session, s), str(s.get("units")))
                            await bus.publish(TOPIC, data, retain=True)
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
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # the bus away: keep trying
            log.warning("bus: %s; retrying", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    asyncio.run(run())
