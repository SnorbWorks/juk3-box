"""The setup wizard's controls step, teaching (docs/Controls Setup — Plan.md,
"The step, in order", 5 and 7): what each of the cabinet's buttons sends,
learned from jb-input's learn mode (jukebox/jb-input/learn), then a try-it
with what was learned.

Pure: events in with their time, a view out. setup_wizard.py feeds it and
saves what it learned (result()) for jb-input, in
/var/lib/juk3-box/controls.json.

Teaching, in order: the knob one click to the right, then to the left (how
many steps one click sends, and which way is which, so one wired backwards
still works), its press, Cancel, then every key the selection format uses.
  - If the first key press already says the key asked for (a keypad that
    sends letters, like the owner's), it turns into a quick check: every key
    in any order.
  - A button already taught: "That's the same button as B."
  - Two presses of one button closer than a person can press (BOUNCE_S)
    mean a bouncing switch: that button gets a longer settle time.
  - A button held down for STUCK_S is reported (Cancel is wired normally
    closed: a broken wire looks like a press that never ends).
"""

from shared.jb_entry.entry import Entry, EntryChanged, Rejected, Selection
from shared.selection import Format

TURN_QUIET_S = 0.4     # a turn is over after this long without a step
BOUNCE_S = 0.06
BOUNCY_SETTLE_MS = 120
STUCK_S = 3.0


def targets(fmt: Format, knob: bool, cancel: bool) -> list[dict]:
    out = []
    if knob:
        out += [{"id": "knob.cw", "kind": "turn", "label": "→", "prompt": "Turn the knob one click to the right"},
                {"id": "knob.ccw", "kind": "turn", "label": "←", "prompt": "Now one click to the left"},
                {"id": "knob.push", "kind": "press", "label": "Knob", "prompt": "Press the knob"}]
    if cancel:
        out.append({"id": "reset", "kind": "press", "label": "Cancel", "prompt": "Press Cancel"})
    out += [{"id": f"key.{k}", "kind": "key", "label": k, "prompt": f"Press {k}"} for k in fmt.keys()]
    return out


