from services.jb_lights import accent, buttons, matrix
from services.jb_lights.show import arena, styles
from services.jb_lights.show.director import Director
from services.jb_lights.show.parts import Bar, Foot, Keys, Upper, finish
from services.jb_lights.tests.test_show import raw, run
from services.jb_lights.themes.accent_city import BRIGHTNESS
from services.jb_lights.themes.accent_diner import AMPS_PER_CHANNEL_FULL

DT = 0.025


def cues_at(beat, since_burst=1e9, tier="main", hat=0.0, kick=0.0, tempo=120.0):
    d = Director(pinned=lambda: "arena")
    cues, _ = run(d, raw(), 0, 0.1)
    cues.beat, cues.bar_beat, cues.tier, cues.tempo = beat, int(beat) % 4, tier, tempo
    cues.pulse = {**cues.pulse, "hat": hat, "kick": kick, "drive": kick}
    cues.since_burst = since_burst
    return cues


def amps(frame, scale=1.0):
    return sum(r + g + b for r, g, b in frame) / 255 * AMPS_PER_CHANNEL_FULL * scale


def luma(frame):
    return sum(0.21 * r + 0.72 * g + 0.07 * b for r, g, b in frame)


def test_the_owner_pins_the_style_for_every_song():
    d = Director(pinned=lambda: "arena")
    cues, _ = run(d, raw(genre="country"), 0, 0.5)
    assert cues.style.name == "arena" and set(cues.scene.values()) == {"arena"}
    d = Director(pinned=lambda: "no-such-style")
    cues, _ = run(d, raw(genre="country"), 0, 0.5)
    assert cues.style.name == "country"


def test_dark_with_a_few_blue_sparkles():
    up = Upper(accent.positions())
    frames = [up.arena(cues_at(k * 0.05, hat=0.5), None, 0, DT) for k in range(40)]
    lit = [sum(1 for c in f if sum(c) > 20) for f in frames[10:]]
    assert 0 < max(lit) < accent.UPPER * 0.25                  # sparkles, not a wash
    assert all(b >= r and b >= g for f in frames for r, g, b in f)


def test_the_footlights_swap_bands_on_the_beat():
    foot = Foot()
    x = foot.arena_look.x
    outer = foot.arena(cues_at(0.5), None, 0, DT)
    middle = foot.arena(cues_at(1.5), None, 0, DT)
    lit = lambda f, test: sum(sum(c) for c, xx in zip(f, x) if test(xx))
    assert lit(outer, lambda xx: xx < 1 / 6 or xx > 5 / 6) > 10 * lit(outer, lambda xx: 1 / 3 < xx < 2 / 3) + 1
    assert lit(middle, lambda xx: 1 / 3 < xx < 2 / 3) > 10 * lit(middle, lambda xx: xx < 1 / 6 or xx > 5 / 6) + 1


