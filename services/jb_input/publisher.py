"""Stage 5: pipeline events -> bus messages, in the same jukebox/<service>/...
namespace and style as the other jb-* services.

  jukebox/jb-input/status   retained: {"online", "device": "connected"|"missing", "faults"}
  jukebox/jb-input/key      {"key": "B", "kind": "letter"|"digit"} on each selection key down
  jukebox/jb-input/gesture  {"control": "knob"|"reset", "gesture": "click"|"turn"|...,
                             "steps": n (turn/push_turn only)}
  jukebox/jb-input/action   {"action", "value", "control", "gesture"} -- only for gestures
                            given an action in config (none are, for now)
  jukebox/jb-input/raw      {"control": "key.B", "state": "down"|"up"} -- only if publish_raw,
                            except Reset's, always sent: the button lights show
                            it pressed (red while held)
  jukebox/jb-input/heartbeat  (from the shared Bus, like every service)
"""

from services.jb_input.config import SERVICE_NAME
from services.jb_input.events import Action, ControlEvent, FaultChange, Gesture, KeyPress

PREFIX = f"jukebox/{SERVICE_NAME}"
# Controls whose press/release always go out on /raw (the lights need them).
ALWAYS_RAW = {"reset"}


def topic(name: str) -> str:
    return f"{PREFIX}/{name}"


def status_payload(online: bool, device: str, faults) -> dict:
    return {"online": online, "device": device, "faults": sorted(faults)}


def message_for(event, publish_raw: bool):
    """(topic, payload) for an event, or None if it isn't published.
    FaultChange is reported through the status topic instead."""
    if isinstance(event, KeyPress):
        return topic("key"), {"key": event.key, "kind": event.kind}
    if isinstance(event, Gesture):
        payload = {"control": event.control, "gesture": event.gesture}
        if event.steps is not None:
            payload["steps"] = event.steps
        return topic("gesture"), payload
    if isinstance(event, Action):
        return topic("action"), {"action": event.action, "value": event.value,
                                 "control": event.control, "gesture": event.gesture}
    if isinstance(event, ControlEvent) and (publish_raw or event.control in ALWAYS_RAW):
        return topic("raw"), {"control": event.control, "state": event.state}
    return None


class Publisher:
    def __init__(self, bus, publish_raw: bool):
        self.bus = bus  # shared.bus.Bus
        self.publish_raw = publish_raw
        self.device = "missing"

    async def status(self, faults, t: float, online: bool = True):
        await self.bus.publish(topic("status"), status_payload(online, self.device, faults), retain=True)

    async def events(self, events, faults, t: float):
        fault_changed = False
        for e in events:
            if isinstance(e, FaultChange):
                fault_changed = True
                continue
            msg = message_for(e, self.publish_raw)
            if msg is not None:
                await self.bus.publish(*msg)
        if fault_changed:
            await self.status(faults, t)
