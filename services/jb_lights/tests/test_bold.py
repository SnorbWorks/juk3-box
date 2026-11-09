"""The bold shows (show/bold.py): Flames, Strobe, Saturday Night Fever, Infinity."""

import colorsys
import math
from types import SimpleNamespace

import pytest

from services.jb_lights import accent, matrix
from services.jb_lights.show import bold, styles
from services.jb_lights.show.parts import Bar, Foot, Keys, Upper, ROW_X

BOLD_SHOWS = ("flames", "strobe", "fever", "infinity")


def cues(move=0.0, tier="main", tempo=120.0, rate=1.0, drive=0.5, palette=None, level=0.6):
    return SimpleNamespace(palette=palette or ((255, 0, 0), (0, 0, 255), (0, 255, 0)), tier=tier, tempo=tempo,
                           rate=rate, move=move, level=level, character="drive",
                           pulse={"drive": drive, "kick": drive, "snare": 0.0, "hat": 0.0, "crash": 0.0,
                                  "offbeat": 0.0})


class Snap:
    bands = [0.5] * 24

    def resample(self, n):
        return [0.5] * n


def test_the_shows_load_and_use_only_their_scenes():
    for sid in BOLD_SHOWS:
        st = styles.STYLES[sid]
        assert st.strobe == 0 and st.accents["crash"] == "none"       # no white flashes, no white splashes
        assert st.keys == "solid"
        for part in ("upper", "foot", "bar"):
            for tier in styles.TIERS:
                assert set(getattr(st, part)[tier]) <= set(styles.BOLD)


def test_vivid_and_hue_mix_never_go_grey():
    assert bold.vivid((255, 150, 200))[1] < 60              # a pastel pink comes back hot pink
    mid = bold.hue_mix((255, 0, 128), (0, 220, 255), 0.5)    # RGB would give a grey lavender
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in mid))
    assert s >= bold.SAT and v == 1.0


@pytest.mark.parametrize("tempo", [70, 100, 128, 150, 175, 200])
@pytest.mark.parametrize("rate", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("offset", [0.0, 0.5])
def test_strobe_flashes_at_most_three_times_a_second(tempo, rate, offset):
    dt, t, onsets, was = 1 / 200, 0.0, [], False
    while t < 10:
        c = cues(move=t * tempo / 60 * rate, tempo=tempo, rate=rate)
        on = bold.flash_env(c, offset)[1] > 0.5
        if on and not was:
            onsets.append(t)
        was, t = on, t + dt
    assert len(onsets) >= 5
    gaps = [b - a for a, b in zip(onsets, onsets[1:])]
    assert min(gaps) >= 1 / bold.MAX_HZ - 0.01


def lit_saturation(frame):
    out = []
    for c in frame:
        if max(c) > 40:
            out.append(colorsys.rgb_to_hsv(*(v / 255 for v in c))[1])
    return out


@pytest.mark.parametrize("scene", styles.BOLD)
@pytest.mark.parametrize("tier", ["calm", "main", "peak"])
def test_every_part_draws_strong_solid_colour(scene, tier):
    upper, foot, bar = Upper(accent.positions()), Foot(), Bar()
    pastel = (255, 150, 200), (150, 200, 255), (255, 230, 150)      # even a pastel palette comes out strong
    sats, lit = [], 0
    for k in range(40):
        c = cues(move=k * 0.37, tier=tier, palette=pastel)
        for part in (upper, foot, bar):
            frame = getattr(part, scene)(c, Snap(), k * 0.05, 0.05)
            assert len(frame) == part.size
            sats += lit_saturation(frame)
            lit += sum(1 for px in frame if max(px) > 40)
    assert lit > 0
    assert min(sats) >= 0.85


def test_keys_solid():
    c = cues()
    c.scene, c.black, c.flash, c.style = {"keys": "solid"}, 0.0, 0.0, styles.STYLES["strobe"]
    c.look_gain, c.lift, c.tension, c.cool = 1.0, 0.0, 0.0, 0.0
    lit = Keys().frame(c, Snap(), 0.05)
    for i in ROW_X:
        r, g, b, w = lit[i]
        assert w == 0 and max(r, g, b) > 100


def test_fever_beams_land_where_they_point():
    c = cues(move=3.0)
    beams = bold.rig(c)
    for (sx, sy, a, colour, _), (lx, lc, _) in zip(beams, bold.landings(beams)):
        assert lc == colour and math.isclose(lx, sx + bold.FLOOR_D * math.tan(a))
