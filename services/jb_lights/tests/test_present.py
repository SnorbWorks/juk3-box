from services.jb_lights import accent, buttons, main, matrix, pio_chains, present, wizard_test
from services.jb_lights.tests.test_pio_chains import ctrl, make


def test_no_file_means_all_three(tmp_path):
    assert present.read(tmp_path / "lights.json") == {"buttons": True, "accent": True, "levelbar": True}


def test_the_file_turns_chains_off(tmp_path):
    f = tmp_path / "lights.json"
    f.write_text('{"buttons": true, "accent": false, "levelbar": false}')
    assert present.read(f) == {"buttons": True, "accent": False, "levelbar": False}
    f.write_text("not json")
    assert all(present.read(f).values())


def test_a_pi4_runs_what_is_wanted():
    run, notes = main.chains_to_run({"buttons": True, "accent": False, "levelbar": True}, None)
    assert run == {"buttons", "levelbar"} and notes == []


def test_a_pi5_skips_a_chain_without_its_device():
    run, notes = main.chains_to_run({"buttons": True, "accent": True, "levelbar": True}, {"accent"})
    assert run == {"accent", "levelbar"}
    assert len(notes) == 1 and "leds-buttons" in notes[0]


def test_no_lights_runs_nothing():
    run, _ = main.chains_to_run({"buttons": False, "accent": False, "levelbar": False}, {"buttons", "accent"})
    assert run == set()


def test_pio_chains_opens_only_the_devices_there(tmp_path):
    dev, gpiomem = make(tmp_path)
    (dev / pio_chains.BUTTONS_DEV).unlink()
    assert pio_chains.present(dev) == {"accent"}
    chains = pio_chains.Chains(dev, gpiomem, log=lambda *_: None)
    assert chains.buttons is None and chains.accent is not None
    # No buttons: the button pin isn't touched.
    assert ctrl(gpiomem, 12) == (4 << 5) | 7


def test_the_accent_load_level_draws_its_cap():
    level = wizard_test.accent_load_level()
    amps = accent.NUM_PIXELS * 3 * level / 255 * 0.020
    assert 70 <= level <= 76 and amps <= accent.MAX_AMPS


def test_identify_walks_one_spot_in_chain_order():
    a = wizard_test.strip_frame(buttons.NUM_PIXELS, 0.0, white=True)
    b = wizard_test.strip_frame(buttons.NUM_PIXELS, wizard_test.SPOT_STEP_S * 3, white=True)
    assert a.index(max(a)) == 0 and b.index(max(b)) == 3
    assert len(wizard_test.bar_frame(0.0)) == matrix.NUM_PIXELS
