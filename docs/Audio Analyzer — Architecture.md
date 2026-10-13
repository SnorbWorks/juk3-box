# Audio Analyzer — Architecture

Oct 10, 2026 · @shawn

The Audio Analyzer listens to whatever the jukebox is playing and produces a running description of the music — beat, energy, brightness, tempo, mood, and song identity — published once as a feature stream. It does not drive hardware itself: the lights, the background visuals, and anything else subscribe to it. This doc covers the analyzer from capture through to that published stream, and records the intent for what consumes it.

## Purpose and scope

The analyzer's whole job is to understand the music and describe it. It does not touch a single pixel. That separation is deliberate: the lights already have their own system (jb-lights), which owns the pixels, the rendering, the current limiter, and the choreography. The analyzer is upstream of all of that.

- **In scope:** capturing the audio inside the Pi, running the analysis, extracting fast and slow features, detecting beats and builds and drops, identifying the song, and deciding its mood — then publishing all of that as one feature stream.
- **Out of scope:** what anything does with the stream. The lights, the background visuals, and any future consumer subscribe to it and make their own decisions.
- **Why its own system:** the stream is meant to feed many things, not just the light bar. Building it as a shared source of truth keeps every consumer — lights, screen, logging — reading one consistent description of the music.

The analyzer runs as its own asyncio service under systemd, publishing over local MQTT like the other jukebox services.

## Capturing the audio

The music already arrives on the Pi digitally — decoded from the Bluetooth stream off the phone. So there is no microphone and no analog input. The analyzer taps the audio inside the Pi, between where it is decoded and where it leaves for the amp.

- **Clean copy.** A digital tap gives an exact, noise-free copy of what is playing — no room noise, no mic coloring.
- **Non-disruptive.** In PipeWire the tap is a monitor on the output, so it reads the stream without disturbing what goes out to the DAC.
- **Always available.** Whatever the source — phone now, hard drive or streaming later — the analyzer sees the same decoded audio at the same point.

The tap feeds the analysis in small chunks, on the order of 1,024 samples at a time, read continuously.

## Fast layer — reacting to the sound now

The core tool is the FFT (Fast Fourier Transform). It takes each chunk of audio and converts it from "how loud, moment to moment" into "how much energy is in each frequency band" — bass through treble, across the spectrum. Run a few dozen times a second, it gives a live readout of the sound's shape. On a Pi 4 this is cheap: numpy does it in well under a millisecond per chunk.

That one operation yields the fast features:

- **Volume / overall energy** — sum the whole spectrum. Drives anything that pulses with loudness.
- **Spectrum / bands** — keep the bands separate. The raw material for a spectrum display.
- **Spectral brightness** — whether the energy sits low (warm, dark, bassy) or high (bright, sharp, airy).
- **Beat / onset** — watch the bass band for sudden jumps to find the kick; onset rate measures how busy the track is.

Raw FFT is jumpy, so the fast layer adds the refinements any musician's tool uses: smoothing so values fall gracefully instead of flickering, and frequency weighting so the readout matches how the ear hears. This layer always runs, needs no internet, and never guesses wrong about what is playing — it is measuring the actual sound.

## Trajectory and build-drop

A plain beat detector lives in the present — it sees the kick happening now. A build-and-drop is about *trajectory*: energy climbing over many seconds, the track thinning out, then everything slamming back. To catch it, the analyzer can't watch only the current frame; it needs short-term memory of the shape over time. This is the feature that makes the lights feel like a live light show rather than something twitching a beat behind.

There are detectable signatures in a build:

- **The riser** — energy and brightness climbing steadily over many seconds. A slope you can measure.
- **The breakdown** — right before the drop, the bass and low end often fall away; the track goes thin and bright. That sudden emptying-out is a strong tell that a drop is coming.
- **The drop** — bass slams back in; low-frequency energy spikes hard and instantly after the thin moment.

With those, the cabinet can do what a human lighting operator does: feel the build, hold back as it rises — tightening or cooling the lights — then blow out on the drop. That anticipation is what sells it.

**The honest catch:** a build and an ordinary quiet-verse-into-chorus can look alike for a while, and you often don't know it was a build until the drop confirms it. Expect the occasional head-fake. Catching most of them, and hitting hard on the ones you catch, is enough.

## Slow layer — understanding the song

