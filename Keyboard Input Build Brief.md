# Keyboard Input: Build Brief

Oct 6, 2026 · @shawn

## Purpose and scope

Build the input path for a cabinet with 20 selection keys, one Reset button and one rotary encoder, so that the rest of the app receives clean, named events and never touches raw key codes.

There are three deliverables:

1. **Pico firmware** (CircuitPython). The Pico presents itself to the Raspberry Pi as a plain USB keyboard. It scans the hardware and sends key down and key up reports. It holds no application logic.
2. **`jb-input` service** (Python 3, asyncio, systemd) on the Raspberry Pi. It captures the Pico's keyboard exclusively, turns raw key events into named keys, gestures and actions, and publishes them on the local MQTT broker (mosquitto).

3) **Selection entry module** (pure Python, no I/O). It combines one letter and one digit into a slot such as `B7`. The project plan assigns letter and digit entry to `jb-core`, so build it as a self-contained module with its own tests that `jb-core` imports.

Out of scope: what a selection does (queueing, playback), volume handling, the on-screen UI and the LED lighting. Those services subscribe to the events defined here.

Design rules the builder must keep:

- **The Pico is only a keyboard.** All timing (click, double-click, hold) and all meaning (selection, volume, paging) live on the Pi, where they can be changed without reflashing.
- **Every pin, key code, timing value and topic name comes from one config file per side.** Nothing is hard-coded in logic.
- **The gesture-to-action mapping is a draft.** It must be a table in config, not branches in code.

## Hardware and pinout

The controller is a Raspberry Pi Pico (RP2040) connected to the Raspberry Pi 4 by USB. All inputs use the Pico's internal pull-ups; there are no external resistors or diodes.

### Selection keys: 4 x 5 matrix

| Row | Keys (column order) | GPIO | Physical pin |
| --- | --- | --- | --- |
| 0 | A B C D E | GP5 | 7 |
| 1 | F G H J K | GP6 | 9 |
| 2 | 1 2 3 4 5 | GP7 | 10 |
| 3 | 6 7 8 9 0 | GP8 | 11 |

| Column | Keys (row order) | GPIO | Physical pin |
| --- | --- | --- | --- |
| 0 | A F 1 6 | GP9 | 12 |
| 1 | B G 2 7 | GP10 | 14 |
| 2 | C H 3 8 | GP11 | 15 |
| 3 | D J 4 9 | GP12 | 16 |
| 4 | E K 5 0 | GP13 | 17 |

The letters skip I: the ten letters are A B C D E F G H J K. The digit order is 1 to 9, then 0. Key number in the matrix = row x 5 + column.

### Other controls

| Control | GPIO | Physical pin | Electrical behaviour |
| --- | --- | --- | --- |
| Reset button | GP14 | 19 (other terminal to GND, pin 18) | **Normally closed.** Reads LOW at rest and HIGH when pressed |
| Encoder A (CLK) | GP2 | 4 | KY-040 module, quadrature |
| Encoder B (DT) | GP3 | 5 | KY-040 module, quadrature |
| Encoder push (SW) | GP4 | 6 | Normally open to GND. Reads LOW when pressed |
| Encoder power | 3V3 OUT | 36 | Powers the module's pull-ups at 3.3 V |
| Encoder ground | GND | 38 |  |

Spare pins: GP0, GP1, GP15 to GP22, GP26 to GP28.

The assignment of CLK, DT and SW to GP2, GP3 and GP4 is assumed from the wiring order and must be confirmed on the bench (see Open decisions). A and B must stay on adjacent GPIO numbers for CircuitPython's `rotaryio`.

## System overview

&#91;embedded content: input path · Pico, jb-input, subscribers\]

The Pico reports raw key down and key up over USB. `jb-input` is the only place that knows about timing or meaning, and everything downstream sees named events on MQTT. The middle band and the Pico firmware are what this brief asks for, plus the selection entry module that `jb-core` hosts.

