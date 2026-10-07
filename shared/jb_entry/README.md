# jb_entry

Selection entry: one letter plus one digit, in either order, becomes a slot
such as `B7` (index = letter index * 10 + digit index; letters A-K without I,
digits 1-9 then 0, so `A1` = 0, `B7` = 16, `K0` = 99).

A pure state machine with no I/O, for `jb-core` to host once songboard
selection is built: feed it `key(key, t)` for each `jukebox/jb-input/key`
message, `cancel(t)` when something should clear a partial entry, and
`tick(t)` periodically for the timeout. Each call returns `EntryChanged` and
`Selection` events to publish; both have `.payload()`.

Not wired into jb-core yet.

    python -m pytest shared/jb_entry/tests
