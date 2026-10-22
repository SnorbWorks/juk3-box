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

## Build order

1. jb-input: any device, learn mode, the saved map, the keyboard always on, extra
   buttons and their actions, a second knob.
2. The wizard step for the 100 layout, so it's tried on the owner's cabinet first; the
   Admin › Setup way back in.
3. The Pico program by positions and the wizard's install and update; the owner's
   cabinet moves to it (a re-teach, about two minutes).
4. The other layouts: 160 and 200 boards (designed with the owner first), Rock-Ola's
   three digits.