## Pico firmware

The firmware scans the inputs and reports them as standard USB keyboard keys. It is written in CircuitPython (current stable release for the Pico) and uses only built-in modules plus `adafruit_hid`.

### Files

| File | Contents |
| --- | --- |
| `boot.py` | USB identity, keyboard-only HID, maintenance-mode check |
| `code.py` | Main loop: read events, send HID reports |
| `config.py` | Pins, key codes, debounce interval, encoder settings. No logic |
| `lib/adafruit_hid/` | Adafruit HID library |

### HID key map

The Pi captures this keyboard exclusively, so the choice of key codes only has to be unambiguous.

| Control | Sends | Report style |
| --- | --- | --- |
| Letters A to K (no I) | Keycode `A` to `K` | Down on press, up on release |
| Digits 1 to 9, 0 | Keycode `ONE` to `NINE`, `ZERO` | Down on press, up on release |
| Reset | `ESCAPE` | Down on press, up on release |
| Encoder push | `ENTER` | Down on press, up on release |
| Encoder clockwise, one detent | `RIGHT_ARROW` | One tap (down then up) per detent |
| Encoder counter-clockwise, one detent | `LEFT_ARROW` | One tap per detent |

### Scanning

- **Matrix:** `keypad.KeyMatrix(row_pins, column_pins)` with a 20 ms scan interval, which also debounces. Map `event.key_number` to a key code through a 20-entry list in `config.py`.
- **Reset:** read GP14 with `digitalio` and an internal **pull-up**, treating HIGH as pressed, and debounce it in the main loop (20 ms). **Do not use `keypad.Keys((GP14,), value_when_pressed=True, pull=True)`:** with `value_when_pressed=True`, `keypad`'s `pull=True` enables a pull-*down*, so when the normally closed switch opens the pin stays LOW and presses are never seen (found on the bench, Oct 6 2026).
- **Encoder push:** `keypad.Keys((GP4,), value_when_pressed=False, pull=True)`.
- **Encoder rotation:** `rotaryio.IncrementalEncoder(GP2, GP3, divisor=4)`, giving one count per detent on a KY-040. Each loop, read the change in position since the last loop and send that many taps. `config.py` holds `ENCODER_DIVISOR` and `ENCODER_INVERT` so the bench can correct step size and direction without code changes.

### Main loop

1. Drain every pending event from the three `keypad` objects. For each, call `keyboard.press(code)` or `keyboard.release(code)`.
2. Read the encoder delta. Send up to 8 taps per loop and carry any remainder to the next loop, so a fast spin cannot stall key scanning.
3. Sleep 1 ms.

Requirements:

- Never block. No `time.sleep` longer than 1 ms and no waiting on a key.
- Wrap HID sends in a handler: on a USB error, call `keyboard.release_all()`, wait 100 ms and continue. The loop must survive the Pi rebooting.
- If a `keypad` event queue reports overflow, call `release_all()` and `reset()` on that object so no key is left stuck down.
- No gesture timing, no selection logic and no knowledge of what any key means.

### boot.py

- Set the USB identity with `supervisor.set_usb_identification(manufacturer="Jukebox", product="JB Keypad")`. The Pi finds the device by this name.
- Enable only the keyboard device: `usb_hid.enable((usb_hid.Device.KEYBOARD,))`.
- **Normal boot:** hide the `CIRCUITPY` drive and the serial console (`storage.disable_usb_drive()`, `usb_cdc.disable()`), so the Pi sees a keyboard and nothing else.
- **Maintenance boot:** if key A is held at power-up, leave the drive and console enabled for editing. `boot.py` runs before `keypad` exists, so check A directly: drive GP5 low, read GP9 with a pull-up, and treat LOW as held.

Do not use the Reset button for the maintenance check. It is normally closed, so a disconnected Reset wire would look like a held button on every boot.

## Pi-side input service (jb-input)

