# Controls Setup — Plan

The setup wizard's step for the cabinet's controls (step 15 in `Setup Wizard — Plan.md`,
"Your controls"), planned with the owner on Oct 21. It replaces "Knob and keys", which
only asked for a few presses and stored nothing.

## Why

Cabinets differ. The owner's has A–K (no I) and 1–0, 100 selections a page, a knob and a
Cancel button, through a Pico that acts as a USB keyboard with a fixed keymap. Others
have 160 or 200 selections (20 letters A–V without I and O, 1–8 or 1–0), Rock-Ola's
three-digit numbers, a second knob for volume, extra buttons, or no knob at all, and
other builders may use a cheap arcade USB board instead of our Pico. Today the letters
and numbers are hard-coded in three places (`services/jb_input/config.py`,
`shared/jb_entry/entry.py`, `services/jb_ui/engine/js/board.js`), and jb-input listens
only to the Pico.

## The step, in order

**1. The keypad board (Pico), first, and optional.** The wizard looks at what's plugged in:

| Found | Does |
|---|---|
| A blank Pico (it shows up as the `RPI-RP2` drive by itself) | "Found a keypad board. Install the keypad program?" One tap: CircuitPython, then ours |
| Our program, current | Nothing to do; on to the layout |
| Our program, older | Updates it without a button: the Pi asks it to restart into install mode |
| A Pico with something else on it | Asks first (it may be someone's own project), then "hold the white BOOTSEL button while you plug it in", with a picture |
| Another keypad or a keyboard | No Pico part |
| Nothing | "No keypad found": plug one in, or go on with a keyboard or the phone only |

**2. How people pick songs.** The cabinet's buttons / a computer keyboard (testing, or no
keypad yet: "demo mode", the standard 100 layout) / the phone only.

