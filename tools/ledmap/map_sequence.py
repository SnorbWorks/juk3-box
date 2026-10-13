import json, signal, sys, time
from rpi_ws281x import PixelStrip, Color, ws
N_UPPER, N = 189, 349
STEP_S = 0.25
FIRST_FLASH_S = 10
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
s = PixelStrip(N, 13, 800000, 10, False, 255, 1, ws.WS2811_STRIP_GRB)
s.begin()
OFF, FLASH, ON = Color(0, 0, 0), Color(30, 30, 30), Color(90, 90, 90)
def fill(c, upto=N_UPPER):
    for i in range(N):
        s.setPixelColor(i, c if i < upto else OFF)
    s.show()
log = {"step_s": STEP_S}
try:
    fill(OFF); time.sleep(3)
    # A long first flash: time to frame the panel and lock exposure while lit.
    log["flash_on"] = time.time(); fill(FLASH); time.sleep(FIRST_FLASH_S)
    log["flash_off"] = time.time(); fill(OFF); time.sleep(2.0)
    log["pixels"] = []
    log["refs"] = []
    for i in range(N_UPPER):
        if i and i % 25 == 0:
            # Reference flash: every pixel dim, to measure any drift.
            log["refs"].append(time.time()); fill(FLASH); time.sleep(0.5)
            fill(OFF); time.sleep(0.25)
        fill(OFF, 0)
        s.setPixelColor(i, ON); s.show()
        log["pixels"].append(time.time())
        time.sleep(STEP_S)
    fill(OFF); time.sleep(1.0)
    log["end_flash_on"] = time.time(); fill(FLASH); time.sleep(1.5)
    log["end_flash_off"] = time.time()
finally:
    fill(OFF, 0); s.show()
    json.dump(log, open("/tmp/map_times.json", "w"))
    print("sequence done,", len(log.get("pixels", [])), "pixels")
