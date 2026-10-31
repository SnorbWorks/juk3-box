"""The ears: what's in the sound, from Google's YAMNet (521 sound classes
from AudioSet; Apache 2.0, models/LICENSE-yamnet), run in plain numpy.

YAMNet hears a 0.96 s window at 16 kHz: a log-mel spectrogram (64 bands,
125-7500 Hz, 10 ms hops) through a MobileNet v1 (a 3x3 convolution, then 13
depthwise-separable layers), averaged, then one sigmoid score per class.
This is the same arithmetic as the reference (tensorflow/models,
research/audioset/yamnet: features.py, yamnet.py), with each batch norm
folded into its convolution (tools/yamnet_convert.py). ~70 million
multiply-adds a window: ~20 ms on a Pi 5, run in a worker thread.

  Ears(rate).feed(samples)   the live capture, any rate that divides 48 kHz by
                             an integer (it's brought down to 16 kHz here)
  Ears.listen()              -> {class: score} for the newest window, or None
  hear(scores)               -> the groups the show cares about (voice, rap,
                                drums, guitar, ..., the moods and genres)

Pure: no I/O but loading the model.
"""

import csv
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent / "models"
WEIGHTS = MODEL_DIR / "yamnet.npz"
CLASS_MAP = MODEL_DIR / "yamnet_class_map.csv"

RATE = 16000
WIN, HOP, NFFT = 400, 160, 512             # 25 ms, 10 ms, the FFT
MEL_BANDS, MEL_LO, MEL_HI = 64, 125.0, 7500.0
LOG_OFFSET = 0.001
PATCH = 96                                 # 0.96 s of 10 ms frames
NEED = HOP * (PATCH - 1) + WIN             # samples in one window: 15600
# (layer, stride, filters) after layer1 (3x3 conv, stride 2, 32 filters)
SEPARABLE = [(2, 1, 64), (3, 2, 128), (4, 1, 128), (5, 2, 256), (6, 1, 256), (7, 2, 512), (8, 1, 512),
             (9, 1, 512), (10, 1, 512), (11, 1, 512), (12, 1, 512), (13, 2, 1024), (14, 1, 1024)]

# The groups a light show cares about, by YAMNet's class names (display_name
# in the class map). A group's score is its strongest class.
GROUPS = {
    "music": ["Music"],
    "speech": ["Speech", "Narration, monologue", "Conversation", "Child speech, kid speaking", "Speech synthesizer"],
    "voice": ["Singing", "Child singing", "Choir", "Vocal music", "A capella", "Yodeling", "Chant", "Humming",
              "Synthetic singing"],
    "rap": ["Rapping"],
    "drums": ["Drum kit", "Drum machine", "Drum", "Snare drum", "Bass drum", "Drum roll", "Rimshot",
              "Percussion"],
    "cymbal": ["Cymbal", "Hi-hat"],
    "guitar": ["Guitar", "Electric guitar", "Bass guitar", "Acoustic guitar", "Steel guitar, slide guitar",
               "Strum", "Distortion"],
    "synth": ["Synthesizer", "Sampler", "Electronic organ"],
    "keys": ["Piano", "Electric piano", "Keyboard (musical)", "Organ", "Hammond organ", "Harpsichord"],
    "strings": ["Bowed string instrument", "String section", "Violin, fiddle", "Cello", "Orchestra",
                "Double bass", "Harp"],
    "brass": ["Brass instrument", "Trumpet", "Trombone", "French horn", "Saxophone", "Wind instrument, woodwind instrument"],
}
MOODS = {"happy": ["Happy music"], "sad": ["Sad music"], "tender": ["Tender music"],
         "exciting": ["Exciting music"], "angry": ["Angry music"], "scary": ["Scary music"]}
# YAMNet's genre classes -> the lights' genre groups (show/styles.py GROUPS)
GENRES = {
    "Pop music": "pop", "Hip hop music": "hiphop", "Rock music": "rock", "Heavy metal": "metal",
    "Punk rock": "rock", "Grunge": "rock", "Progressive rock": "rock", "Rock and roll": "oldies",
    "Psychedelic rock": "rock", "Rhythm and blues": "soul", "Soul music": "soul", "Reggae": "latin",
    "Country": "country", "Swing music": "jazz", "Bluegrass": "country", "Funk": "soul", "Folk music": "country",
    "Jazz": "jazz", "Disco": "soul", "Classical music": "chill", "Opera": "chill", "Electronic music": "edm",
    "House music": "edm", "Techno": "edm", "Dubstep": "bass", "Drum and bass": "edm", "Electronica": "edm",
    "Electronic dance music": "edm", "Ambient music": "chill", "Trance music": "edm",
    "Music of Latin America": "latin", "Salsa music": "latin", "Flamenco": "latin", "Blues": "jazz",
    "New-age music": "chill", "Ska": "latin", "Afrobeat": "latin", "Gospel music": "soul",
    "Christian music": "soul", "Independent music": "rock", "Lullaby": "chill", "Background music": "chill",
    "Dance music": "edm", "Soundtrack music": "chill",
}


def class_names(path=CLASS_MAP) -> list:
    with open(path, newline="") as f:
        rows = csv.reader(f)
        next(rows)
        return [r[2] for r in rows]


def mel_matrix() -> np.ndarray:
    """tf.signal.linear_to_mel_weight_matrix(64, 257, 16000, 125, 7500): HTK
    mels, triangles between linearly spaced mel points, the DC bin zeroed."""
    def mel(f):
        return 1127.0 * np.log1p(np.asarray(f, dtype=np.float64) / 700.0)
    bins = NFFT // 2 + 1
    freqs = np.linspace(0.0, RATE / 2, bins)[1:]
    m = mel(freqs)[:, None]
    edges = np.linspace(mel(MEL_LO), mel(MEL_HI), MEL_BANDS + 2)
    lo, mid, hi = edges[:-2][None, :], edges[1:-1][None, :], edges[2:][None, :]
    w = np.maximum(0.0, np.minimum((m - lo) / (mid - lo), (hi - m) / (hi - mid)))
    return np.vstack([np.zeros((1, MEL_BANDS)), w]).astype(np.float32)


