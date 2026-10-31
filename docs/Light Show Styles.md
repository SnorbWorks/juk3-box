# Light Show Styles

How the cabinet's light show follows the music: the design rules it is
built on, the style it uses for each kind of music, and where each piece
lives in the code. Written 2026-10-18, from research into how professional
lighting designers and automatic systems (SoundSwitch, MaestroDMX, Philips
Hue Sync, Nanoleaf, WLED) light music.

## The idea

A lighting operator doesn't flash everything on every beat. They give each
light a job, hold back, and save the big moments for the big moments. The
cabinet does the same:

- **jb-analyzer** listens and describes the music: the drums (kick, snare,
  hats), the beat clock (where we are in the beat and the bar), and the
  moments (a gap, a hit, a new section, a build, a drop). See
  `docs/contracts/audio-analysis.md`.
- **The director** (`services/jb_lights/show/director.py`) picks a style
  for the song and turns the stream into cues: which palette, how intense,
  which scene each part plays, when to black out, when to flash.
- **The parts** (`services/jb_lights/show/parts.py`) each draw their own
  role from those cues.

## Every part has a job

| Part | Role | Follows |
|---|---|---|
| Upper panel | The storyteller | Sections, palette, the build and the drop. Big, slow, deliberate |
| Footlights | The rhythm section | The kick (or snare, or the off-beat, by style), pulsing and chasing |
| Level bar | The secondary voice | The music itself (spectrum, VU, ribbon) or a second rhythm (a drum kit seen from above, a beat counter, ripples, a bouncing ball). It changes scene every 8 bars and at each section, from the scenes that suit the style and the moment |
| Keypad | The sparkle | Hi-hats and accents, the kick on the white keys |

Pros put structure on big fixtures and fast hits on small ones, and fire
big cues on phrase points, not on every drum hit.

## The rules

- **Contrast beats constant motion.** Build in waves; after every peak,
  come back to a calm look. A build *holds back*: the upper panel dims
  while the rhythm tightens. Then the drop is a full reveal.
- **Blackout, then the hit.** A gap (the half-bar of silence before a
  drop, a band stopping dead) blacks the cabinet out, and the hit that
  ends it flashes white. A snap, never a slow fade: a hit ends a
  blackout instantly. Gentle styles dim instead (oldies, country) or ignore
  gaps (jazz, chill).
- **Colour follows feeling.** Faster, major, energetic music suits lighter,
  more saturated, warmer colours; slower, darker music suits deeper,
  bluer, less saturated ones (Palmer et al. 2013, PNAS).
- **Sections change the picture.** Each new section moves to the style's
  next palette; a section that lifts the energy (a chorus) or a drop
  switches to the peak palette for a while; a breakdown goes calm and cool.
- **Move with the beat, not against it.** Chases step on the beat clock.
  Fast songs (over 140 bpm) move at half time, slow ones (under 80) at
  double. Scenes rotate every 16 bars so a long song doesn't repeat.
- **Swing and rubato fool beat clocks.** Jazz and chill follow the level,
  not the beat.

## Safety: flashes

Photosensitive epilepsy guidance (WCAG 2.3.1; Ofcom; UK HSE for live
events) allows no more than **3 flashes a second** over a large area, and
the cabinet fills much of a viewer's view up close. So:

- White flashes (hits, drops) are capped at 3 in any second (`MAX_FLASHES_PER_S`).
- Whole-area pulses (the footlights on the kick) retrigger at most 3 times a
  second (`PULSE_MIN_S`).
- Small sparkle (single keys) is exempt, as fine patterns are.
- Styles with `strobe: 0` never flash white.

## The styles

`show/styles.py`. The genre comes from listeners' tags (Last.fm, via the
analyzer's slow channel); a compound tag counts by its main word ("doom
metal" is metal). Without one, the mood picks pop (lively) or chill (calm).
Each song's own seed picks among a style's palettes and scenes, so two
songs of one genre still look different. Some genres take turns between
two styles (`ROTATIONS`): metal, EDM and bass/dubstep songs each get either
their own style or **arena**, by the song's seed, so a song always gets the
same one. The owner can also pin one style for every song
(`"show_style"` in `/var/lib/juk3-box/lights.json`).

