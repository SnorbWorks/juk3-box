import numpy as np

from services.jb_analyzer import ears

RATE = 48000


def heard(signal):
    e = ears.Ears(RATE)
    for i in range(0, signal.size, 1600):
        e.feed(signal[i:i + 1600].astype(np.float32))
    return e.listen()


def top(scores):
    return max(scores, key=scores.get)


def test_the_mel_matrix_is_tensorflows():
    m = ears.mel_matrix()
    assert m.shape == (257, 64)
    assert not m[0].any()                                  # the DC bin is zeroed
    assert np.all(m >= 0) and m.max() <= 1.0
    # each band peaks at a higher frequency than the one before
    peaks = m.argmax(axis=0)
    assert np.all(np.diff(peaks) >= 0) and peaks[0] >= 3 and peaks[-1] <= 241


def test_it_hears_silence_a_beep_and_noise():
    t = np.arange(2 * RATE) / RATE
    assert top(heard(np.zeros(2 * RATE))) == "Silence"
    assert top(heard(0.3 * np.sin(2 * np.pi * 1000 * t))) == "Beep, bleep"
    noise = heard(0.1 * np.random.default_rng(1).standard_normal(2 * RATE))
    assert top(noise) == "White noise" and noise["White noise"] > 0.5


def test_nothing_until_a_whole_window_is_in():
    e = ears.Ears(RATE)
    e.feed(np.zeros(RATE // 2, dtype=np.float32))
    assert e.listen() is None
    e.feed(np.zeros(RATE // 2, dtype=np.float32))
    assert e.listen() is not None


def test_the_groups_a_show_reads():
    names = ears.class_names()
    assert len(names) == 521
    for group in list(ears.GROUPS.values()) + list(ears.MOODS.values()):
        assert all(n in names for n in group)
    assert all(n in names for n in ears.GENRES)
    g = ears.hear({"Singing": 0.4, "Rapping": 0.1, "Sad music": 0.3, "Rock music": 0.2, "Grunge": 0.5})
    assert g["voice"] == 0.4 and g["rap"] == 0.1 and g["moods"]["sad"] == 0.3
    assert g["genres"] == {"rock": 0.5}                    # a group's strongest genre


def test_the_downsampler_keeps_the_voice_band_and_cuts_above_8k():
    h = ears.lowpass(RATE)
    freqs = np.fft.rfftfreq(4096, 1 / RATE)
    gain = np.abs(np.fft.rfft(h, 4096))
    assert gain[np.argmin(np.abs(freqs - 3000))] > 0.95
    assert gain[np.argmin(np.abs(freqs - 9000))] < 0.01