The slow layer changes a few times a song, not many times a second. It comes in two parts.

**From the audio itself (local, immediate).** The same FFT stream yields features that map onto mood without knowing anything about the song:

- **Tempo** — beats per minute, from the spacing of the bass hits. Separates slow from fast directly.
- **Energy** — loud and dense versus sparse and quiet.
- **Spectral brightness** (sustained) — warm and dark versus bright and sharp.
- **Onset rate** — how busy the track is.

Together these already say "slow, dark, sparse" versus "fast, bright, busy" — enough to drive a blue-versus-vibrant palette. It works on any song, including ones nothing online knows about.

**From outside (slower, needs internet).** Acoustic fingerprinting: the Pi listens a few seconds, computes a fingerprint, and asks a service — the open option is AcoustID paired with the MusicBrainz database — what it is. That returns title, artist, album. From there, mood and genre tags (happy, melancholy, aggressive, chill) can be pulled from services that tag tracks.

**How the two parts combine.** The local features run from the first beat and drive the palette immediately. Fingerprinting arrives a few seconds later and *refines* the mood when it is confident. It can fail or come back "unknown," and it lags the song's opening — so it is always a refinement on top of the local read, never a prerequisite.

## The published feature stream

The analyzer's single output is a running description of the music, published over local MQTT. Everything else is a subscriber. The stream has two speeds, and keeping them distinct is the whole point — mixing them is what makes amateur builds feel either frantic or inert.

- **Fast channel** — updates many times a second: volume, the spectrum bands, brightness, beat/onset, and the build-drop state (rising, breakdown, drop). This is what makes a bar bounce or a button flash on the kick. Twitchy by nature.
- **Slow channel** — updates a few times a song: tempo, sustained energy, mood/palette, and song identity (title, artist, album, art). This is what makes the cabinet feel like it understands what's playing. Steady by nature.

The design rule: **the fast stuff rides on top of the slow stuff.** The palette (slow) sets *which* colors; the beat (fast) sets *when* they move. A consumer reads both and lets the slow channel frame the fast one.

The analyzer stops at this boundary. It publishes the description; it never decides what a pixel or a screen does with it.

## Intent — feeding the lights and background

This section records *why* the stream is shaped the way it is — what it is meant to enable. The choreography itself is owned by the lights' own system (jb-lights); this is the handoff and the intent behind it.

**One shared mood state drives everything.** The same mood the stream reports picks the palette for the lights *and* the background visuals — blue and slow for a moody track, red for a slow one, vibrant for a fast one. Lights and screen read one mood so they always agree: bar, buttons, and background breathing together. It is one mood feeding two outputs, not two systems.

**Follow the moment.** Mood tracks the music as it moves, not just the song's average. A quiet bridge in an upbeat song should pull the cabinet toward calm and then climb back out. The build-drop signals are part of this — the show should rise with a build and detonate on the drop.

**A dance, not a color wash.** The goal is lights that move, not just change color. This is why physical position matters, below.

**Unified coordinate space (the direction for the lights).** The cabinet has pixels in many places — the level bar, the button field, and accent panels being built. The intent is to treat the whole cabinet as one coordinate space: every pixel, whatever channel or strip drives it, gets a position (left/right, high/low). Then animations are written as *ideas* — "sweep upward," "pulse outward from center," "chase left to right," "flash everywhere on the drop" — and the system maps them to actual pixels. Wiring and choreography become separate layers. This marries to trajectory: the build rises up the cabinet, the breakdown pulls inward and cools, the drop explodes outward — the music's shape moving through real space.

**Background visuals ride the same mood.** The screen's backdrop is driven by the shared mood state too — calm, slow-moving for dark songs; energetic for bright ones — so the light show and the screen show are one coordinated thing.

*A full pixel map — where each run lands, how many pixels, physical position — is the input needed before the coordinate space and choreography can be built. That belongs to the lights' doc.*

## Opportunities this exposes

Once both layers run, the analyzer stops being a light-driver and becomes the cabinet's sensory system. Everything here is just another subscriber to the same stream:

