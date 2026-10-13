"""The fast layer: one hop of audio in, the sound right now out.

Pure numpy, no I/O, so it can be tested with synthetic signals. Each call to
FastAnalyzer.process() takes the newest `hop` mono samples (float, full scale
= 1.0) and returns the fast-channel fields of the audio-analysis contract
(docs/contracts/audio-analysis.md): level, peak, bands, brightness, beat,
onset -- plus a few internals the slower layers read (bass, flux).
"""

import numpy as np

N_BANDS = 32
F_LO, F_HI = 40.0, 16000.0
LEVEL_RANGE_DB = 24.0     # published level spans this far under the recent loud parts
LEVEL_REF_DECAY_DB = 0.5  # how fast the loud reference forgets (dB per second)
BAND_TAU_S = 3.0          # how fast each band's range adapts
BAND_FLOOR_DB = 45.0      # a band this far under the loudest (tilt-levelled) is "nothing there"


def a_weighting(freqs):
    """IEC A-weighting as a power gain (so the readout matches the ear)."""
    f2 = np.maximum(freqs, 1.0) ** 2
    ra = (12194.0 ** 2 * f2 ** 2) / (
        (f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194.0 ** 2))
    return (ra * 10 ** (2.0 / 20)) ** 2


class FastAnalyzer:
    def __init__(self, rate=48000, hop=1600, fft_size=4096):
        self.rate, self.hop, self.n = rate, hop, fft_size
        self.fps = rate / hop
        self.buf = np.zeros(fft_size, dtype=np.float32)
        self.window = np.hanning(fft_size).astype(np.float32)
        self.win_gain = float(np.sum(self.window)) / 2           # full-scale sine -> 1.0 amplitude
        freqs = np.fft.rfftfreq(fft_size, 1 / rate)
        self.freqs = freqs
        self.weight = a_weighting(freqs)
        edges = np.geomspace(F_LO, F_HI, N_BANDS + 1)
        # Each band: the bins whose centre falls in it (the nearest bin when a
        # low band is narrower than one bin).
        self.band_bins = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            idx = np.nonzero((freqs >= lo) & (freqs < hi))[0]
            if idx.size == 0:
                idx = np.array([int(np.argmin(np.abs(freqs - np.sqrt(lo * hi))))])
            self.band_bins.append(idx)
        self.bass_bins = np.nonzero((freqs >= 40) & (freqs < 150))[0]
        self.centroid_lo, self.centroid_hi = np.log(150.0), np.log(8000.0)
        # State
        self.bands = np.zeros(N_BANDS)
        self.band_mean = None                      # auto-range: each band's running mean and variance (dB)
        self.band_var = None
        centres = np.sqrt(edges[:-1] * edges[1:])
        self.tilt = 4.5 * np.log2(centres / 1000.0)  # music falls ~4.5 dB/octave; level it out for "is anything there"
        self.band_ref = -120.0                     # the loudest tilted band lately
        self.level = 0.0
        self.loudness = 0.0
        self.loud_ref = -60.0
        self.prev_log = None
        self.prev_bass_db = -120.0
        self.flux_hist = []                        # bass flux, last ~1.5 s, for the beat threshold
        self.onset_ref = 1e-3
        self.since_beat = 1e9

    def process(self, samples: np.ndarray) -> dict:
        n = samples.size
        self.buf = np.roll(self.buf, -n)
        self.buf[-n:] = samples
        spec = np.fft.rfft(self.buf * self.window)
        power = (np.abs(spec) / self.win_gain) ** 2               # full-scale sine bin -> 1.0
        wpower = power * self.weight

        # Loudness: A-weighted RMS in dBFS -> 0..1 over -60..0 dB, smoothed
        # (fast attack, slower release). Absolute, so it also moves with the
        # phone's volume: silence, energy and build-ups read this.
        rms_db = 10 * np.log10(np.sum(wpower) / 2 + 1e-12)        # /2: a sine's power is half its peak
        target = float(np.clip((rms_db + 60) / 60, 0, 1))
        self.loudness += (target - self.loudness) * (0.6 if target > self.loudness else 0.15)
        # Level (published): the same, against this song's own loud parts
        # lately, so a quiet master or a low phone volume still uses the
        # whole range. Real silence still reads 0.
        self.loud_ref = max(rms_db, self.loud_ref - LEVEL_REF_DECAY_DB / self.fps, -60.0)
        rel = float(np.clip((rms_db - (self.loud_ref - LEVEL_RANGE_DB)) / LEVEL_RANGE_DB, 0, 1))
        rel *= float(np.clip((rms_db + 70) / 10, 0, 1))
        self.level += (rel - self.level) * (0.6 if rel > self.level else 0.15)
        peak = float(np.max(np.abs(samples))) if n else 0.0

        # Bands: each band against its own recent behaviour (a running mean
        # and spread over a few seconds), so every column moves with the music
        # whatever the song's tonal balance or the phone's volume -- music only
        # swings a band by ~5 dB, so a fixed dB window looks like a flat wall.
        # A band far under the loudest one (nothing there) stays dark.
        band_db = np.array([10 * np.log10(np.mean(power[b]) + 1e-12) for b in self.band_bins])
        a = 1 / (BAND_TAU_S * self.fps)
        if self.band_mean is None:
            self.band_mean, self.band_var = band_db.copy(), np.full(N_BANDS, 9.0)
        dev = band_db - self.band_mean
        self.band_mean += a * dev
        self.band_var += a * (dev * dev - self.band_var)
        spread = np.maximum(np.sqrt(self.band_var), 2.5)
        tilted = band_db + self.tilt
        self.band_ref = max(float(tilted.max()), self.band_ref - 6.0 / self.fps)
        present = np.clip((tilted - (self.band_ref - BAND_FLOOR_DB)) / 12, 0, 1)
        target_bands = np.clip(0.38 + dev / (3.2 * spread), 0, 1) * present
        up = target_bands > self.bands
        self.bands = np.where(up, self.bands + (target_bands - self.bands) * 0.65,
                              self.bands + (target_bands - self.bands) * 0.18)

        # Brightness: spectral centroid of the weighted spectrum, on a log
        # scale between 150 Hz and 8 kHz.
        total = np.sum(wpower) + 1e-12
        centroid = float(np.sum(self.freqs * wpower) / total)
        brightness = float(np.clip((np.log(max(centroid, 1.0)) - self.centroid_lo)
                                   / (self.centroid_hi - self.centroid_lo), 0, 1))

        # Onset: positive spectral flux across the spectrum (log power).
        logp = np.log10(power + 1e-10)
        flux = 0.0 if self.prev_log is None else float(np.sum(np.maximum(0, logp - self.prev_log)))
        self.prev_log = logp
        self.onset_ref = max(flux, self.onset_ref * (1 - 0.5 / self.fps))
        onset = float(np.clip(flux / (self.onset_ref + 1e-9), 0, 1))

        # Beat: a jump in bass energy well above its recent typical jumps,
        # at most ~4 a second.
        bass_db = 10 * np.log10(np.mean(power[self.bass_bins]) + 1e-12)
        bass_flux = max(0.0, bass_db - self.prev_bass_db)
        self.prev_bass_db = bass_db
        self.flux_hist.append(bass_flux)
        keep = int(1.5 * self.fps)
        self.flux_hist = self.flux_hist[-keep:]
        h = np.array(self.flux_hist)
        threshold = h.mean() + 1.6 * h.std() + 1.0
        self.since_beat += 1
        beat = bool(bass_flux > threshold and self.since_beat >= 0.25 * self.fps and target > 0.08)
        if beat:
            self.since_beat = 0

        return {
            "level": round(self.level, 3),
            "peak": round(min(peak, 1.0), 3),
            "bands": [round(float(x), 3) for x in self.bands],
            "brightness": round(brightness, 3),
            "beat": beat,
            "onset": round(onset, 3),
            # internals for the slower layers (not published as-is)
            "_loudness": round(self.loudness, 3),
            "_bass_db": bass_db,
            "_rms_db": rms_db,
            "_flux": flux,
        }
