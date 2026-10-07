# jb-input

Turns the Pico keypad's raw key events into named keys and gestures on the
bus. The key-to-pin mapping is in `Keyboard Input Build Brief.md` (repo root);
names and conventions follow the rest of the jukebox.

    device.py  -> keymap.py -> gestures.py / knob.py -> actions.py -> publisher.py
    (I/O)         (pure)       (pure)                   (pure)        (I/O)

`pipeline.py` wires the pure stages together and `runner.py` adds the device
and reconnect handling; `main.py` is the entry point.

## Behaviour (canon for now)

- **Knob:** turning publishes `turn` with signed `steps` (clockwise positive);
  pressing publishes `click`. Nothing else.
- **Selection keys (A-K, 1-0):** each press publishes the key, instantly.
- **Reset:** publishes `click`. No function is assigned yet.
- **Stuck button:** Reset or the knob held 30 s raises a fault in the status
  (a broken wire on the normally closed Reset looks exactly like this).
- **Off unless enabled in config:** hold, double-click, push-and-turn, and the
  gesture -> action table (empty).

## Messages

| Topic | Retained | Payload |
| --- | --- | --- |
| `jukebox/jb-input/status` | yes | `{"online", "device": "connected"/"missing", "faults": [...]}` |
| `jukebox/jb-input/key` | no | `{"key": "B", "kind": "letter"}` on each selection key down |
| `jukebox/jb-input/gesture` | no | `{"control": "knob", "gesture": "turn", "steps": 3}` / `{"control": "knob", "gesture": "click"}` |
| `jukebox/jb-input/action` | no | only for gestures given an action in config (none yet) |
| `jukebox/jb-input/raw` | no | `{"control": "key.B", "state": "down"}`, only if `publish_raw` |
| `jukebox/jb-input/heartbeat` | no | the standard service heartbeat |

## Config

`/etc/juk3-box/jb-input.json`, like the other services; every value has a
default, so the file is optional. `deploy/config/jb-input.json` lists them
all. Notable: `grab` (exclusive capture, default true), `gestures`
(`{"knob": ["hold", "double"]}` to enable extras), `push_turn`, `actions`
(`{"knob.click": "some.action"}`).

## Run / install

    python -m services.jb_input.main [--config /etc/juk3-box/jb-input.json]

    deploy/deploy.sh jb_input                               # unit from the shared template
    sudo cp deploy/udev/99-jb-keypad.rules /etc/udev/rules.d/
    sudo udevadm control --reload && sudo udevadm trigger   # /dev/input/jb-keypad

Needs `evdev` and a user in the `input` group. With `grab` on, the keypad's
keys stop reaching the kiosk browser directly.

## Test

    python -m pytest services/jb_input/tests shared/jb_entry/tests

No hardware needed: the pure stages take a fake clock and the runner takes a
fake device.