- **Now-playing identity.** Fingerprinting gives title and artist, so the screen can show the song name, album art pulled from the web, maybe the year. A real jukebox never knew what it was playing.
- **Attract mode.** When no one is selecting, the cabinet puts on a show to whatever is playing — the thing that draws someone across a room to it.
- **Play history and taste.** Because it identifies songs, it can quietly log what played and how often. That feeds a "most popular" sort on the song board, and later, recommendations.
- **Visualizer variety.** Spectrum, waveform, beat-reactive particle fields — all just different renderers reading the same feature stream. Build the stream once, swap visualizers forever.
- **Music as a universal trigger.** Any cabinet event can key off the music without a microphone: a drop flashes the buttons, a quiet passage dims the screen. The whole box becomes responsive rather than scripted.

The common thread: all of it hangs off one well-designed feature stream. Build that stream to carry everything above from the start, and each feature is just a subscriber. Build it narrowly for the light bar, and each one is a retrofit.

### Signal-level features

Three features share one loudness/level measurement from the analyzer. Two stay purely analyzer-side; the third crosses into the output path because it acts on the audio rather than just observing it.

- **Silence / end-of-song detection** (analyzer-side). A bottom threshold on the level: audio near-zero for a second or two fires a "silence" event on the stream. A free trigger for advancing the queue, dropping into attract mode, or dimming the screen — without waiting on the phone to report anything.
- **Clipping / amp protection** (analyzer-side). A top threshold on the level: the signal pinning near maximum fires a warning, where distortion and speaker strain live. Can flash a subtle indicator or trigger a protective gain pull before it hurts the amp or ears.
- **Auto-leveling / loudness normalization** (measured here, applied in the output path). Levels every song to the same perceived loudness so a quiet track and a loud one land the same — no reaching for the knob between songs. This one acts on the audio, so it crosses the analyzer's boundary: the analyzer measures loudness, a gain stage in the output path (a PipeWire filter) applies the correction.

**How the leveling works.** Measure perceived loudness on the LUFS standard (what streaming services use), compare each song to a target, and apply a gentle, slow gain offset so nothing pumps or breathes. Two timescales: a per-song correction for the big differences, and optionally a faster safety limiter riding underneath for sudden peaks — which is where it ties back into clipping protection.

## Build order and dependency chain

The analyzer builds lowest to highest: get audio in, measure it, remember it, understand it, publish it, then let things consume it. Each stage is the input to the next, so each can be proven before the one above it starts.

**Timing principle (applies to every stage).** The fast layer runs live and must never block playback. The slow lookup (web fingerprint) is the only laggy part, and it must never stall the music. The rule everywhere: pre-fetch the expensive slow features, run the cheap fast features live. A cold track the system has never heard either pre-rolls (DJ mode, below) or starts on a neutral look and snaps to its real mood a second later; a track that came up through the queue is already analyzed and plays smooth from the downbeat.

- **Stage 0 — Capture.** PipeWire monitor tap on the output, reading \~1,024-sample chunks continuously. Everything depends on this. *Done when:* a continuous stream of real samples comes off the playing audio.
- **Stage 1 — Fast layer.** FFT plus the four fast features: volume, bands, brightness, beat/onset, with smoothing and frequency weighting. Depends only on Stage 0. *Done when:* the numbers track the music — bass jumps on the kick, brightness rises on a bright track.
- **Stage 2 — Signal-level thresholds.** Silence detection (bottom threshold) and clipping detection (top threshold). Cheapest thing built on the level measurement, dropped in early so the silence event is available to the rest of the jukebox. Depends on Stage 1. *Done when:* a quiet gap fires a silence event and a hot signal fires a clip warning.
- **Stage 3 — Trajectory and build-drop.** The first stage that needs short-term memory: the riser, the breakdown, the drop, read from the fast layer's history over many seconds. Sits above Stage 1. *Done when:* a track with an obvious build and drop fires the right state transitions (head-fakes accepted).
- **Stage 4 — Slow layer, local half.** Tempo, sustained energy, sustained brightness, onset rate — mood read purely from the audio, no internet. A sibling of Stage 3 (both read the fast layer over time), not stacked on it. *Done when:* a slow dark song and a fast bright song give clearly different mood values offline.
- **Stage 5 — Slow layer, fingerprint half.** AcoustID + MusicBrainz for title/artist/album, then mood/genre tags. Deliberately above the local half: fingerprinting refines, never gates. The local mood must already work before identity is bolted on, so an unknown or failed lookup degrades gracefully. *Done when:* known songs get identified and the tags sharpen the mood, and unknown songs still work on the local read alone.
- **Stage 6 — The published stream.** The two-speed MQTT output: fast channel (volume, bands, brightness, beat, build-drop) and slow channel (tempo, energy, mood/palette, identity). The capstone of the analyzer — it can only be finalized once Stages 1–5 define what flows into it. The one hard gate: nothing downstream exists until the stream does. *Done when:* a subscriber sees fast features updating many times a second and slow features updating a few times a song.

