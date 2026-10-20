from services.jb_core.phone_choice import GRACE_S, PhoneChoice

BT = "AA:BB:CC:DD:EE:01"
BT2 = "AA:BB:CC:DD:EE:02"


def bt(mac=BT, connected=True, name="Lena's iPhone"):
    return {"connected": connected, "mac": mac, "name": name}


def app(*phones):
    """jb-source-helper/connected's phones: (device, reachable[, name])."""
    return {"phones": [{"device_id": d, "reachable": r, "name": (n[0] if n else "Shawn's iPhone")}
                       for d, r, *n in phones]}


def started(c=None, now=0.0):
    c = c or PhoneChoice()
    c.settled(now)
    return c


def switches(actions):
    return [(a[1] and a[1]["id"], a[2] and a[2]["id"]) for a in actions if a[0] == "switched"]


def test_the_first_phone_gets_the_jukebox_and_a_second_is_offered_not_switched():
    c = started()
    assert switches(c.bluetooth(bt(), 1)) == [(None, BT)]
    c.helper(app(("d1", True)), 2)
    assert c.state()["active"]["id"] == BT
    assert c.state()["offer"] == {"kind": "helper", "id": "helper:d1", "name": "Shawn's iPhone"}
    # Not now: the offer goes, and isn't made again while it stays.
    c.decline()
    c.helper(app(("d1", True)), 3)
    assert c.state()["offer"] is None
    # Switch from Sources.
    assert switches(c.use("helper:d1", 4)) == [(BT, "helper:d1")]
    assert c.state()["active"] == {"kind": "helper", "id": "helper:d1", "name": "Shawn's iPhone", "connected": True}


def test_when_the_phone_leaves_another_takes_over_after_the_grace():
    c = started()
    c.helper(app(("d1", True)), 1)
    c.bluetooth(bt(), 2)
    assert c.state()["active"]["id"] == "helper:d1"
    c.helper(app(("d1", False)), 10)                  # left the house
    assert c.state()["active"]["connected"] is False and c.state()["offer"]["id"] == BT   # still offered
    assert c.tick(10 + GRACE_S - 1) == []
    assert switches(c.tick(10 + GRACE_S)) == [("helper:d1", BT)]
    assert c.state()["offer"] is None


def test_a_phone_back_within_the_grace_keeps_it():
    c = started()
    c.bluetooth(bt(), 1)
    c.helper(app(("d1", True)), 2)
    c.bluetooth(bt(connected=False), 5)
    c.bluetooth(bt(), 20)                            # a moment out of range
    assert c.tick(5 + GRACE_S + 1) == [] and c.state()["active"]["id"] == BT


def test_a_restart_keeps_the_choice_and_asks_nothing():
    c = PhoneChoice()
    c.bluetooth(bt(), 0)
    c.helper(app(("d1", True)), 0)
    c.restore({"active": {"kind": "helper", "id": "helper:d1", "name": "Shawn's iPhone"}}, 0)
    actions = c.settled(1)
    assert switches(actions) == [] and c.state()["active"]["id"] == "helper:d1"
    assert c.state()["offer"] is None


def test_switching_to_a_bluetooth_phone_not_here_connects_it():
    c = started()
    c.bluetooth_phones([{"mac": BT2, "name": "Old iPhone"}])
    c.helper(app(("d1", True)), 1)
    actions = c.use(BT2, 2)
    assert ("connect_bluetooth", BT2) in actions and c.state()["active"]["connected"] is False
    # Connected: still the one, no offer for itself.
    c.bluetooth(bt(BT2, name="Old iPhone"), 3)
    assert c.state()["active"]["connected"] and c.state()["offer"] is None


def test_forgetting_the_phone_that_has_it_hands_it_on():
    c = started()
    c.helper(app(("d1", True)), 1)
    c.bluetooth(bt(), 2)
    assert switches(c.helper(app(), 3)) == [("helper:d1", BT)]   # d1 unpaired
    assert switches(c.forgotten(BT, 4)) == [(BT, None)] and c.state()["active"] is None


def test_a_rename_reaches_the_badge():
    c = started()
    c.helper(app(("d1", True)), 1)
    c.helper(app(("d1", True, "Bar phone")), 2)
    assert c.state()["active"]["name"] == "Bar phone"
