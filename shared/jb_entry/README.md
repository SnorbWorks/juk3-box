# jb_entry

Selection entry: a letter followed by a digit becomes a slot such as `B7`.
Letter first only -- a digit with no letter waiting is ignored. Index =
letter index * 10 + digit index (letters A-K without I, digits 1-9 then 0),
so `A1` = 0, `B7` = 16, `K0` = 99: the same order as the board's slots.

A pure state machine with no I/O, hosted by `jb-core`: it feeds `key(key, t)`
from each `jukebox/jb-input/key` message and calls `tick(t)` for the 10 s
timeout, and publishes what comes back -- `EntryChanged` on
`jukebox/jb-core/entry` (retained) and `Selection` on
`jukebox/jb-core/selection`. The kiosk turns a selection into the track shown
at that slot and queues it.

    python -m pytest shared/jb_entry/tests
