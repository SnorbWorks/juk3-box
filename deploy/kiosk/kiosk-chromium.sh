#!/bin/sh
# Launches the Juk3box kiosk browser. Started from sway's config:
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
# Memory: the Pi has 1 GB and Chromium is most of it; when it runs short the
# kernel kills the page ("Aw, Snap!" / a dead kiosk -- 2026-10-13, scrolling
# Home with music and the analyzer running). So the binary is launched
# directly, not via the /usr/bin/chromium wrapper, which adds Raspberry Pi
# desktop defaults from /etc/chromium.d we don't want here:
#   --force-renderer-accessibility  builds a screen-reader copy of every page
#                                    update (memory + CPU, for no one)
#   --enable-remote-extensions, --load-extension=...  extension machinery
# The wrapper's useful ones are kept below (ANGLE on GLES, GPU raster, no
# /dev/shm, no pings). Plus: no extensions or component extensions, no
# background networking, and one renderer process.
# Chromium starts in the background so this script can unveil its window:
# sway opens it see-through (sway-config) and it stays that way, showing
# the splash wallpaper, until the page reports it has painted the same
# splash (jb-ui /api/kiosk-painted), else after 30 s regardless. Then the
# script waits on Chromium, so getty still restarts the kiosk if it exits.
STARTED=$(date +%s)
/usr/lib/chromium/chromium \
  --use-angle=gles \
  --enable-gpu-rasterization \
  --disable-dev-shm-usage \
  --no-default-browser-check \
  --disable-pings \
  --media-router=0 \
  --disable-extensions \
  --disable-component-extensions-with-background-pages \
  --disable-background-networking \
  --renderer-process-limit=1 \
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
  "$@" &
CHROMIUM=$!
for _ in $(seq 1 120); do
  AT=$(curl -sf "${KIOSK_URL%/}/api/kiosk-painted" | sed -n 's/.*"at": *\([0-9]*\).*/\1/p')
  [ -n "$AT" ] && [ "$AT" -ge "$STARTED" ] && break
  kill -0 "$CHROMIUM" 2>/dev/null || break
  sleep 0.25
done
swaymsg '[app_id="^chrom"] opacity 1' >/dev/null 2>&1
wait "$CHROMIUM"