`jb-input` is a small asyncio pipeline: each stage takes the previous stage's output and knows nothing about the others. Build each stage as its own module with no I/O except in the first and last.

### Modules

| Module | Input | Output | Responsibility |
| --- | --- | --- | --- |
| `device.py` | evdev device | `RawEvent(code, value, t)` | Find, grab and read the keypad; reconnect when it disappears |
| `keymap.py` | `RawEvent` | `ControlEvent(control, state, t)` | Translate key codes to control names from config |
| `gestures.py` | `ControlEvent` | `Gesture` | Click, double-click and hold for buttons |
| `knob.py` | `ControlEvent` | `Gesture` | Turn, and push-and-turn; coalesce fast steps |
| `actions.py` | `Gesture` | `Action` | Look up the action for a gesture in the config table |
| `publisher.py` | all of the above | MQTT messages | Publish, status, last-will |
| `config.py` | TOML file | typed settings | Load and validate configuration |
| `__main__.py` |  |  | Wire the stages together, signal handling |

Use `python-evdev` for input and `aiomqtt` for MQTT. Use `time.monotonic()` for all timing, and pass the clock in as a parameter so tests can control it.

### Device capture

- Find the device whose name is `Jukebox JB Keypad` (configurable). Ship a udev rule that creates a stable symlink, `/dev/input/jb-keypad`, and prefer it when present.
- **Grab it exclusively** (`device.grab()`), so key presses never reach the console or the kiosk browser.
- Ignore auto-repeat events (evdev value 2). Only down (1) and up (0) matter.
- If the device is missing or unplugged: publish status `device: missing`, retry every 2 seconds, and on reconnect clear all held-key state before reading.
- On start, read the keys currently held (`device.active_keys()`). If Reset is already down, enter the stuck-reset fault described below.

### Control names

| Key code from the Pico | Control name | Kind |
| --- | --- | --- |
| `KEY_A` to `KEY_K` (no I) | `key.A` to `key.K` | letter |
| `KEY_1` to `KEY_9`, `KEY_0` | `key.1` to `key.0` | digit |
| `KEY_ESC` | `reset` | button |
| `KEY_ENTER` | `knob.push` | button |
| `KEY_RIGHT` | `knob.cw` | step |
| `KEY_LEFT` | `knob.ccw` | step |

### Selection keys

Letter and digit keys are published on key down, immediately, with no gesture delay. Key up is ignored. A selection must feel instant, and these keys have no double-click or hold meaning.

### Button gestures (Reset and knob push)

Each button runs the same small state machine, with two timings from config: `double_window_ms` (default 300) and `hold_ms` (default 700).

| Gesture | Fires when |
| --- | --- |
| `hold` | The button has been down for `hold_ms`. Fires once, while still held. The later release produces nothing |
| `click` | Released before `hold_ms`, and no second press arrives within `double_window_ms` |
| `double` | A second press arrives within `double_window_ms` of the first release. Fires on that second press |

If the action table has no `double` entry for a button, skip the wait and fire `click` on release. That keeps Reset instant for as long as double-click is unassigned.

**Stuck button:** if a button stays down for `stuck_ms` (default 30000), publish fault `reset_stuck` or `knob_push_stuck`, stop producing gestures for that button, and clear the fault when it is released. A broken wire on the normally closed Reset shows up this way.

### Knob

- A `knob.cw` or `knob.ccw` tap is one step of +1 or -1.
- Coalesce steps that arrive within `coalesce_ms` (default 20) into one gesture carrying the signed total, for example `turn, steps: +3`.
- If `knob.push` is down when a step arrives, the gesture is `push_turn` instead of `turn`. Once a push has produced a `push_turn`, that press is consumed: it produces no `click`, `double` or `hold` when it ends.
- A push with no turn goes through the button gestures above.

### Actions

The action stage is a table lookup: `(control, gesture)` to an action name, read from config. Gestures with no entry are published as gestures only. This is the draft table:

