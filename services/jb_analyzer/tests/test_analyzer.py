import numpy as np

from services.jb_analyzer.dsp import N_BANDS, FastAnalyzer
from services.jb_analyzer.levels import Levels
from services.jb_analyzer.slow import SlowAnalyzer
from services.jb_analyzer.trajectory import Trajectory

RATE, HOP = 48000, 1600
FPS = RATE / HOP


def run(signal, fa=None):
    fa = fa or FastAnalyzer(RATE, HOP)
    out = []
    for i in range(0, signal.size - HOP + 1, HOP):
        out.append(fa.process(signal[i:i + HOP].astype(np.float32)))
    return out


def tone(freq, seconds, amp=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    return amp * np.sin(2 * np.pi * freq * t)


def kicks(bpm, seconds, amp=0.8):
    """A kick drum: a decaying 55 Hz thump on every beat, over quiet hiss."""
    n = int(seconds * RATE)
    sig = 0.01 * np.random.default_rng(1).standard_normal(n)
    period = int(RATE * 60 / bpm)
    t = np.arange(int(0.15 * RATE)) / RATE
    thump = amp * np.sin(2 * np.pi * 55 * t) * np.exp(-t * 25)
    for start in range(0, n - thump.size, period):
        sig[start:start + thump.size] += thump
    return sig


def test_bass_tone_lights_low_bands_and_reads_dark():
    f = run(tone(80, 2))[-1]
    bands = np.array(f["bands"])
    assert bands[:8].max() > 0.6 and bands[20:].max() < 0.2
    assert f["brightness"] < 0.2


def test_treble_tone_lights_high_bands_and_reads_bright():
    f = run(tone(6000, 2))[-1]
    bands = np.array(f["bands"])
    assert bands[24:].max() > 0.6 and bands[:8].max() < 0.2
    assert f["brightness"] > 0.8
    assert len(f["bands"]) == N_BANDS


def test_loudness_is_absolute_and_peak_reads_full_scale():
    loud = run(tone(1000, 1, amp=1.0))[-1]
    quiet = run(tone(1000, 1, amp=0.01))[-1]
    silent = run(np.zeros(RATE))[-1]
    assert loud["_loudness"] > 0.9 and quiet["_loudness"] < loud["_loudness"] - 0.4
    assert silent["_loudness"] < 0.02 and silent["level"] < 0.02
    assert loud["peak"] > 0.99


def test_level_adapts_to_a_quiet_source_but_follows_dips():
    # A song sent at a low volume still fills the range once it's playing...
    frames = run(np.concatenate([tone(1000, 3, amp=0.05), tone(1000, 1, amp=0.005)]))
    assert frames[int(2.5 * FPS)]["level"] > 0.9
    # ...and a passage 20 dB quieter than the rest reads well down.
    assert frames[-1]["level"] < 0.3


def test_kicks_at_120_bpm_give_beats_and_tempo():
    frames = run(kicks(120, 12))
    beats = sum(f["beat"] for f in frames[int(2 * FPS):])          # after the first 2 s
    assert 17 <= beats <= 23                                       # ~2 a second for 10 s
    slow = SlowAnalyzer(FPS)
    for f in frames:
        slow.step(f)
    state = slow.state(True)
    assert state["tempo"] is not None and abs(state["tempo"] - 120) < 6


def test_silence_and_sound_events():
    lv = Levels(FPS)
    events = []
    for _ in range(int(FPS)):
        events += lv.step(0.5, 0.3)
    for _ in range(int(2 * FPS)):
        events += lv.step(0.0, 0.0)
    events += lv.step(0.5, 0.3)
    assert events == ["sound", "silence", "sound"]


def test_clipping_fires_once_then_cools_down():
    lv = Levels(FPS)
    events = []
    for _ in range(int(FPS)):
        events += lv.step(0.9, 1.0)
    assert events.count("clip") == 1


def test_breakdown_then_drop():
    tr = Trajectory(FPS)
    states = []
    for _ in range(int(8 * FPS)):
        states.append(tr.step(0.6, -20.0, True))       # steady groove
    for i in range(int(5 * FPS)):
        # bass falls away -- with the odd stray bass note poking up
        states.append(tr.step(0.4, -24.0 if i % 40 == 0 else -38.0, True))
        assert not tr.dropped
    drops = 0
    for _ in range(int(1 * FPS)):
        states.append(tr.step(0.8, -12.0, True))       # bass slams back
        drops += tr.dropped
    assert "breakdown" in states and "drop" in states
    assert states.index("breakdown") < states.index("drop")
    assert drops == 1


def test_the_drop_lands_on_its_kick_not_a_third_of_a_second_later():
    tr = Trajectory(FPS)
    for _ in range(int(8 * FPS)):
        tr.step(0.6, -20.0, True, False)               # steady groove
    for i in range(int(5 * FPS)):
        # a breakdown with stray kicks that don't reach the groove
        tr.step(0.4, -24.0 if i % 20 == 0 else -38.0, True, i % 20 == 0)
        assert not tr.dropped
    tr.step(0.8, -12.0, True, True)                    # the drop's first kick
    assert tr.dropped and tr.state == "drop"


def test_mood_axes_separate_slow_dark_from_fast_bright():
    calm, wild = SlowAnalyzer(FPS), SlowAnalyzer(FPS)
    for _ in range(int(10 * FPS)):
        calm.step({"_flux": 0.1, "_loudness": 0.25, "brightness": 0.2, "beat": False})
    for i in range(int(10 * FPS)):
        wild.step({"_flux": 5.0 if i % 10 == 0 else 0.1, "_loudness": 0.85, "brightness": 0.8, "beat": i % 10 == 0})
    c, w = calm.state(True)["mood"], wild.state(True)["mood"]
    assert c["arousal"] < w["arousal"] and c["warmth"] > w["warmth"]
    assert c["label"] == "moody" and w["label"] in ("bright", "energetic")


def noise(seconds, amp, seed=2):
    return amp * np.random.default_rng(seed).standard_normal(int(seconds * RATE))


def moments_of(signal):
    from services.jb_analyzer.pipeline import Pipeline
    pipe, events = Pipeline(RATE, HOP), []
    for i in range(0, signal.size - HOP + 1, HOP):
        _, found = pipe.frame(signal[i:i + HOP].astype(np.float32))
        events += [(round(i / RATE, 2), e) for e in found if e["type"] not in ("sound", "clip")]
    return events


def test_a_gap_then_the_sound_slamming_back_is_a_gap_and_a_hit():
    sig = np.concatenate([noise(6, 0.3), noise(0.4, 0.002, 3), noise(2, 0.3, 4)])
    events = moments_of(sig)
    kinds = [e["type"] for _, e in events]
    assert kinds == ["gap", "hit"]
    (t_gap, _), (t_hit, hit) = events
    assert 6.0 <= t_gap <= 6.3 and 6.35 <= t_hit <= 6.6
    assert hit["after_gap"] is True


def test_a_steady_beat_is_not_a_hit_or_a_gap():
    assert moments_of(kicks(120, 12) + noise(12, 0.05)) == []


def test_a_new_section_is_noticed_once():
    sig = np.concatenate([tone(200, 16, amp=0.1) + noise(16, 0.01), tone(3000, 14, amp=0.4) + noise(14, 0.05, 5)])
    sections = [(t, e) for t, e in moments_of(sig) if e["type"] == "section"]
    assert len(sections) == 1
    t, e = sections[0]
    assert 16 <= t <= 20 and e["energy"] == "up"


def test_the_drums_each_get_their_own_reading():
    quiet = noise(2, 0.01)
    snare = quiet.copy()
    burst = np.random.default_rng(6).standard_normal(int(0.05 * RATE))
    sos_hi = np.fft.rfft(burst)
    f = np.fft.rfftfreq(burst.size, 1 / RATE)
    sos_hi[(f < 1500) | (f > 5000)] = 0
    crack = np.fft.irfft(sos_hi, burst.size)
    snare[RATE:RATE + crack.size] += 0.5 * crack / np.abs(crack).max()
    frames = run(snare)
    peak = max(frames, key=lambda f: f["snare"])
    assert peak["snare"] > 0.5 and peak["kick"] < 0.3
    frames = run(kicks(120, 3) + noise(3, 0.01))
    assert max(f["kick"] for f in frames) > 0.5


def test_the_beat_clock_counts_bars_and_follows_the_kicks():
    from services.jb_analyzer.moments import BeatClock
    clock = BeatClock(FPS)
    period = FPS / 2                                  # 120 bpm
    out = []
    for i in range(int(8 * FPS)):
        kick = (i + 4) % period < 1                   # the kicks land 4 frames after the clock started
        out.append((kick, *clock.step(120.0, kick)))
    late = [min(p, 1 - p) for kick, p, _ in out[int(4 * FPS):] if kick]
    assert late and max(late) < 0.1                   # pulled into step
    assert {b for _, _, b in out} == {0, 1, 2, 3}
    clock.downbeat()
    assert clock.step(120.0, False)[1] == 0
    assert BeatClock(FPS).step(None, True) == (None, None)


def noise_burst(seconds, decay, lo, hi=None, amp=0.5, seed=3):
    """Band-limited noise dying away: a cymbal, a hat, a snare's rattle."""
    n = int(seconds * RATE)
    X = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    fr = np.fft.rfftfreq(n, 1 / RATE)
    X[(fr < lo) | (fr > (hi or RATE))] = 0
    x = np.fft.irfft(X, n)
    return amp * x / np.abs(x).max() * np.exp(-np.arange(n) / RATE / decay)


def test_a_crash_cymbal_is_a_crash_and_rings_and_hats_or_a_snare_are_not():
    period = int(RATE * 60 / 120)
    beat = kicks(120, 8, amp=0.6)
    hat = noise_burst(0.05, 0.015, 7000, amp=0.15)
    for s in range(period // 2, beat.size - hat.size, period // 2):
        beat[s:s + hat.size] += hat                                 # hats on the eighths
    plain = run(beat.copy())
    assert all(f["crash"] == 0 for f in plain) and max(f["ring"] for f in plain[60:]) < 0.15
    crash = noise_burst(2.0, 0.8, 3000, amp=0.35, seed=4)
    beat[4 * RATE:4 * RATE + crash.size] += crash
    fr = run(beat)
    hits = [((i + 1) * HOP / RATE, f) for i, f in enumerate(fr) if f["crash"] > 0]
    assert len(hits) == 1
    t, f = hits[0]
    assert abs(t - f["crash_ago"] - 4.0) < 0.07                    # it says when it struck
    assert max(x["ring"] for x in fr[int(4.2 * FPS):int(5 * FPS)]) > 0.8      # and it rings on

    snares = kicks(120, 8, amp=0.6)
    n = int(0.2 * RATE)
    tt = np.arange(n) / RATE
    snare = noise_burst(0.2, 0.06, 1000, 5000, amp=0.4) + 0.3 * np.sin(2 * np.pi * 200 * tt) * np.exp(-tt / 0.05)
    for s in range(period, snares.size - n, 2 * period):
        snares[s:s + n] += snare                                    # on 2 and 4
    assert all(f["crash"] == 0 for f in run(snares))