**3. The layout.** Common ones first, each with a picture of its board:
- 100: A–K and 1–0 (the owner's)
- 160: A–V without I and O, and 1–8 (Seeburg)
- 200: A–V without I and O, and 1–0
- Numbers only: three digits, 100–299 (Rock-Ola)
- Something else: checkboxes A–Z and 1–0, I and O unchecked to start

The page says what it makes as it changes: "10 letters × 10 numbers = 100 songs a page,
A1 to K0". Songs per page follows from the keys; it isn't asked.

**4. The other controls.** Checkboxes: the knob (turn and press), Cancel, a volume knob
(clicks in steps, or smooth like a radio's; press to mute if it has one), volume
buttons. No knob: Left, Right and OK buttons (with only Left and Right, holding Right is
OK); none at all: the board stays on the screen and the menus are on the phone.

**5. Teaching each button.** The cabinet does most of it, in big type: "Press A", with the
whole keypad drawn and each key lighting as it's learned. The phone shows the same, with
Back, "This key doesn't work" (skip it, fix it later) and Start over.
- If the first press already says "A" (a keypad that sends letters, like the owner's
  today), it turns into a quick check: press every key in any order, each ticks off.
- "That's the same button as B" when a learned button comes again; a key that never
  lets go gets a warning (Cancel is wired normally closed, so a broken wire looks like a
  press that never ends).
- The knob learns its direction ("turn it one click to the right"), so one wired
  backwards still works.

**6. Extra buttons.** "Add a button": name it, press it, then pick what it does, and
optionally what holding it does. The list: Skip, Pause/Play, Volume up/down, Mute,
Random song, Favorite this song, Show Now Playing, Next/Previous page, Change light show,
Lights on/off, Whole-home on/off, Power menu. More as people ask.

**7. Try it.** Press B7: "B7: song 17"; the knob moves the highlight; Cancel clears it.
Done when the owner taps "Looks right".

The same step runs again from Admin › Setup (a new keypad, a key to fix).

## A keyboard, always

A plugged-in keyboard works next to the cabinet's buttons, with no mode to switch:
letters and numbers pick songs, ← → turn the knob, Enter presses it, Backspace and Esc
are Cancel, ↑ ↓ are volume. Letters the jukebox doesn't use flash "This jukebox has no I."

## The pieces

- **jb-input** opens any keypad or keyboard, not only the Pico. A learn mode (asked over
  MQTT by jb-admin) publishes what each press sends: device and code. The learned map,
  the layout, the extra buttons and their actions live in `/etc/juk3-box/jb-input.json`
  (written by jb-setupctl, like the other root-owned settings). A second knob.
- **The layout as a setting** (letters, numbers, numbers-only), read by jb-input,
  jb-entry and the board, instead of the three hard-coded copies.
- **The Pico program** reports positions, not letters: "row 2, column 3", "knob 1 turned
  right". The teaching step gives them meaning, so builders follow one published pin
  diagram and never edit the program. It reports its version (in its USB identity), and
  restarts into install mode when the jukebox asks (a pattern of keyboard-light
  signals), so updates need no button. CircuitPython (MIT) and our program ship in the
  card image; jukebox updates carry new versions of the program.
- **The board** for 160 and 200 (a new design: probably two columns of title strips,
  like a Seeburg), and three-digit entry for Rock-Ola. Entry stays letter-first unless a
  cabinet needs the number first.

## First version (decided Oct 21)

In from the start:
- The Pico: detect, install, update (step 1).
- Layouts: 100 (A–K, 1–0), 160 (A–V without I and O, 1–8), Rock-Ola's three-digit
  numbers, and "something else" (tick the letters and numbers).
- **A responsive song board**: it lays itself out for any letters × numbers (100, 160,
  a custom set) and for three-digit numbers, instead of the fixed 10 × 10.
- **Three-digit entry** (Rock-Ola) next to letter-and-number entry.
- Teaching the knob, its press, Cancel and every key; the try-it screen; redo from Admin.
- **Knob calibration**: "turn it one click to the right" counts what one click sends (encoders
  send 1, 2 or 4 steps a click), so the knob never skips or moves two places a click;
  the direction comes from the same turn.
- **Bouncy buttons**: while teaching and trying, two presses of the same button closer
  together than a person can press (under ~60 ms) mean a bouncing switch. The wizard says
  so ("A looks bouncy: fixed") and lengthens that button's settle time (debounce), stored
  per button. The Pico program takes a settle time per button too.
- A keyboard always works.
- **The README's build guide**: the recommended hardware (our build: the parts list), how
  to wire the keypad board (pin diagram, a photo of the owner's), and how to install,
  step by step.

Later, when someone needs it: 200 selections, the volume knob, extra buttons and their
actions, Left/Right/OK buttons for cabinets without a knob, a touchscreen.

## How a selection is written (decided Oct 21)

One setting, **the selection format**, decides three things together, so they can't
disagree: which keys the wizard teaches, how presses are read (when an entry is
complete, what's wrong), and how the song board numbers its slots and how many make a
page.

The questions (step 3, "The layout"):
- **"What's on your selection buttons?"** Letters and numbers / Numbers only.
- **Letters and numbers:** tick the letters (presets fill them: A–K or A–V, without I
  and O) and the numbers (1–8 or 1–0). An entry is a letter, then a number. A page is
  every pair: A–K × 1–0 = 100, A–V × 1–8 = 160.
- **Numbers only:** "How many digits is a selection?" (usually 3), "the first number on
  your cabinet?" and "the last?" (100 and 299, say), and optionally the digits each place
  uses (some models use only 1–8 in the last place). An entry is complete at the last
  digit. A page is the whole range: 100–299 = 200.
- **A live preview** while answering: a small song board with its real numbers and "160
  songs a page, A1 to V8". Then: "Compare with your cabinet's own title cards: do the
  first and last match?"

The board lays itself out from the format:
- Letters and numbers: a group per letter, its numbers inside. 100: 10 groups of 10 (as
  now); 160: 20 groups of 8, in two columns of 10, like a Seeburg.
- Numbers only: a group per ten numbers (100–109, 110–119…). 200: 20 groups of 10, the
  same two columns, labeled by number.
- Sized by the number of groups and slots. If 200 to a page reads too small on the
  cabinet's screen, the page splits in halves that the knob or a page key flips.

## Where it is (Oct 21)

- Done: jb-input opens every keypad and keyboard, with learn mode, the learned controls
  (`/var/lib/juk3-box/controls.json`), knob calibration and settle times
  (`services/jb_input`). The selection format as a setting, the responsive board and
  three-digit entry (`shared/selection.py`). The wizard step: format questions with a
  live preview, teaching (`services/jb_admin/controls_teach.py`), the try-it, and
  Admin › Setup › "Teach the buttons again" (`/setup?step=controls`).
- Done too: the Pico program 2.0 by positions (`firmware/pico-keypad`), its install on a
  blank Pico and its update (jb-setupctl `keypad`, jb-input's light signal), step 1 in the
  wizard; the README's build guide with the parts list and `docs/keypad-wiring.svg`.
- Waiting on the owner: moving the cabinet's own Pico from version 1 to 2.0 (version 1
  can't be asked: hold A while plugging it in, then the wizard's Update), and a photo of
  the keypad board for the README.

## Build order

1. jb-input: any device, learn mode, the saved map, knob calibration, per-button settle
   times, the keyboard always on.
2. The wizard step on the 100 layout, tried on the owner's cabinet first; the Admin ›
   Setup way back in.
3. The layout as a setting; the responsive board and three-digit entry (the 160 and
   Rock-Ola boards designed with the owner first).
4. The Pico program by positions, with settle times; the wizard's install and update; the
   owner's cabinet moves to it (a re-teach, about two minutes).
5. The README's build guide.
