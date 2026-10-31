# Light Show Styles

How the cabinet's light show follows the music: the design rules it is
built on, how it plans a show for each song, the shows it has, and where
each piece lives in the code. Written 2026-10-18, from research into how
professional lighting designers and automatic systems (SoundSwitch,
MaestroDMX, Philips Hue Sync, Nanoleaf, WLED) light music; rewritten
2026-10-30 for the song-aware show (v0.3.0), where every song gets its own
plan instead of a show taking turns with the others.

## The idea

A lighting operator doesn't flash everything on every beat. They plan the
show before the gig: a look that suits the song, a job for each light,
holding back, and the big moments saved for the big moments. The cabinet
does the same, in four steps: listen, understand, plan, perform.

- **jb-analyzer listens and describes** the music
  (`docs/contracts/audio-analysis.md`). Moment to moment: the drums (kick,
  snare, hats, the crash cymbal), the beat clock (where we are in the beat
  and the bar), and the moments (a gap, a hit, a build, a drop). Per song:
  its **feel** (how energetic, happy, tender, aggressive, groovy and moody
  it is, its pace, how much it's sung, its decade), its **sections**
  (labelled: the same music gets the same letter, so a chorus coming back
  is known as the chorus, each with a role like verse, chorus or drop), and
  once the cabinet has heard a song through, its **map**: the whole song
  ahead of time, locked to the recording as it plays.
- **The choreographer** (`services/jb_lights/show/plan.py`) plans the
  song: the show that fits it, one colour family, one pace and smoothness,
  what the big moments do, and a look for each section.
- **The director** (`services/jb_lights/show/director.py`) plays the plan
  moment to moment and turns it into cues: which look, which colours, how
  bright, when to black out, when to slam, splash or flash.
- **The parts** (`show/parts.py`, with the song-aware moves in
  `show/moves.py`) each draw their own role from those cues.

The analyzer describes; the lights decide. Nothing in the analyzer knows
about lights, so a show file (or another lights module) can make its own
choices from the same description.

## One plan per song

Made as the song starts, made again as its feel, tempo, genre and tags come
in, and then held after 15 seconds (`SETTLE_S`), so a song keeps one
choreographed feel from start to finish. A song the cabinet has learned has
its feel at once.

### The show that fits

Each show says what kind of song it suits (`fits` in its show file): targets
on the feel's axes, and the decades and listeners' tags it's made for. The
choreographer scores every show against the song (`plan.fit`):

- **The feel first.** How far the song is from each target, squared, and
  weighted by how far the song stands out on that axis: a very moody song
  is matched on its moodiness first, a very tender one on its tenderness,
  whatever its tags say. An axis a show doesn't name is taken to be a
  typical song's.
- **Then the words.** A show made for the song's genre group (from its
  genre or tags) gets +0.3, one naming one of its tags +0.3, one made for
  its decade +0.2. A song with no genre gets no genre bonus at all: the
  choice is its feel's.
- **Close calls take turns.** Shows within 0.05 of the best are equally
  good: the song's seed (its title and artist) picks among them, so two
  similar songs can look different but a song always looks the same.

Before the feel is in (the first seconds of a song nobody has tagged), the
song's genre group takes turns among its shows as before.

The owner can **pin** a show (it's the look for every song, whatever the
song) or limit a genre group to **only these** shows (the fit then chooses
among them), in admin › Light Show › Shows. These live in
`/var/lib/juk3-box/lights/settings.json` (`services/jb_lights/owner.py`).

Checked against 24 realistic songs (moody indie → Moody, a love song →
Love, 80s new wave → Starfield, hard rock → Rock Show, a sad ballad →
Moody or Love, reggae → Latin, an untagged 80s song → Starfield).

### One colour family

The song keeps one of its show's palettes all the way through: the one
whose warmth suits the feel (`Plan._family`). A moody song gets the
bluest, a happy or tender one the warmest; the less a song leans either
way, the more of the palettes its seed picks from. The sections vary it
without leaving it:

- each label gets its own lead colour from the family (the verse leads with
  one, the chorus another);
- quiet sections and breakdowns darken it;
- a big section lifts it toward white, and its third time round moves
  toward the show's peak colours;
- the song's peak, its last chorus and a drop get the show's peak colours.

