"""The texture: what the music is made of, ten times a second.

  chroma   how much of each of the 12 notes (C, C#, ... B) is sounding, from
           a long FFT (16384 samples, 0.34 s: fine enough for bass notes),
           whitened against the spectrum's own envelope so it follows the
           notes, not the instruments' formants -- the harmony, for telling
           one section from another and finding the ones that come back
  timbre   13 mel-cepstrum coefficients (MFCC) of the fast layer's spectrum:
           the instrumentation's colour (c0, the overall level, first)
  width    the stereo picture: how much of the sound is to the sides
           (0 mono .. 1 wide). The capture is stereo; the fast layer hears
           its mid (left + right) and this the side (left - right)
  centre   how much of the voice band (300 Hz - 3.4 kHz) is dead centre
           rather than to the sides -- a lead voice sits there, so it rises
           when one comes in (against the song's own, as it varies a lot)

Key (below) reads the song's key and mode (major or minor) from its chroma
so far. Pure numpy, no I/O.
"""

import numpy as np

EVERY = 3                    # fast frames per texture frame (30 fps -> 10 a second)
LONG_FFT = 16384
CHROMA_LO, CHROMA_HI = 55.0, 5000.0
WHITEN_OCT = 1 / 3           # the envelope chroma is whitened against: this wide
MEL_N, MEL_LO, MEL_HI = 40, 60.0, 12000.0
N_MFCC = 13
VOICE_BAND = (300.0, 3400.0)
WIDE_BAND = (100.0, 10000.0)
NOTES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
# Krumhansl-Kessler key profiles (tonic first)
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def chroma_matrix(rate, n_fft, lo=CHROMA_LO, hi=CHROMA_HI) -> np.ndarray:
    """(12, bins): each bin's share of the pitch class it's nearest to,
    fading out half a semitone either side."""
    freqs = np.fft.rfftfreq(n_fft, 1 / rate)
    m = np.zeros((12, freqs.size), dtype=np.float32)
    ok = (freqs >= lo) & (freqs <= hi)
    midi = 69 + 12 * np.log2(np.maximum(freqs[ok], 1.0) / 440.0)
    nearest = np.round(midi)
    w = np.cos(np.pi * (midi - nearest)) ** 2            # 1 on the note, 0.5 a quarter-tone off
    pc = (nearest.astype(int)) % 12
    idx = np.nonzero(ok)[0]
    m[pc, idx] = w
    return m


def mel_filters(rate, n_fft, n=MEL_N, lo=MEL_LO, hi=MEL_HI) -> np.ndarray:
    """(n, bins) triangular mel filters (HTK mels)."""
    def mel(f):
        return 2595 * np.log10(1 + np.asarray(f) / 700)
    def hz(m):
        return 700 * (10 ** (np.asarray(m) / 2595) - 1)
    freqs = np.fft.rfftfreq(n_fft, 1 / rate)
    edges = hz(np.linspace(mel(lo), mel(hi), n + 2))
    fb = np.zeros((n, freqs.size), dtype=np.float32)
    for k in range(n):
        a, b, c = edges[k], edges[k + 1], edges[k + 2]
        up = (freqs - a) / (b - a)
        down = (c - freqs) / (c - b)
        fb[k] = np.maximum(0, np.minimum(up, down))
    return fb


def dct_matrix(n_in=MEL_N, n_out=N_MFCC) -> np.ndarray:
    k = np.arange(n_out)[:, None]
    i = np.arange(n_in)[None, :]
    d = np.cos(np.pi * k * (2 * i + 1) / (2 * n_in)) * np.sqrt(2 / n_in)
    d[0] /= np.sqrt(2)
    return d.astype(np.float32)


def _envelope_kernel(rate, n_fft):
    """The whitening envelope's width in bins at each frequency (a third of an octave)."""
    freqs = np.fft.rfftfreq(n_fft, 1 / rate)
    return np.maximum(3, (freqs * (2 ** (WHITEN_OCT / 2) - 2 ** (-WHITEN_OCT / 2)) / (rate / n_fft))).astype(int)


