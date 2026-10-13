from services.jb_lights import buttons
from services.jb_lights.audio_feed import Snapshot
from services.jb_lights.themes.buttons_music import IDLE_W, ButtonsMusic


def snap(look, beat_age=9.0, drop_age=9.0):
    return Snapshot([k / 31 for k in range(32)], 0.8, beat_age, drop_age, 0.5, look)


def valid(frame):
    assert sorted(frame) == list(range(buttons.NUM_PIXELS))
    assert all(len(c) == 4 and all(isinstance(v, int) and 0 <= v <= 255 for v in c) for c in frame.values())


def run(m, look, frames=60, **kw):
    out = None
    for _ in range(frames):
        out = m.frame(1 / 30, snap(look, **kw))
    return out


def test_resting_idle_leaves_the_keypad_to_theme1():
    assert ButtonsMusic().frame(1 / 30, snap("idle")) is None


def test_eq_runs_bass_to_treble_across_letters_and_digits():
    f = run(ButtonsMusic(), "eq")
    valid(f)
    a, k = f[buttons.index("A")], f[buttons.index("K")]
    assert a[0] > a[2] and k[2] > k[1]                 # A red-ish (bass), K violet-ish (treble)
    assert sum(k[:3]) > sum(a[:3])                     # the treble band is fuller in this snapshot


def test_ribbon_renders_and_white_keys_pulse_on_the_beat():
    m = ButtonsMusic()
    f = run(m, "ribbon")
    valid(f)
    rest = f[buttons.SINGLE][3]
    on_beat = m.frame(1 / 30, snap("ribbon", beat_age=0.0))[buttons.SINGLE][3]
    assert on_beat > rest


def test_show_ends_back_on_idle_white_then_hands_over():
    m = ButtonsMusic()
    run(m, "eq")
    f = run(m, "idle", frames=44)                      # fading out (1.5 s)
    assert f is not None and all(c[3] >= IDLE_W - 10 for c in f.values())
    assert run(m, "idle", frames=5) is None


def test_after_an_interaction_it_fades_back_in_from_white():
    m = ButtonsMusic()
    run(m, "eq")
    m.resume()
    first = m.frame(1 / 30, snap("eq"))
    assert first[buttons.index("E")][3] > IDLE_W * 0.8  # still mostly white


def test_drop_flashes_white():
    m = ButtonsMusic()
    run(m, "eq")
    plain = m.frame(1 / 30, snap("eq"))
    flash = m.frame(1 / 30, snap("eq", drop_age=0.0))
    assert sum(c[3] for c in flash.values()) > sum(c[3] for c in plain.values()) + 1000