The voice's EQ, the ribbon and the spectrum draw in the song's colours too.
The classic rainbow analyzer is still there for the shows that ask for it
(EDM, Soul).

### One pace, one smoothness

How often the lights move (`plan.rate_of`), in steps a beat:

| The song | Steps | Looks like |
|---|---|---|
| A ballad (pace under 0.2) | 1 a bar | a slow roll |
| Slow (under 0.4) | 1 every other beat | unhurried |
| Most songs | 1 a beat | on the beat |
| Fast and hard (0.85 and up) | 2 a beat | driving |

Above 140 bpm the beat steps go half time, and nothing moves more than
about three steps a second. A show can lean it (`motion.pace`: half, beat,
double). Builds quicken it.

**Smoothness** (0 punchy .. 1 flowing) comes from the same feel: slow,
tender and moody songs flow, aggressive ones punch. It sets how long a
pulse takes to fade, how long one look takes to crossfade into the next
(0.35 s for a punchy song, up to 2.75 s for a ballad), and above 0.75 the
footlights follow the level instead of the kick. A show can lean it too
(`motion.smooth`).

### What the big moments do

| Moment | The show's choices |
|---|---|
| A crash cymbal | `splash`: a ring of light spreading from where it struck, across every part; `shimmer`: a soft glow; `flash`; `none` |
| A drop, or the hit after a gap | `slam`: the brightness jumps and settles over a beat and a half; `flash`: white; `swell`: up over 0.7 s, down over 2.5 s; `burst`: Arena's white burst |
| A gap | `blackout`, `dim`, or ignore it |

A **gentle** song (tender, or very smooth) never slams or flashes, whatever
its show: its drops swell and its crashes shimmer.

### A look for each section

Each section gets a **character** from what the analyzer says about it
(`plan.character`): its role, how big it is, how much it's sung, and how
much drum is in it.

| Character | When | How bright | Typical look |
|---|---|---|---|
| quiet | an intro or outro, anything small | 45% | breathing, a glow |
| vocal | a sung section (a verse) | 68% | the EQ for the voice |
| build | a build or pre-chorus | 72% | the panel holds back, the rhythm tightens |
| drive | a section that drives (drums, no voice) | 82% | chases and sweeps |
| solo | a solo | 92% | beams, sparks |
| big | a chorus, the song's peak, a drop | 100% | the rock-show moment: beams, slabs, fire |
| break | a breakdown | 35% | thinned out, cool |

A show lists its scenes for each character (`sections` in its show file);
a character it leaves out plays its tier's scenes (calm, main or peak).

**The same music gets the same look.** A label's look is kept: the chorus
coming back looks like the chorus again (the same scenes, the same lead
colour), 6% brighter each time round. A section that comes back bigger
than before (a quiet verse that returns with drums) gets the bigger look.

### A learned song

The first time a song plays, the analyzer finds its sections live, a bar
and a beat after each starts, and guesses their roles (the loudest label
that comes back is the chorus). When it has heard the whole song in one
go, it studies the recording and saves the song's **map**
(`/var/lib/juk3-box/songs`): every section, its role and its feel.

The next time, the analyzer locks to the recording and the director
follows the map instead of waiting to hear things:

