"""Stage 5: pipeline events -> bus messages, in the same jukebox/<service>/...
namespace and style as the other jb-* services.

  jukebox/jb-input/status   retained: {"online", "device": "connected"|"missing" (a keypad),
                            "keyboards": n, "learning": bool, "faults"}
  jukebox/jb-input/key      {"key": "B", "kind": "letter"|"digit"} on each selection key down
  jukebox/jb-input/gesture  {"control": "knob"|"reset", "gesture": "click"|"turn"|...,
                             "steps": n (turn/push_turn only)}
  jukebox/jb-input/action   {"action", "value", "control", "gesture"} -- only for gestures
                            given an action in config (none are, for now)
  jukebox/jb-input/raw      {"control": "key.B", "state": "down"|"up"} -- only if publish_raw,
                            except Reset's, always sent: the button lights show
                            it pressed (red while held)
  jukebox/jb-input/learn    while the setup wizard is learning, every key event of every
                            device, instead of the above: {"device": id, "name", "role":
                            "keypad"|"keyboard"|"other", "code": "KEY_A", "state":
                            "down"|"up", "t": ms (monotonic, for spotting a bouncing switch)}
  jukebox/jb-input/heartbeat  (from the shared Bus, like every service)
"""

from services.jb_input.config import SERVICE_NAME
from services.jb_input.events import Action, ControlEvent, FaultChange, Gesture, KeyPress

PREFIX = f"jukebox/{SERVICE_NAME}"
# Controls whose press/release always go out on /raw (the lights need them).
ALWAYS_RAW = {"reset"}


def topic(name: str) -> str:
    return f"{PREFIX}/{name}"


def status_payload(online: bool, device: str, faults, keyboards: int = 0, learning: bool = False) -> dict:
    return {"online": online, "device": device, "keyboards": keyboards, "learning": learning,
            "faults": sorted(faults)}


def learn_payload(dev, role: str, raw) -> dict:
    return {"device": dev.id, "name": dev.name, "role": role, "code": raw.code,
            "state": "down" if raw.value == 1 else "up", "t": round(raw.t * 1000)}


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
        self.keyboards = 0
        self.learning = False

    async def status(self, faults, t: float, online: bool = True):
        await self.bus.publish(topic("status"), status_payload(online, self.device, faults, self.keyboards,
                                                               self.learning), retain=True)

    async def learn(self, dev, role: str, raw):
        await self.bus.publish(topic("learn"), learn_payload(dev, role, raw))

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
