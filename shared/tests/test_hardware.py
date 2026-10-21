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


def test_suggestions_per_board():
    analyzer = {"id": "jb-analyzer", "boards": {"pi4": {"suggested": False, "why": "busy", "cost": "30%"}}}
    assert hardware.module_suggestion(analyzer, "pi4") == {"why": "busy", "cost": "30%"}
    assert hardware.module_suggestion(analyzer, "pi5") is None
    assert hardware.module_suggestion({"id": "jb-core"}, "pi4") is None
    assert hardware.module_suggestion({"boards": {"pi4": {"suggested": True}}}, "pi4") is None
    assert hardware.feature_suggestion("background_video", "pi4")["why"]
    assert hardware.feature_suggestion("whole_home", "pi4")["warn"] is True
    assert hardware.feature_suggestion("background_video", "pi5") is None