- each look lands on the beat it belongs to (50 ms early, for the capture's
  and a frame's delay), not a bar late;
- the build into a big section starts two bars before it;
- a gap goes dark as the sound falls away, not 0.1 s into the silence;
- a live moment the map already played isn't played twice.

admin › Light Show › Songs lists the learned songs, each with its map and
the analyzer's words for it; forgetting one makes the cabinet learn it
again the next time it plays all the way through.

## Every part has a job

| Part | Role | Follows |
|---|---|---|
| Upper panel | The storyteller | The section's look, the colours, the build and the drop. Big, slow, deliberate |
| Footlights | The rhythm section | The kick (or snare, or the off-beat, or the level for a smooth song), pulsing and chasing |
| Level bar | The secondary voice | The music itself (spectrum, VU, ribbon, the voice's EQ) or a second rhythm (a drum kit seen from above, a beat counter, ripples, a bouncing ball). It changes with each section, and turns to another of its scenes in a long one (16 bars) |
| Keypad | The sparkle | Hi-hats and accents, the kick on the white keys; the EQ while someone sings |

Pros put structure on big fixtures and fast hits on small ones, and fire
big cues on phrase points, not on every drum hit.

## The rules

- **One song, one feel.** A song keeps its show, its colour family and its
  pace from start to finish; only its sections change the picture, and
  they change it the same way each time the music comes back.
- **Contrast beats constant motion.** Build in waves; after every peak,
  come back to a calmer look. A build *holds back*: the upper panel dims
  while the rhythm tightens. Then the drop is a full reveal.
- **Blackout, then the hit.** A gap (the half-bar of silence before a
  drop, a band stopping dead) blacks the cabinet out, and the hit that
  ends it slams in. A snap, never a slow fade: a hit ends a blackout
  instantly. Gentle shows dim instead (oldies, country, love) or ignore
  gaps (jazz, chill).
- **Colour follows feeling.** Faster, major, energetic music suits lighter,
  more saturated, warmer colours; slower, darker music suits deeper,
  bluer, less saturated ones (Palmer et al. 2013, PNAS). The colour family
  is chosen this way.
- **Move with the song, not against it.** A slow song never gets fast
  lights: the pace is the song's. Chases step on the beat clock at the
  plan's rate.
- **Swing and rubato fool beat clocks.** Jazz and chill follow the level,
  not the beat.

## Safety: flashes

Photosensitive epilepsy guidance (WCAG 2.3.1; Ofcom; UK HSE for live
events) allows no more than **3 flashes a second** over a large area, and
the cabinet fills much of a viewer's view up close. So:

- White flashes (hits, drops) are capped at 3 in any second (`MAX_FLASHES_PER_S`).
- Whole-area pulses (the footlights on the kick) retrigger at most 3 times a
  second (`PULSE_MIN_S`).
- Movement never steps more than about 3 times a second.
- Small sparkle (single keys) is exempt, as fine patterns are.
- Shows with `strobe: 0`, and gentle songs in any show, never flash white.
- The owner's **low-flash** setting turns every white flash into a soft
  swell, and makes Arena's bursts gentle.

## The shows

The built-in shows are in `services/jb_lights/shows/` (show files, format 2:
`docs/contracts/lights.md`, Shows). The owner can upload more, and modules
can bring their own.

| Show | Made for | Colours | Motion | The big moments |
|---|---|---|---|---|
| Moody | very moody, dark songs; trip-hop, shoegaze, dream pop, sad | deep blues and violet | slow and very smooth (0.85); breathing, flowing, the spectrum rising for the voice | crashes shimmer, drops swell, gaps go dark; never flashes |
| Love | tender songs; love songs, ballads, romance | reds, roses and pinks | flowing (0.9): waves of colour rolling across | swells, shimmers; gaps dim; never flashes |
| Starfield | 70s and 80s songs; alternative, new wave, synthwave, post-punk, psychedelic | starlight: white, ice blue and violet; gold, pink and sky | stars flying out from the middle, faster with the music | dark on the gaps, then a slam when the beat comes back; crash splashes |
| Rock Show | hard, loud songs; rock, metal | the stage rig: red, amber and white; blue and white; purple | beams sweeping in pairs on the pace, hit by the kick | the whole rig in the chorus, crash splashes, a blackout and a slam |
| Rock | rock songs | red and amber, or blue and white | big single-colour slabs | crash splashes, slams |
| Metal | very aggressive, very loud | red/amber/white, icy blue/white | slabs on the kick at half time, fire on the bar, beams in solos | heavy: flashes, builds strobe |
| Pop | happy, bright songs | pink, sky, yellow | breathe, wash, sweep; the EQ for the voice | crash splashes, white on the drop |
| EDM | groovy, energetic dance music | neon magenta, cyan, UV violet | chases, sweeps, the footlights on the kick | heavy: builds strobe the footlights |
| Bass | dubstep, bass music | dark, toxic green, red, purple | the panel as wide as the bass; half time on the snare | heavy; slams |
| Hip-hop | groovy, rapped | gold, purple, deep red | kick at the front, snare at the back; the spectrum rising | slams |
| Soul | groovy and warm; soul, funk, disco | disco gold, red, green, blue | the disco floor flipping on the beat | crash splashes, white on the drop |
| Latin | groovy and happy; reggae, ska, latin | rasta red, gold, green; coral, turquoise | the footlights on the off-beat | crash splashes, white on the drop |
| Country | warm, mid-energy; country, folk | warm whites, amber, sunset | slow breath and wash, half time | swells; gaps dim; never flashes |
| Oldies | the 50s, 60s and 70s | incandescent amber, cherry, mint | the marquee: chasing bulbs, bubble tubes, a spinning record | swells; gaps dim; never flashes |
| Jazz | jazz, blues, swing | amber, steel blue | almost still; follows the level, not the beat | shimmers, swells; ignores gaps |
| Chill | calm, slow songs; ambient, classical | lavender, soft gold, teal | slow breathing and flowing | shimmers, swells; ignores gaps |
| Arena | energetic, aggressive, groovy; in the running for metal, EDM and bass | dark, blue sparkles; white | blue bands snapping on the beat | the drop: the whole cabinet white, breaking into sparkles from the centre out |

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

### The song-aware moves

`show/moves.py`, drawn on every part in the shared cabinet picture:

- **starfield:** stars flying out from the middle, faster as they go; their
  speed is the plan's pace, and the kick pushes them on.
- **flow:** slow waves of the palette rolling across; the swell follows how
  loud the song has been lately, not each hit.
- **beams:** a rock show's moving heads, sweeping across in pairs on the
  plan's pace, hit by the kick, more of them in a big section.
- **splash:** a crash cymbal's light, a ring spreading from where it struck
  (a shimmer is its soft glow).

Scenes, by part (`show/parts.py`):

- **Upper:** breathe, wash, chase, sweep, rise, slab, wobble, marquee, tubes, eq, ribbon, starfield, flow, beams
- **Footlights:** glow, pulse, rows, chase, checker, offbeat, starfield, flow, beams
- **Level bar:** spectrum (in the palette), classic (the rainbow analyzer), vu, ribbon, starfield, flow, and in chunky blocks: drumkit (hi-hat, snare, kick, tom and crash, each hit by its own drum), tiles, mirror, bounce, trace (a heart-monitor scroll), ripple, counter (1-2-3-4), comet, fire, rain, sparks
- **Keypad:** sparkle, eq, pulse, glow, arena (on a Pi 5 the keypad sends
  its own frames: its chain is its own device there)
- **Arena:** one scene for every part, drawing the whole cabinet together

## Seeing it

admin › Light Show:

- **Now:** the song playing, the show it got and why (the analyzer's words
  for it), its colours, pace and accents, the section it's in, and its map
  as a timeline.
- **Shows:** each show with what it's made for, and a preview of it
  playing to a short demo song (intro, verse, a rising pre-chorus, a gap,
  the drop into the chorus, crash cymbals, the chorus again, the outro) as
  a kind of song: upbeat, moody, a love song, hard rock, or 80s
  (`services/jb_lights/preview.py`).
- **Songs:** the songs the cabinet has learned.

Without the cabinet, replay a real song through the real show code:

```
venv/bin/python -m tools.analyze_song song.m4a --json song.jsonl --quiet   # what the analyzer hears
venv/bin/python -m tools.show_preview song.jsonl --audio song.m4a --out song.mp4 --feel moody
venv/bin/python -m tools.show_preview song.jsonl --audio song.m4a --tour drumkit,tiles,ripple   # cycle the bar's scenes
```

The preview draws the cabinet's real pixel positions with the song's audio
and a caption of the show, tier, the section's character and label, the
movement rate, the scenes and the moments. `analyze_song` runs without the
ears, so the stream has no feel: `--feel` stands in for one (upbeat, moody,
love, hard, 80s) and the show is then chosen by fit; `--genre` stands in
for the tags. Run one render at a time on the Pi.

## Limits and next steps

- **The first play is live.** Until a song has been heard through once, its
  sections are found a bar and a beat late, and their roles are guesses.
  Seeking or pausing during the first play means it isn't learned yet.
- **The feel needs tags or ears.** An untagged song is judged by the
  analyzer's ears (YAMNet) and the sound alone, which can't tell a decade
  without tags.
- **Previewing a learned song** (its own map and feel, in admin) isn't
  built yet: the previews use the demo song.
- **Owner-made shows** say what they suit with format 2's `fits`; a format 1
  show is taken to suit a typical song of its genre groups.