| Style | For | Palette | Rhythm (footlights) | Upper panel | Flashes |
|---|---|---|---|---|---|
| edm | EDM, house, techno, trance, dnb | neon magenta, cyan, UV violet | kick: pulse, rows, chase | breathe in breakdowns; chase, sweep, EQ; slab on drops | heavy: builds strobe the footlights |
| bass | dubstep, bass music | dark, toxic green, red, purple | snare, half time | slab, and *wobble*: as wide as the bass | heavy |
| hiphop | hip-hop, rap, trap | gold, purple, deep red | rows: kick front, snare back | a cool, steady wash; the spectrum rising | on big hits |
| pop | pop, synthpop, k-pop | pink, sky, yellow; a new palette each section | kick: pulse, chase | breathe, wash, sweep on the downbeat | on big hits |
| rock | rock, alternative, punk, grunge | red/amber, blue/white | snare | big single-colour slabs, a new one every bar | on big hits |
| metal | metal, industrial | red/amber/white, icy blue/white | kick, half time | slabs | heavy |
| oldies | classic rock, 50s-70s, rockabilly | incandescent amber, cherry, mint | soft chase on the backbeat | the marquee: chasing bulbs, bubble tubes, a spinning record | never |
| country | country, folk, americana, acoustic | warm whites, amber, sunset, dusty blue | gentle, half time | slow breath and wash | never |
| soul | soul, funk, disco, R&B, motown | disco gold/red/green/blue, magenta | the disco floor: a checkerboard flipping on the beat | chase, wash | on big hits |
| jazz | jazz, blues, swing | amber, steel blue, few tones | a low warm glow, the level only | almost still | never |
| chill | classical, ambient, lo-fi, soundtracks | lavender, soft gold, teal | glow | slow breathing | never |
| latin | reggae, ska, latin, reggaeton | rasta red/gold/green, coral, turquoise | the off-beat (the skank) | wash, sweep | on big hits |
| arena | in turn with metal, EDM, bass | dark, blue sparkles; white | blue bands snapping on the beat: the outer sixths, then the middle third | dark with blue sparkles; fades to black in a build or breakdown | the drop: the whole cabinet white, breaking up into sparkles from the centre out |

### Arena

The owner's design (2026-10-30), after a concert clip: a dark cabinet,
then VERY bright. The whole cabinet is one picture (`show/arena.py`):

- **Dark:** blue sparkles all over the cabinet, quicker with the hi-hats
  and the kick. The footlights and the keypad's letters and digits snap
  between two blue bands on the beat. SINGLE, ALBUM and RESET pulse white
  with the kick (their filters block blue).
- **Hush:** a riser building, or the bass falling away before a drop, fades
  everything toward black; a gap snaps it black.
- **Burst:** on the drop itself, or a really big hit on its own (not the
  section lifting or the hit after a gap: those come just before the drop,
  so the flash led the beat), the whole cabinet flashes white and the white
  breaks up into white and blue sparkles from the centre out, in about a
  second, then settles back to the dark.
- **Current:** each part has a budget well inside its branch's cap (the
  upper panel and footlights 4 A of 6, the level bar 2.4 A of 4), a bigger
  one for the flash (5 A and 3.2 A); a frame that would draw more is dimmed
  to fit. Bursts come at most once a second (a drop may follow a big hit
  0.4 s later).
- The analyzer calls a drop on its first kick (a beat whose bass is back at
  the song's groove), not when the smoothed bass catches up a third of a
  second later, so the flash lands on the drop.

Scenes, by part (`show/parts.py`):

- **Upper:** breathe, wash, chase, sweep, rise, slab, wobble, marquee, tubes, eq, ribbon
- **Footlights:** glow, pulse, rows, chase, checker, offbeat
- **Level bar:** spectrum (in the palette), classic (the rainbow analyzer), vu, ribbon, and in chunky blocks: drumkit (hi-hat, snare, kick, tom and crash, each hit by its own drum), tiles, mirror, bounce, trace (a heart-monitor scroll), ripple, counter (1-2-3-4), comet, fire, rain, sparks
- **Keypad:** sparkle, eq, pulse, glow, arena (on a Pi 5 the keypad sends
  its own frames: its chain is its own device there)
- **Arena:** one scene for every part, drawing the whole cabinet together

## Tuning without the cabinet

```
venv/bin/python -m tools.analyze_song song.m4a --json song.jsonl --quiet   # what the analyzer hears
venv/bin/python -m tools.show_preview song.jsonl --audio song.m4a --out song.mp4 --genre hip-hop
venv/bin/python -m tools.show_preview song.jsonl --audio song.m4a --tour drumkit,tiles,ripple   # cycle the bar's scenes
```

The preview draws the cabinet's real pixel positions with the song's audio
and a caption of the style, tier, scenes and moments. Run one render at a
time on the Pi (it has ~900 MB of memory).

## Limits and next steps

- **No look-ahead.** The analyzer hears the music as it plays, so the
  blackout starts ~0.1 s into a gap, and short gaps give short blackouts.
  Seeing ahead needs the per-song pre-analysis cache (Audio Analyzer
  architecture, "Pre-analysis"): with a song analysed in advance, the show
  could black out *on* the gap and pre-load the drop.
- **Sections are a novelty score, not verse/chorus labels.** A real chorus
  detector (repetition, the loudest recurring part) would let choruses get
  their own look.
- **A low-flash setting** (flashes become soft swells) would be the next
  safety option, as a cabinet setting.
