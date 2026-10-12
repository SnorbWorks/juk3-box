# AVRCP browsing tools

Debug scripts used to work out how the iPhone's library browses over
Bluetooth (Oct 11, 2026). Run them on the Pi. They browse only, never play.

| Script | What it does |
| --- | --- |
| `with_lease.sh script.py [args]` | Takes jb-source-phone's browse lease as "diag", runs the script only if the lease was granted, then releases it (jb-source-phone re-syncs Songs afterwards). Always browse through this: browsing without the lease fights the library sync. |
| `survey.py <mac>` | One fresh connection per branch (Playlists, Artists, Albums, Genres, Radio): lists each and peeks into a few children. Prints JSON. Reconnects the phone several times. Stop jb-source-phone first if not using the lease. |
| `walk_artists.py N` | Walks the first N artists with jb-crawl's own worker code and times every step; puts the cursor back on failure. |

What they showed (details in the jb-crawl worker's docstring and comments):

- The browse root can't be returned to; a reconnect is the only way back.
- After any listing, BlueZ fetches the whole folder from the phone in the
  background (~10 items per round trip). Calls meanwhile fail "busy" or
  queue and time out. Wait until BlueZ holds every item before the next step.
- BlueZ reuses an object when a listed item's phone ID matches one it
  already has (Artists' first entry comes back as My Music). Skip entries
  that aren't directly under the current folder.
- Walking big folders works but costs ~19 s per child folder.
- About 40 forced reconnects in an evening wedged the phone's Bluetooth;
  only turning Bluetooth off and on in the iPhone's Settings fixed it.
  Budget reconnects when testing.

Capture the raw traffic with `sudo btmon > /root/cap/bt.txt` (root can't
write to another user's file in /tmp).
