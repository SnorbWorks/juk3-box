"""deploy/jb-setupctl's pure parts: config.txt's block and the EEPROM text."""

import importlib.machinery
import json
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "jb-setupctl"
loader = importlib.machinery.SourceFileLoader("jb_setupctl", str(SCRIPT))
spec = importlib.util.spec_from_loader("jb_setupctl", loader)
ctl = importlib.util.module_from_spec(spec)
loader.exec_module(ctl)

CONFIG = """# For more options and information see
dtparam=audio=off

[pi5]
dtoverlay=ws2812-pio,gpio=12,num_leds=23,rgbw,brightness=0,dev_name=leds-buttons
usb_max_current_enable=1

[all]
dtparam=spi=on
"""


def test_the_block_is_added_at_the_end_under_pi5():
    out = ctl.managed(CONFIG, [ctl.RTC_CHARGE])
    assert out.startswith(CONFIG.rstrip("\n"))
    assert out.endswith(f"{ctl.BEGIN}\n[pi5]\n{ctl.RTC_CHARGE}\n[all]\n{ctl.END}\n")
    assert ctl.block_lines(out) == [ctl.RTC_CHARGE]


def test_the_block_is_replaced_not_repeated_and_removed_when_empty():
    once = ctl.managed(CONFIG, [ctl.RTC_CHARGE])
    assert ctl.managed(once, [ctl.RTC_CHARGE]) == once
    assert once.count(ctl.BEGIN) == 1
    assert ctl.managed(once, []) == CONFIG


def test_lines_outside_the_block_are_never_touched():
    once = ctl.managed(CONFIG, [ctl.RTC_CHARGE])
    edited = once + "# the owner's own line\ndtparam=foo\n"
    again = ctl.managed(edited, [])
    assert "dtparam=foo" in again and ctl.BEGIN not in again


def test_eeprom_psu_is_set_and_cleared():
    text = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf461\n"
    on = ctl.eeprom_with(text, "5000")
    assert on.splitlines()[-1] == "PSU_MAX_CURRENT=5000"
    assert ctl.eeprom_with(on, "5000") == on
    assert "PSU_MAX_CURRENT" not in ctl.eeprom_with(on, None)
    assert "BOOT_ORDER=0xf461" in ctl.eeprom_with(on, None)


def test_requests_outside_its_list_are_refused():
    for bad in (["reboot"], ["psu", "3000"], ["fan"], ["timezone", "../etc/passwd"], ["wifi-country", "usa"],
                ["lights", "identify", "buttons,accent", "10"], ["lights", "load", "lasers", "10"],
                ["lights", "load", "accent", "999"], ["lastfm", "not-a-key"], ["lastfm", "A" * 32]):
        with pytest.raises(ctl.Refused):
            ctl.dispatch(bad)


def test_the_lastfm_key_is_set_and_cleared_keeping_the_other_settings():
    key = "0123456789abcdef0123456789abcdef"
    text = ctl.with_field('{"enrich_delay": 1}', "lastfm_api_key", key)
    assert json.loads(text) == {"enrich_delay": 1, "lastfm_api_key": key}
    assert ctl.with_field(text, "lastfm_api_key", key) is None
    assert json.loads(ctl.with_field(text, "lastfm_api_key", None)) == {"enrich_delay": 1}
    assert json.loads(ctl.with_field("", "lastfm_key", key)) == {"lastfm_key": key}
    assert ctl.with_field("", "lastfm_key", None) is None