class Teacher:
    def __init__(self, fmt: Format, knob: bool = True, cancel: bool = True):
        self.fmt = fmt
        self.knob, self.cancel = knob, cancel
        self.targets = targets(fmt, knob, cancel)
        self.by_id = {t["id"]: t for t in self.targets}
        self.learned: dict[str, dict] = {}   # target id -> {"code", "device"} or {"skipped": True}
        self.order: list[str] = []           # target ids as learned, for Back
        self.quick = False
        self.turn = None                     # the turn in progress: {"code", "device", "count", "last"}
        self.steps_per_click = None
        self.settle: dict[str, int] = {}     # code -> ms, for bouncy buttons
        self.bouncy: list[str] = []          # their labels
        self.held: dict[str, float] = {}     # code -> when it went down, until it comes up
        self.last_down: dict[str, float] = {}
        self.devices: set[str] = set()
        self.message = None
        self.stuck = None                    # a button held down, while it is
        self.trial = None

    # Where it is -------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return "try" if self.current() is None else "teach"

    def current(self) -> dict | None:
        return next((t for t in self.targets if t["id"] not in self.learned), None)

    def keymap(self) -> dict:
        return {v["code"]: tid for tid, v in self.learned.items() if "code" in v}

    def label_of(self, code: str) -> str:
        tid = self.keymap().get(code)
        return self.by_id[tid]["label"] if tid else code.removeprefix("KEY_").removeprefix("BTN_")

    # Events --------------------------------------------------------------------------

    def heard(self, ev: dict, now: float) -> None:
        """A learn event: {"device", "role", "code", "state", "t" (ms)}."""
        code, state, device = ev.get("code"), ev.get("state"), ev.get("device")
        if not isinstance(code, str) or state not in ("down", "up") or not isinstance(ev.get("t"), (int, float)):
            return
        t = ev["t"] / 1000
        self.tick(now)
        if state == "up":
            self.held.pop(code, None)
            self.tick(now)
            return
        self.held[code] = t
        gap = t - self.last_down.get(code, float("-inf"))
        self.last_down[code] = t
        turning = code in self._turn_codes() or (self.turn and self.turn["code"] == code)
        if gap < BOUNCE_S and not turning:
            self._bouncy(code)
            return
        if self.phase == "teach":
            self._teach(code, device, t)
        else:
            self._try(code)

    def tick(self, now: float) -> None:
        """Ends a turn that's gone quiet; notices a button stuck down."""
        if self.turn and now - self.turn["last"] >= TURN_QUIET_S:
            self._end_turn()
        stuck = [c for c, since in self.held.items() if now - since >= STUCK_S and c not in self._turn_codes()]
        self.stuck = (f"{self.label_of(stuck[0])} seems to be held down. If nobody's pressing it, "
                      "check its wires (Cancel is wired so a loose wire looks like this).") if stuck else None

    def _turn_codes(self) -> set:
        return {self.learned[t]["code"] for t in ("knob.cw", "knob.ccw") if "code" in self.learned.get(t, {})}

    def _bouncy(self, code: str) -> None:
        self.settle[code] = max(self.settle.get(code, 0), BOUNCY_SETTLE_MS)
        label = self.label_of(code)
        if label not in self.bouncy:
            self.bouncy.append(label)
        self.message = f"{label} looks bouncy (it sent two presses at once): fixed."

    def _teach(self, code, device, t):
        cur = self.current()
        if cur["kind"] == "turn":
            other = self.learned.get("knob.cw", {}).get("code")
            if cur["id"] == "knob.ccw" and code == other:
                self.message = "That was to the right again. Turn it one click to the left."
                return
            if self.turn and self.turn["code"] != code:
                self.turn = None
            self.turn = self.turn or {"code": code, "device": device, "count": 0}
            self.turn["count"] += 1
            self.turn["last"] = t
            return
        mapped = self.keymap().get(code)
        if mapped:
            self.message = f"That's the same button as {self.by_id[mapped]['label']}."
            return
        if cur["kind"] == "key" and not self.quick and code == f"KEY_{cur['label']}":
            self.quick = True      # it sends letters: any order from here
        target = cur
        if self.quick and code.startswith("KEY_"):
            match = self.by_id.get(f"key.{code[4:]}")
            if match and match["id"] not in self.learned:
                target = match
        self._learn(target["id"], code, device)

    def _end_turn(self):
        turn, self.turn = self.turn, None
        cur = self.current()
        if not cur or cur["kind"] != "turn":
            return
        if cur["id"] == "knob.cw":
            self.steps_per_click = max(1, min(8, turn["count"]))
            if turn["count"] > 4:
                self.message = "That looked like more than one click. If it's wrong, press Back and turn just one."
        self._learn(cur["id"], turn["code"], turn["device"])

    def _learn(self, tid, code, device):
        self.learned[tid] = {"code": code, "device": device}
        self.order.append(tid)
        if device:
            self.devices.add(device)
        self.message = None
        if self.phase == "try":
            self.start_trial()

    # The phone's buttons ---------------------------------------------------------------

    def skip(self) -> None:
        """"This key doesn't work": go on without it (fix it later)."""
        cur = self.current()
        if cur:
            self.learned[cur["id"]] = {"skipped": True}
            self.order.append(cur["id"])
            self.turn = None
            self.message = None
            if self.phase == "try":
                self.start_trial()

    def back(self) -> None:
        if self.order:
            self.learned.pop(self.order.pop(), None)
        if "knob.cw" not in self.learned:
            self.steps_per_click = None
        self.turn, self.trial, self.message = None, None, None

    # Try it ------------------------------------------------------------------------------

    def start_trial(self) -> None:
        self.entry = Entry(10_000, self.fmt)
        self.trial = {"typed": "", "picked": None, "clicks": 0, "steps": 0, "pressed": 0, "cancelled": 0,
                      "said": None}

    def _try(self, code):
        if self.trial is None:
            self.start_trial()
        tr, tid = self.trial, self.keymap().get(code)
        if tid is None:
            tr["said"] = "That button wasn't taught."
            return
        tr["said"] = None
        if tid.startswith("key."):
            for e in self.entry.key(tid[4:], 0):
                if isinstance(e, Selection):
                    tr["picked"] = {"code": e.slot, "n": e.index + 1}
                    tr["typed"] = ""
                elif isinstance(e, EntryChanged) and e.state == "partial":
                    tr["typed"] = e.typed
                elif isinstance(e, Rejected):
                    tr["said"] = {"no_letter": "A letter comes first.",
                                  "not_here": f"This jukebox has no {e.key}.",
                                  "no_such_number": "No song starts like that."}[e.reason]
        elif tid in ("knob.cw", "knob.ccw"):
            tr["steps"] += 1 if tid == "knob.cw" else -1
            tr["clicks"] = int(tr["steps"] / (self.steps_per_click or 1))
        elif tid == "knob.push":
            tr["pressed"] += 1
        elif tid == "reset":
            tr["cancelled"] += 1
            self.entry.cancel(0)
            tr["typed"] = ""

    # Out ------------------------------------------------------------------------------------

    def view(self) -> dict:
        cur = self.current()
        return {
            "phase": self.phase,
            "prompt": cur["prompt"] if cur else None,
            "now": cur["id"] if cur else None,
            "targets": [{"id": t["id"], "label": t["label"], "kind": t["kind"],
                         "state": ("skipped" if self.learned.get(t["id"], {}).get("skipped") else
                                   "done" if t["id"] in self.learned else
                                   "now" if cur and t["id"] == cur["id"] else "todo")}
                        for t in self.targets],
            "quick": self.quick,
            "turning": bool(self.turn),
            "steps_per_click": self.steps_per_click,
            "bouncy": list(self.bouncy),
            "message": self.stuck or self.message,
            "trial": self.trial,
            "can_back": bool(self.order),
        }

    def result(self) -> dict:
        """controls.json for jb-input (services/jb_input/config.py, load_controls)."""
        return {"keypads": sorted(self.devices), "keymap": self.keymap(),
                "knob_steps_per_click": self.steps_per_click or 1, "settle_ms": dict(self.settle)}