class Texture:
    def __init__(self, rate=48000, hop=1600, fft_size=4096):
        self.rate, self.hop, self.n = rate, hop, fft_size
        self.fps = rate / hop / EVERY
        self.long = np.zeros(LONG_FFT, dtype=np.float32)
        self.long_window = np.hanning(LONG_FFT).astype(np.float32)
        self.side = np.zeros(fft_size, dtype=np.float32)
        self.window = np.hanning(fft_size).astype(np.float32)
        self.win_gain = float(np.sum(self.window)) / 2
        self.chroma_m = chroma_matrix(rate, LONG_FFT)
        lf = np.fft.rfftfreq(LONG_FFT, 1 / rate)
        self.chroma_bins = np.nonzero((lf >= CHROMA_LO * 0.9) & (lf <= CHROMA_HI * 1.1))[0]
        self.env_w = _envelope_kernel(rate, LONG_FFT)[self.chroma_bins]
        self.mel = mel_filters(rate, fft_size)
        self.dct = dct_matrix()
        f = np.fft.rfftfreq(fft_size, 1 / rate)
        self.voice_bins = np.nonzero((f >= VOICE_BAND[0]) & (f < VOICE_BAND[1]))[0]
        self.wide_bins = np.nonzero((f >= WIDE_BAND[0]) & (f < WIDE_BAND[1]))[0]
        self.count = 0
        self.acc = None

    def _whiten(self, mag):
        """Each bin over the spectrum's envelope around it (its local mean), clipped:
        the peaks that are notes, not the overall tilt."""
        x = mag[self.chroma_bins]
        c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
        i = np.arange(x.size)
        lo = np.maximum(0, i - self.env_w // 2)
        hi = np.minimum(x.size, i + self.env_w // 2 + 1)
        env = (c[hi] - c[lo]) / (hi - lo)
        out = np.zeros_like(mag)
        out[self.chroma_bins] = np.clip(x / (env + 1e-9) - 1.0, 0, 4)
        return out

    def process(self, stereo: np.ndarray, power: np.ndarray) -> dict | None:
        """One hop of stereo audio (n, 2), with the fast layer's power
        spectrum of its mid; a texture frame every EVERY hops, else None."""
        left, right = stereo[:, 0], stereo[:, 1]
        mid = 0.5 * (left + right)
        n = mid.size
        self.long = np.roll(self.long, -n)
        self.long[-n:] = mid
        self.side = np.roll(self.side, -n)
        self.side[-n:] = 0.5 * (left - right)
        acc = power if self.acc is None else self.acc + power
        self.acc = acc
        self.count += 1
        if self.count % EVERY:
            return None
        self.acc = None
        power = acc / EVERY
        # chroma
        mag = np.abs(np.fft.rfft(self.long * self.long_window))
        w = self._whiten(mag)
        chroma = self.chroma_m @ (w * w)
        total = float(chroma.sum())
        chroma = chroma / chroma.max() if chroma.max() > 0 else chroma
        # timbre
        mel = np.log10(self.mel @ power + 1e-10)
        mfcc = self.dct @ mel
        # stereo
        sp = (np.abs(np.fft.rfft(self.side * self.window)) / self.win_gain) ** 2
        m_w, s_w = float(np.sum(power[self.wide_bins])), float(np.sum(sp[self.wide_bins]))
        m_v, s_v = float(np.sum(power[self.voice_bins])), float(np.sum(sp[self.voice_bins]))
        width = s_w / (m_w + s_w + 1e-12)
        centre = m_v / (m_v + s_v + 1e-12)
        return {"chroma": chroma.astype(np.float32), "tonal": total, "mfcc": mfcc.astype(np.float32),
                "width": round(float(np.clip(2 * width, 0, 1)), 3), "centre": round(centre, 3)}


class Key:
    """The song's key so far: its chroma, weighted by how tonal each moment
    was, against the 24 major and minor key profiles."""

    def __init__(self):
        self.sum = np.zeros(12)

    def reset(self):
        self.sum[:] = 0

    def add(self, chroma, weight=1.0):
        self.sum += np.asarray(chroma, dtype=float) * weight

    def read(self) -> dict | None:
        """{"key": "A", "mode": "minor", "major": -1..1 (how major it leans), "sure": 0..1}, or None."""
        if not self.sum.any():
            return None
        x = self.sum - self.sum.mean()
        scores = []
        for mode, prof in (("major", MAJOR), ("minor", MINOR)):
            p = prof - prof.mean()
            for tonic in range(12):
                r = float(np.dot(x, np.roll(p, tonic)) / (np.linalg.norm(x) * np.linalg.norm(p) + 1e-12))
                scores.append((r, mode, tonic))
        scores.sort(reverse=True)
        best, second = scores[0], scores[1]
        maj = max(r for r, m, _ in scores if m == "major")
        mino = max(r for r, m, _ in scores if m == "minor")
        return {"key": NOTES[best[2]], "mode": best[1], "major": round(float(np.clip((maj - mino) * 4, -1, 1)), 3),
                "sure": round(float(np.clip((best[0] - second[0]) * 5 + best[0] * 0.5, 0, 1)), 3)}