def log_mel(wave16: np.ndarray) -> np.ndarray:
    """(frames, 64) log-mel spectrogram of 16 kHz audio, as features.py makes it."""
    n = 1 + (wave16.size - WIN) // HOP
    if n <= 0:
        return np.zeros((0, MEL_BANDS), dtype=np.float32)
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN) / WIN)).astype(np.float32)   # periodic Hann
    mag = np.abs(np.fft.rfft(wave16[idx] * window, n=NFFT)).astype(np.float32)
    return np.log(mag @ _MEL + LOG_OFFSET)


_MEL = mel_matrix()


def _same(size, k, stride):
    """TensorFlow's 'same' padding: (before, after, out)."""
    out = -(-size // stride)
    total = max((out - 1) * stride + k - size, 0)
    return total // 2, total - total // 2, out


def _taps(x, stride):
    """The 9 shifted views of x (H, W, C) a 3x3 'same' kernel sees."""
    h, w = x.shape[:2]
    t, b, oh = _same(h, 3, stride)
    l, r, ow = _same(w, 3, stride)
    xp = np.pad(x, ((t, b), (l, r), (0, 0)))
    return [xp[i:i + stride * oh:stride, j:j + stride * ow:stride] for i in range(3) for j in range(3)]


class Model:
    def __init__(self, path=WEIGHTS):
        with np.load(path) as z:
            self.w = {k: z[k].astype(np.float32) for k in z.files}

    def __call__(self, patch: np.ndarray) -> tuple:
        """(scores (521,), embedding (1024,)) for one (96, 64) log-mel patch."""
        w = self.w
        x = patch.astype(np.float32)[:, :, None]
        taps = np.concatenate(_taps(x, 2), axis=2)                         # (48, 32, 9)
        x = np.maximum(0.0, taps @ w["l1_w"].reshape(9, -1) + w["l1_b"])
        for n, stride, _ in SEPARABLE:
            dw = w[f"l{n}_dw"]
            acc = None
            for k, view in enumerate(_taps(x, stride)):
                term = view * dw[k // 3, k % 3]
                acc = term if acc is None else acc + term
            x = np.maximum(0.0, acc + w[f"l{n}_db"])
            h, wd, c = x.shape
            x = np.maximum(0.0, (x.reshape(-1, c) @ w[f"l{n}_pw"] + w[f"l{n}_pb"]).reshape(h, wd, -1))
        emb = x.mean(axis=(0, 1))
        logits = emb @ w["logits_w"] + w["logits_b"]
        return 0.5 * (1 + np.tanh(0.5 * logits)), emb             # the sigmoid, without overflow


def lowpass(rate_in: int, taps: int = 97) -> np.ndarray:
    """A windowed-sinc low-pass for bringing rate_in down to 16 kHz (cut at 7.6 kHz)."""
    fc = 7600.0 / rate_in
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * fc * np.sinc(2 * fc * n) * np.kaiser(taps, 8.0)
    return (h / h.sum()).astype(np.float32)


class Ears:
    """The live capture in, YAMNet's scores out (the newest 0.96 s)."""

    def __init__(self, rate: int = 48000, model: Model | None = None):
        if rate % RATE:
            raise ValueError("the ears need a rate that's a multiple of 16 kHz")
        self.step = rate // RATE
        self.h = lowpass(rate)
        self.tail = np.zeros(self.h.size - 1, dtype=np.float32)
        self.phase = 0
        self.buf = np.zeros(NEED, dtype=np.float32)
        self.filled = 0
        self.model = model or Model()
        self.names = class_names()

    def feed(self, samples: np.ndarray):
        x = np.concatenate([self.tail, np.asarray(samples, dtype=np.float32)])
        y = np.convolve(x, self.h, mode="valid")              # each output uses h.size inputs
        self.tail = x[-(self.h.size - 1):]
        y = y[self.phase::self.step]
        self.phase = (self.phase - (x.size - self.h.size + 1)) % self.step
        n = min(y.size, NEED)
        self.buf = np.roll(self.buf, -n)
        self.buf[-n:] = y[-n:]
        self.filled = min(NEED, self.filled + n)

    def ready(self) -> bool:
        return self.filled >= NEED

    def listen(self) -> dict | None:
        """{class name: score} for the newest window (None until it's full)."""
        return self.score(self.snapshot())

    def snapshot(self) -> np.ndarray | None:
        """The newest window, copied (feed() goes on while a thread scores it)."""
        return self.buf.copy() if self.ready() else None

    def score(self, window: np.ndarray | None) -> dict | None:
        """{class name: score} for a window from snapshot()."""
        if window is None:
            return None
        scores, _ = self.model(log_mel(window)[:PATCH])
        return dict(zip(self.names, scores.tolist()))


def hear(scores: dict) -> dict:
    """The groups, the moods and the genre groups a show reads, 0..1."""
    def top(names):
        return max((scores.get(n, 0.0) for n in names), default=0.0)
    out = {g: round(top(names), 3) for g, names in GROUPS.items()}
    out["moods"] = {m: round(top(names), 3) for m, names in MOODS.items()}
    genres = {}
    for name, group in GENRES.items():
        genres[group] = max(genres.get(group, 0.0), scores.get(name, 0.0))
    out["genres"] = {g: round(v, 3) for g, v in sorted(genres.items(), key=lambda kv: -kv[1]) if v >= 0.02}
    return out