| Control | Gesture | Action | Value |
| --- | --- | --- | --- |
| knob | turn | `volume.step` | steps |
| knob | push\_turn | `page.step` | steps |
| knob | click | unassigned |  |
| knob | double | unassigned |  |
| knob | hold | unassigned |  |
| reset | click | `entry.cancel` |  |
| reset | double | unassigned |  |
| reset | hold | unassigned |  |

## Selection entry module

The module turns letter and digit presses into slot selections. It is a pure state machine: it receives events and a clock, and returns the new state and any outputs. It does no I/O. `jb-core` feeds it from `jb/input/key` and publishes what it returns.

### Rules

- A selection is one letter plus one digit, in **either order**.
- A second key of the same kind replaces the first: pressing B then C leaves C waiting for a digit.
- When both are present, emit the selection and return to idle.
- If a partial entry sits for `entry_timeout_ms` (default 10000) with no key, clear it.
- `entry.cancel` clears a partial entry. With nothing entered, it does nothing.

### Slot numbering

Slot name = letter followed by digit, for example `B7`. Letter index: A=0, B=1, C=2, D=3, E=4, F=5, G=6, H=7, J=8, K=9. Digit index: 1=0, 2=1, up to 9=8, then 0=9. Slot index = letter index x 10 + digit index, giving 0 to 99. `A1` is 0, `B7` is 16 and `K0` is 99.

### States and transitions

| State | Event | Next state | Output |
| --- | --- | --- | --- |
| idle | letter | partial (letter) | entry changed |
| idle | digit | partial (digit) | entry changed |
| partial (letter) | letter | partial (new letter) | entry changed |
| partial (letter) | digit | idle | selection, entry changed |
| partial (digit) | digit | partial (new digit) | entry changed |
| partial (digit) | letter | idle | selection, entry changed |
| partial (any) | cancel | idle | entry changed, reason `cancelled` |
| partial (any) | timeout | idle | entry changed, reason `timeout` |
| idle | cancel or timeout | idle | none |

The lighting and the screen both follow the published entry state: a waiting key lights up, and it clears on selection, cancel or timeout.

## Message contract

All messages are JSON on the local mosquitto broker. Every payload carries `t`, the event time in milliseconds from the monotonic clock, and `v`, the schema version (1). Events use QoS 1 and are not retained. State topics are retained so a service that starts late gets the current value.

| Topic | Published by | Retained | Payload | When |
| --- | --- | --- | --- | --- |
| `jb/input/status` | jb-input | yes | `{"online": true, "device": "connected", "faults": []}` | On start, on device change, on fault change. Last-will sets `online: false` |
| `jb/input/key` | jb-input | no | `{"key": "B", "kind": "letter"}` | Each letter or digit key down |
| `jb/input/gesture` | jb-input | no | `{"control": "knob", "gesture": "turn", "steps": 3}` | Each gesture. `steps` only on `turn` and `push_turn` |
| `jb/input/action` | jb-input | no | `{"action": "volume.step", "value": 3, "control": "knob", "gesture": "turn"}` | Each gesture that has an action in the table |
| `jb/input/raw` | jb-input | no | `{"control": "key.B", "state": "down"}` | Every control event. Off unless `publish_raw` is true |
| `jb/core/entry` | jb-core | yes | `{"state": "partial", "letter": "B", "digit": null, "reason": null}` | Each entry change. `reason` is `selected`, `cancelled` or `timeout` when returning to idle |
| `jb/core/selection` | jb-core | no | `{"slot": "B7", "index": 16}` | Each completed selection |

Value lists:

- `device`: `connected`, `missing`.
- `faults`: any of `reset_stuck`, `knob_push_stuck`.
- `kind`: `letter`, `digit`.
- `control` in gestures: `reset`, `knob`.
- `gesture`: `click`, `double`, `hold`, `turn`, `push_turn`.

