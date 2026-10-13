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
    for _ in range(int(2 * FPS)):
        states.append(tr.step(0.4, -38.0, True))       # bass falls away
    drops = 0
    for _ in range(int(1 * FPS)):
        states.append(tr.step(0.8, -12.0, True))       # bass slams back
        drops += tr.dropped
    assert "breakdown" in states and "drop" in states
    assert states.index("breakdown") < states.index("drop")
    assert drops == 1


def test_mood_axes_separate_slow_dark_from_fast_bright():
    calm, wild = SlowAnalyzer(FPS), SlowAnalyzer(FPS)
    for _ in range(int(10 * FPS)):
        calm.step({"_flux": 0.1, "_loudness": 0.25, "brightness": 0.2, "beat": False})
    for i in range(int(10 * FPS)):
        wild.step({"_flux": 5.0 if i % 10 == 0 else 0.1, "_loudness": 0.85, "brightness": 0.8, "beat": i % 10 == 0})
    c, w = calm.state(True)["mood"], wild.state(True)["mood"]
    assert c["arousal"] < w["arousal"] and c["warmth"] > w["warmth"]
    assert c["label"] == "moody" and w["label"] in ("bright", "energetic")