Everything below is a consumer sitting on top of Stage 6; none can start until the stream exists.

- **Stage 7 — Auto-leveling.** The one feature that acts on the audio rather than observing it — a LUFS-based gain stage in the output path. Separated from the Stage 2 thresholds and placed late because it touches the audio path and needs a trustworthy measurement first. *Done when:* a quiet track and a loud track land at the same perceived loudness with no pumping.
- **Stage 8 — Consumers.** Lights, background visuals, now-playing screen, attract mode, play history, visualizers. Each is just a subscriber to the stream; they can be built in any order and in parallel, depending only on Stage 6.

### Pre-analysis and the song cache (DJ mode)

The analyzer shares the per-song show cache from the main jukebox plan (SQLite, keyed by title + artist). Pre-computing the slow features into that cache ahead of playtime is what lets songs play smooth from the first note. Three sources fill it, in priority order:

- **Queue pre-analysis.** While the current song plays, the analyzer walks the queue one track at a time (one at a time so it never spikes CPU), fingerprints each, works out its mood, and writes it to the cache. By the time a queued song starts, its mood is already waiting — no lookup at playtime. Needs Stages 5 and 6, so it lands alongside them in the build order.
- **DJ-mode pre-roll.** For a true cold track — the first song of a session, or one played with nothing queued behind it — the system can take a deliberate, honest beat: analyze the opening, get the fingerprint, decide the mood, *then* start the music already dressed correctly. Once queue pre-analysis is running, pre-roll is only ever needed for these rare cold starts; everything that came up through the queue is already cached.
- **Idle-time library analysis.** After the machine has sat idle for a predefined period, that becomes the trigger to work through the library in the background — analyzing tracks it hasn't seen yet and caching their mood, one at a time. Over time the whole library gets captured during downtime, so fewer and fewer songs are ever truly cold. Lowest-priority background work: it yields immediately the moment someone interacts or music starts.

**What pre-analysis does and doesn't give you.** It pre-computes the *slow* features — identity, mood, palette — because those only need a fingerprint and a few seconds of audio. It does **not** pre-compute the *fast* features (live beat, build-drop), which must run against the audio as it actually plays. That split is the whole trick: pre-fetch the expensive slow stuff, run the cheap fast stuff live.

### Status — as built (2026-10-13)

`services/jb_analyzer` (the `jb-analyzer` service), publishing `docs/contracts/audio-analysis.md` v1. Tuned against three real songs: Don't Fear the Reaper (140 bpm, bright, no drops), Alone Again (Naturally) (86 bpm, calm, no drops), Don't Wait Up (one breakdown, one drop at the real drop).

- **Stages 0–4, 6 — done.** Capture is the DAC sink's monitor (pre-volume: the jukebox knob doesn't change the readings; the phone's own volume does, so keep the phone at full). Published `level` and `bands` auto-range per song; absolute loudness drives silence, energy and build-drop. ~8% of one core, 42 MB.
- **Stage 5 — done differently.** No fingerprinting: the phone's AVRCP metadata already gives title/artist/album, so tags and genre come straight from Last.fm by name (`tags.py`, cached in `/var/lib/juk3-box`, key in `/etc/juk3-box/jb-analyzer.json`). Listener mood tags nudge arousal ±0.12. Fingerprinting waits for a source without metadata.
- **Stage 8 — first consumer: lights.** jb-lights follows the stream (`audio_feed.py`): EQ/analyzer for livelier moods, the ribbon for calmer ones, crossfades, a white flash on the drop.
- **Not yet.** Stage 7 auto-leveling; queue pre-analysis, DJ pre-roll and idle library analysis — the phone source can't hand over audio ahead of playback, so for it only the slow *tags* can be pre-fetched (by name), not audio features. Earmarked CPU trim: let capture sleep until a source reports "playing".