def test_a_burst_flashes_white_then_breaks_up_from_the_centre():
    up = Upper(accent.positions())
    dark = up.arena(cues_at(0.5), None, 0, DT)
    flash = up.arena(cues_at(0.5, since_burst=0.05), None, 0, DT)
    assert luma(flash) > 20 * max(1.0, luma(dark))
    assert all(abs(r - g) < 1 and abs(g - b) < 1 for r, g, b in flash)     # pure white
    look = up.arena_look
    mid = up.arena(cues_at(0.5, since_burst=0.6), None, 0, DT)
    order = sorted(range(len(mid)), key=lambda i: look.dist[i])
    near = [mid[i] for i in order[:len(order) // 3]]
    far = [mid[i] for i in order[-len(order) // 4:]]
    # The centre has broken up into sparkles; the edges are still a white glow.
    assert sum(1 for c in near if sum(c) < 5) > len(near) * 0.3
    assert all(sum(c) > 100 for c in far)
    over = up.arena(cues_at(0.5, since_burst=4.0), None, 0, DT)
    assert luma(over) < 0.1 * luma(flash)


def test_bursts_come_on_the_drop_not_the_moments_just_before_it():
    """Owner, 2026-10-30: the flash led the beat. A section lifting and the
    hit after a gap come just before a drop; only the drop (or a really big
    hit on its own) bursts."""
    d = Director(pinned=lambda: "arena")
    r = raw()
    cues, t = run(d, r, 0, 1.0)
    assert cues.since_burst > 100
    r.section_at, r.section_energy = t, "up"
    cues, t = run(d, r, t, 0.3)
    r.gap_at = t
    cues, t = run(d, r, t, 0.2)
    r.hit_at, r.hit_strength, r.hit_after_gap = t, 0.9, True
    cues, t = run(d, r, t, 0.3)
    assert cues.since_burst > 100                            # nothing yet
    r.drop_at = t
    cues, t = run(d, r, t, 0.05)
    assert cues.since_burst < 0.1 and cues.flash == 0        # the drop; arena flashes on its own
    r.hit_at, r.hit_strength, r.hit_after_gap = t, 0.95, False
    cues, t = run(d, r, t, 0.1)
    assert cues.since_burst > 0.1                            # too soon after the drop
    cues, t = run(d, r, t, 1.0)
    r.hit_at = t
    cues, t = run(d, r, t, 0.05)
    assert cues.since_burst < 0.1                            # a really big hit on its own
    r.drop_at = t
    cues, t = run(d, r, t, 0.05)
    assert cues.since_burst > 0.06                           # 0.05 s after: too close even for a drop
    cues, t = run(d, r, t, 0.4)
    r.drop_at = t
    cues, t = run(d, r, t, 0.05)
    assert cues.since_burst < 0.1                            # but a drop may follow a big hit soon


def test_every_frame_stays_inside_the_current_caps():
    """A busy loop with bursts, the kick and the hats hitting, at full gain."""
    up, foot, bar = Upper(accent.positions()), Foot(), Bar()
    worst_accent = worst_bar = 0.0
    for k in range(400):
        t = k * DT
        c = cues_at(t * 2, since_burst=t % 4.0, hat=1.0, kick=1.0)
        a = finish(up.arena(c, None, t, DT), c, "upper") + finish(foot.arena(c, None, t, DT), c, "foot")
        b = finish(bar.arena(c, None, t, DT), c, "bar")
        worst_accent = max(worst_accent, amps(a, BRIGHTNESS))
        worst_bar = max(worst_bar, amps(b))
    assert worst_accent <= arena.UPPER_FLASH_A + arena.FOOT_FLASH_A + 0.01 < 0.9 * accent.MAX_AMPS
    assert worst_bar <= arena.BAR_FLASH_A + 0.01 < 0.9 * matrix.MAX_AMPS


def test_the_keypad_sparkles_and_goes_white_in_a_burst():
    keys = Keys()
    c = cues_at(0.5)
    c.scene = {**c.scene, "keys": "arena"}
    assert len(keys.frame(c, None, DT)) == buttons.NUM_PIXELS
    c = cues_at(0.5, since_burst=0.05)
    c.scene = {**c.scene, "keys": "arena"}
    assert all(w == 255 for r, g, b, w in keys.frame(c, None, DT).values())


def test_a_build_or_a_breakdown_fades_the_cabinet_down_before_the_drop():
    up, keys = Upper(accent.positions()), Keys()
    lit = lambda f: sum(map(sum, f))

    def loop(**kw):
        up.arena_look.sparks.clear()
        total = 0.0
        for k in range(40):
            c = cues_at(k * 0.05, hat=0.6, kick=0.5)
            for name, v in kw.items():
                setattr(c, name, v)
            total += lit(up.arena(c, None, 0, DT))
        return total
    assert loop(cool=1.0) < 0.15 * loop()
    assert loop(tension=1.0) < 0.15 * loop()
    c = cues_at(0.5, since_burst=0.05)
    c.cool = 1.0                                   # the drop still comes out full white
    flash = up.arena(c, None, 0, DT)
    assert all(abs(r - g) < 1 and abs(g - b) < 1 and r > 50 for r, g, b in flash)
    c = cues_at(0.5, kick=1.0)
    c.scene, c.cool = {**c.scene, "keys": "arena"}, 1.0
    assert max(max(v) for v in keys.frame(c, None, DT).values()) < 30


def test_the_keypad_does_the_footlights_bands():
    from services.jb_lights.show.parts import ROW_X
    keys = Keys()
    for beat, lit_x in ((0.5, lambda x: x < 1 / 6 or x > 5 / 6), (1.5, lambda x: 1 / 3 < x < 2 / 3)):
        c = cues_at(beat)
        c.scene = {**c.scene, "keys": "arena"}
        f = keys.frame(c, None, DT)
        for i, x in ROW_X.items():
            if lit_x(x):
                assert f[i][2] > 50, (beat, x)
