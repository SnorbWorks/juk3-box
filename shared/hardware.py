"""Which board Juk3box is running on, and what differs between them.

The hardware is chosen in setup (detected here, confirmed by the owner;
dev plan roadmap item 2) and the right drivers and settings follow from
it. jb-lights picks its LED driver from it (docs/pi5-move.md).
"""

from pathlib import Path

MODEL_PATH = Path("/proc/device-tree/model")

# What differs per board family, for the services that have to care.
FAMILIES = {
    "pi4": {
        "name": "Raspberry Pi 4",
        # rpi_ws281x: PWM + DMA on GPIO12/13 (buttons, accent chain).
        "led_driver": "rpi_ws281x",
        # Hardware H.264 decode: background videos are encoded H.264.
        "video_codec": "h264",
        "hardware_decode": ["h264", "hevc"],
    },
    "pi5": {
        "name": "Raspberry Pi 5",
        # rpi_ws281x can't drive the RP1 I/O chip; the kernel's PIO
        # driver can (ws2812-pio overlays, jb_lights/pio_chains.py).
        "led_driver": "pio",
        # No hardware H.264 on the Pi 5; its CPU decodes 720p H.264 easily.
        "video_codec": "h264",
        "hardware_decode": ["hevc"],
    },
}


# What a board is better off without (Platform — Development Plan, "Per-board
# suggestions"). Only ever a suggestion, with the reason: the admin labels it
# and asks before turning one on; the setup wizard starts a board with them
# off. Modules declare theirs in module.json ("boards"); features inside a
# module (a setting, not a service) are listed here. "warn": allowed on,
# with the reason shown, rather than suggested off. Costs are measured on
# that board with music playing (2026-10-20, the Pi 4 at load ~4 of 4 cores).
FEATURES = {
    "pi4": {
        "background_video": {
            "why": "A moving background keeps the Pi 4's graphics and CPU busy all the time, and the music and the knob have less to work with. A still picture looks almost the same.",
        },
        "whole_home": {
            "warn": True,
            "why": "Whole-home runs a second audio pipeline and streams over Wi-Fi to the house speaker. It works on a Pi 4, with less room to spare.",
            "cost": "about 6% of a core and 16 MB",
        },
    },
}


def module_suggestion(manifest: dict, family: str) -> dict | None:
    """A module suggested off on this board: {"why", "cost"}, or None."""
    entry = ((manifest or {}).get("boards") or {}).get(family)
    if not isinstance(entry, dict) or entry.get("suggested") is not False:
        return None
    return {"why": entry.get("why") or "", "cost": entry.get("cost")}


def feature_suggestion(feature: str, family: str) -> dict | None:
    """A feature suggested off (or, with "warn", only cautioned) on this board."""
    return FEATURES.get(family, {}).get(feature)


def family_of(model: str) -> str:
    """'pi5', 'pi4', or 'other' from a device-tree model string."""
    m = model.lower()
    if "raspberry pi 5" in m or "compute module 5" in m or "raspberry pi 500" in m:
        return "pi5"
    if "raspberry pi 4" in m or "compute module 4" in m or "raspberry pi 400" in m:
        return "pi4"
    return "other"


def board(model_path: Path = MODEL_PATH) -> dict:
    """{"model": "Raspberry Pi 4 Model B Rev 1.5", "family": "pi4", ...the
    family's settings}. Off a Pi (a laptop running the tests), family
    "other" with no settings."""
    try:
        model = model_path.read_bytes().rstrip(b"\x00").decode(errors="replace").strip()
    except OSError:
        model = ""
    family = family_of(model)
    return {"model": model, "family": family, **FAMILIES.get(family, {})}