Consumers should subscribe to `jb/input/action` for behaviour and to `jb/input/key` for selection entry. `jb/input/gesture` exists for debugging and for consumers that need a gesture before it has an action.

## Configuration and layout

Every value below is read from config. The Pi side uses one TOML file, `/etc/jukebox/input.toml`, with these defaults built in so the service runs without the file.

| Setting | Default | Side | Meaning |
| --- | --- | --- | --- |
| `device_name` | `Jukebox JB Keypad` | Pi | evdev device name to capture |
| `device_path` | `/dev/input/jb-keypad` | Pi | udev symlink, used first when present |
| `reconnect_s` | 2 | Pi | Retry interval when the device is missing |
| `double_window_ms` | 300 | Pi | Maximum gap between clicks of a double-click |
| `hold_ms` | 700 | Pi | Press length that counts as a hold |
| `stuck_ms` | 30000 | Pi | Press length that counts as a fault |
| `coalesce_ms` | 20 | Pi | Window for merging knob steps |
| `publish_raw` | false | Pi | Publish every control event on `jb/input/raw` |
| `mqtt_host`, `mqtt_port` | `localhost`, 1883 | Pi | Broker address |
| `topic_prefix` | `jb` | Pi | First segment of every topic |
| `[keymap]` table | as in Control names | Pi | Key code to control name |
| `[actions]` table | as in Actions | Pi | Control and gesture to action |
| `entry_timeout_ms` | 10000 | jb-core | Partial entry lifetime |
| `ROW_PINS`, `COL_PINS` | GP5 to GP8, GP9 to GP13 | Pico | Matrix pins |
| `KEY_CODES` | 20 key codes in key-number order | Pico | Matrix position to key code |
| `RESET_PIN`, `RESET_PRESSED_VALUE` | GP14, True | Pico | Reset pin and its pressed level |
| `ENC_A`, `ENC_B`, `ENC_SW` | GP2, GP3, GP4 | Pico | Encoder pins |
| `ENCODER_DIVISOR`, `ENCODER_INVERT` | 4, False | Pico | Counts per detent, direction flip |
| `SCAN_INTERVAL_S` | 0.02 | Pico | Matrix scan and debounce interval |

### Repository layout

```
firmware/pico-keypad/
  boot.py
  code.py
  config.py
  lib/adafruit_hid/
services/jb-input/
  pyproject.toml
  jb_input/
    __main__.py  config.py  device.py  keymap.py
    gestures.py  knob.py    actions.py publisher.py
  tests/
libs/jb-entry/
  jb_entry/entry.py
  tests/
deploy/
  jb-input.service
  99-jb-keypad.rules
  input.toml
```

The systemd unit runs the service as an unprivileged user in the `input` group, starts after `mosquitto.service`, and restarts on failure. Target platform: Raspberry Pi OS (Bookworm, 64-bit), Python 3.11 or later.

## Test plan and acceptance

The gesture, knob and entry logic must be fully testable on a laptop with no hardware, by feeding events and a fake clock into the pure modules.

### Automated tests (pytest)

| Area | Cases that must pass |
| --- | --- |
| Button gestures | Short press gives `click`. Two presses inside 300 ms give one `double` and no `click`. A 700 ms press gives one `hold` and nothing on release. With no `double` action configured, `click` fires on release with no delay |
| Stuck button | A 30 s press raises the fault once, produces no gestures, and clears on release |
| Knob | Three taps inside 20 ms give one `turn` with `steps: 3`. Mixed directions sum with sign. A step while pushed gives `push_turn`, and that push then produces no `click` or `hold` |
| Key map | Every one of the 24 key codes maps to its control. Unknown codes are ignored without error. Auto-repeat is ignored |
| Actions | Each row of the action table produces its action. An unassigned gesture produces a gesture message and no action |
| Selection entry | Every row of the transition table. Letter then digit and digit then letter both select. `B7` is index 16, `A1` is 0, `K0` is 99. Timeout and cancel clear a partial entry |
| Device | With a fake device: disconnect then reconnect clears held state and updates status |

