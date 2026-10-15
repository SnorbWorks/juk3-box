from shared import hardware


def test_families():
    assert hardware.family_of("Raspberry Pi 4 Model B Rev 1.5") == "pi4"
    assert hardware.family_of("Raspberry Pi 5 Model B Rev 1.0") == "pi5"
    assert hardware.family_of("Raspberry Pi Compute Module 5 Rev 1.0") == "pi5"
    assert hardware.family_of("Raspberry Pi 400 Rev 1.0") == "pi4"
    assert hardware.family_of("Raspberry Pi 3 Model B Plus Rev 1.3") == "other"


def test_board_reads_the_device_tree(tmp_path):
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\x00")
    b = hardware.board(model)
    assert b["model"] == "Raspberry Pi 5 Model B Rev 1.0"
    assert b["family"] == "pi5" and b["led_driver"] == "pio"
    assert hardware.board(tmp_path / "missing") == {"model": "", "family": "other"}
