import pytest

from piratebox.fl import wire
from piratebox.fl.simulator import FakeFL, load_script


def test_u21_roundtrip():
    for v in (0, 1, 127, 128, 142000, 522000, wire.U21_MAX):
        assert wire.from_u21(wire.u21(v)) == v
    with pytest.raises(wire.WireError):
        wire.u21(wire.U21_MAX + 1)


def test_encode_decode():
    data = wire.encode(wire.MT_REQUEST, 5, wire.OP_SET_TEMPO, wire.u21(142000))
    assert all(0 <= b <= 127 for b in data)
    f = wire.decode([0xF0, *data, 0xF7])
    assert (f.msg_type, f.seq, f.op, wire.from_u21(f.payload)) == (wire.MT_REQUEST, 5, wire.OP_SET_TEMPO, 142000)


def test_foreign_sysex_rejected():
    with pytest.raises(wire.WireError):
        wire.decode([0x43, 0x10, 0x4C, 0, 0, 0x7E, 0])  # a Yamaha message


def test_fl_script_constants_match_bridge():
    """The FL script can't import the bridge, so its copy of the protocol must be checked here."""
    script = load_script(FakeFL())
    names = [n for n in dir(wire) if n.split("_")[0] in ("MT", "OP", "EV", "ST", "FLAG")]
    names += ["PROTOCOL_VERSION", "MANUFACTURER_ID", "MAGIC"]
    for name in names:
        assert getattr(script, name) == getattr(wire, name), name
