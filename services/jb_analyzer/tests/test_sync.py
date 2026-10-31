import numpy as np

from services.jb_analyzer.pipeline import Pipeline
from services.jb_analyzer.sync import Sync
from services.jb_analyzer.tests import songmaker as sm

PLAN = [("A", 4), ("B", 4), ("C", 4), ("D", 4), ("B", 2), ("A", 6), ("D", 4), ("C", 2)]


def replay(start_s, source=None, assume=None, seek=None, seconds=30.0):
    """Play the made-up song again from start_s; the source's word is off by
    `source` seconds (None: it says nothing). -> [(true position, sync's)]."""
    _, _, rec, x = sm.heard(PLAN, seed=4)
    sync = Sync(rec)
    pipe = Pipeline(sm.RATE, 1600)
    out = []
    t0, s = 500.0, int(start_s * sm.RATE)
    k = 0
    while s + 1600 <= x.shape[0] and k * 1600 / sm.RATE < seconds:
        if seek and abs(k * 1600 / sm.RATE - seek[0]) < 1e-6:
            s = int(seek[1] * sm.RATE)                       # jump elsewhere in the song
        t = t0 + (k + 1) * 1600 / sm.RATE
        true = (s + 1600) / sm.RATE
        pipe.frame(x[s:s + 1600])
        sync.frame(t, pipe.f)
        if pipe.tex is not None:
            sync.texture(t, pipe.tex)
        if k == 0 and assume is not None:
            sync.assume(t, assume)
        if source is not None and k % 60 == 0:
            sync.source(t, true + source)
        out.append((true, sync.position(t), sync.offset is not None))
        s += 1600
        k += 1
    return out


def test_it_locks_to_the_sound_though_the_source_is_off():
    run = replay(20.0, source=1.0)
    true, pos, locked = run[-1]
    assert locked and abs(pos - true) < 0.05
    assert abs(run[30][1] - run[30][0] - 1.0) < 0.05          # before the lock: the source's word


def test_a_seek_unlocks_it_and_it_finds_its_place_again():
    run = replay(5.0, source=0.4, seek=(15.0, 30.0), seconds=40.0)
    assert any(locked for true, pos, locked in run[:15 * 30])
    true, pos, locked = run[-1]
    assert true > 40 and locked and abs(pos - true) < 0.05


def test_with_no_word_from_the_source_it_takes_the_start_it_heard():
    run = replay(0.0, assume=0.0)
    true, pos, locked = run[-1]
    assert locked and abs(pos - true) < 0.05