### Bench checks (with the hardware)

Status as of Oct 6 2026: **all bench checks pass.** The Pico side was verified with `evtest`, one control at a time; then `jb-input` was run by hand against the real keypad, watching `mosquitto_sub -t 'jb/#' -v`.

- [x] Pressing each of the 20 keys produces exactly one `jb/input/key` message with the right key, and no neighbour fires *(every key verified at the Pico; through jb-input, B and 7 each gave exactly one message on key down and nothing on release; the 24-code key map is covered by the tests)*
- [x] One detent of the knob produces one step, clockwise positive *(3 detents = 3 steps each way)*
- [x] A fast spin loses no more than a step or two and never blocks key presses *(one fast turn = 22 steps; knob clicks between spins all arrived in order)*
- [x] Reset reads as released at rest and pressed when pushed (normally closed wiring) *(after the pull-up fix in the Scanning section)*
- [x] Unplugging the Reset wire raises `reset_stuck` after 30 seconds *(the wires are hot-glued, so tested by holding Reset 35 s instead -- electrically identical, since an open NC switch is what a broken wire looks like: `reset_stuck` appeared in `jb/input/status` at 30 s and cleared on release)*
- [x] Key presses do not appear in the console or the kiosk browser *(while jb-input runs, any other reader of the keypad gets "This device is grabbed by another process")*
- [x] Unplugging and replugging the Pico recovers within a few seconds with no restart *(status went `missing`, then `connected` ~6 s later in the same jb-input process; the next key press went straight through. Keys pressed in the ~2 s before jb-input re-finds the keypad are lost -- the reconnect poll interval is `reconnect_s`)*
- [x] Holding key A at power-up exposes the `CIRCUITPY` drive; a normal boot does not

### Done when

1. All automated tests pass.
2. All bench checks are ticked.
3. `mosquitto_sub -t 'jb/#' -v` shows correct messages for every control and gesture.
4. A README in each of the three folders explains install, configuration and how to flash or deploy.

## Open decisions and things to verify

None of these block the build, because each is a config value. The builder should use the defaults and leave them easy to change.

| Item | Default in this brief | How it gets settled |
| --- | --- | --- |
| Which encoder wire is on GP2, GP3 and GP4 | CLK, DT, SW in that order | **Confirmed on the bench (Oct 6 2026)** |
| Encoder counts per detent | Divisor 4 | **Confirmed: 4 gives one step per detent** |
| Encoder direction | Not inverted | **Confirmed: clockwise sends RIGHT_ARROW** |
| Whether `rotaryio` accepts the pins as wired | Assumed yes, A and B adjacent | **Confirmed** |
| Entry order | Letter and digit in either order | Product decision. Letter-first only is a one-line rule change |
| Entry timeout | 10 s | Tune in use |
| Meaning of knob click, double and hold | Unassigned | Product decision, set in the action table |
| Meaning of Reset double and hold | Unassigned | Product decision, set in the action table |
| Double-click window and hold time | 300 ms and 700 ms | Tune in use |
| Diodes on the matrix | None fitted | Only needed if three or more keys are ever held together |
| Knob hold vs. push-and-turn | — | **Decided Oct 6:** advanced gestures (hold, double-click, push-and-turn) are not in use; the knob turns and clicks only. They exist in jb-input but are off unless enabled in config. If ever enabled together: a push held > 700 ms before turning fires `hold` before the `push_turn` (seen on the bench) |
| Naming, topics and config | — | **Decided Oct 6:** this brief is the key-to-pin mapping only; names and conventions follow the existing system. jb-input publishes on `jukebox/jb-input/...` and reads `/etc/juk3-box/jb-input.json`, and no gesture has an action assigned yet |
