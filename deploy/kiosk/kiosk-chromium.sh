#!/bin/sh
# Launches the JUKE.BOX kiosk browser. Started from sway's config:
#   exec /home/admin/kiosk-chromium.sh [extra chromium flags]
#
# The kiosk never exits Chromium cleanly -- it's killed on redeploy, or the
# Pi just loses power/reboots -- so Chromium always believes it crashed last
# time and briefly shows crash-recovery browser UI on the next start. Mark
# the last session as a normal exit before launching.
PREFS="$HOME/.config/chromium/Default/Preferences"
if [ -f "$PREFS" ]; then
  sed -i \
    -e 's/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/' \
    -e 's/"exited_cleanly":false/"exited_cleanly":true/' \
    "$PREFS"
fi

# The page comes from jb-ui, which may still be starting at boot. Wait for
# it (up to ~30s) rather than open a "can't connect" page. If it never
# answers, launch anyway: the engine page retries on its own once loaded,
# and agetty restarts this whole kiosk if Chromium exits.
KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8090/}"
for _ in $(seq 1 60); do
  curl -sf -o /dev/null "$KIOSK_URL" && break
  sleep 0.5
done

# Hardware video decoding with zero-copy: the Pi's H.264 decoder hands
# frames straight to the GPU. Without ZeroCopyGL every frame was copied
# through the CPU (~120% of a core with the background video playing,
# ~30% with it; knob scrolling went from ~30 fps to ~50). Measured
# 2026-10-10 on Chromium 152 (Raspberry Pi build).
VIDEO_FEATURES="AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL"

# On Wayland, --kiosk alone still first paints a normal browser window
# (tab strip + address bar) at its remembered size for a second or two
# before going fullscreen -- visible as a "browser flash" at startup.
# --app= has no browser UI at all, and starting at full screen size means
# even that first paint already fills the display. --force-dark-mode (with
# the page's color-scheme meta) keeps the instant before first paint dark
# rather than a white flash.
exec chromium \
  --kiosk \
  --enable-features="$VIDEO_FEATURES" \
  --app="$KIOSK_URL" \
  --start-fullscreen \
  --window-position=0,0 \
  --window-size=1920,1080 \
  --force-dark-mode \
  --ozone-platform=wayland \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --hide-crash-restore-bubble \
  --disable-translate \
  --disable-features=TranslateUI \
  --check-for-update-interval=31536000 \
  "$@"
