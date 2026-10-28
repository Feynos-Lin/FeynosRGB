import math
import re
import struct
import time
from pathlib import Path

import pytest

from feynosrgb import color
from feynosrgb.config import EffectSpec, Profile, ProfileStore, Settings
from feynosrgb.devices import SUPPORTED_USB_IDS, VirtualDevice
from feynosrgb.devices import asus_aura_usb as aura
from feynosrgb.effects import EFFECTS, AudioFrame, FrameContext
from feynosrgb.hidraw import descriptor_has_report_id, enumerate_hid

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ color
def test_color_helpers():
    assert color.parse_hex("#ff8000") == (255, 128, 0)
    assert color.parse_hex("0f0") == (0, 255, 0)
    assert color.to_hex((1, 2, 3)) == "#010203"
    assert color.lerp((0, 0, 0), (200, 100, 50), 0.5) == (100, 50, 25)
    assert color.hsv(0) == (255, 0, 0)
    assert color.gradient([(0, 0, 0), (255, 255, 255)], 1.0) == (255, 255, 255)
    assert color.apply_brightness([(255, 255, 255)], 0.0) == [(0, 0, 0)]
    with pytest.raises(ValueError):
        color.parse_hex("#12")


# ---------------------------------------------------------------- effects
@pytest.mark.parametrize("effect", EFFECTS, ids=lambda e: e.id)
@pytest.mark.parametrize("n", [0, 1, 2, 8, 30, 120])
def test_effects_render_valid_frames(effect, n):
    params = effect.defaults()
    state: dict = {}
    audio = AudioFrame(bands=[0.5] * 16, level=0.7, bass=0.9, beat=True)
    for i in range(20):
        ctx = FrameContext(t=i / 30, dt=1 / 30, audio=audio, temps={"auto:cpu": 65.0})
        out = effect.render(params, n, ctx, state)
        assert len(out) == n
        for c in out:
            assert len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c)


def test_effect_params_coerce_bad_values():
    for e in EFFECTS:
        junk = {p.id: "not-a-valid-value" for p in e.params}
        norm = e.normalize(junk)
        assert set(norm) == {p.id for p in e.params}


def test_static_effect_color():
    e = next(e for e in EFFECTS if e.id == "static")
    out = e.render({"color": "#123456"}, 4, FrameContext(0, 0), {})
    assert out == [(0x12, 0x34, 0x56)] * 4


# ----------------------------------------------------------- aura protocol
def test_direct_packets_match_openrgb_layout():
    colors = [(i, 255 - i, 7) for i in range(45)]
    pkts = aura.build_direct_packets(1, colors)
    assert len(pkts) == 3
    assert all(len(p) == 65 and p[0] == 0xEC and p[1] == 0x40 for p in pkts)
    # first two packets: 20 LEDs each, no apply flag
    assert (pkts[0][2], pkts[0][3], pkts[0][4]) == (0x01, 0, 20)
    assert (pkts[1][2], pkts[1][3], pkts[1][4]) == (0x01, 20, 20)
    # last packet applies the frame
    assert (pkts[2][2], pkts[2][3], pkts[2][4]) == (0x81, 40, 5)
    assert tuple(pkts[1][5:8]) == colors[20]
    assert tuple(pkts[2][5 + 3 * 4: 8 + 3 * 4]) == colors[44]


def test_direct_single_packet_applies():
    pkts = aura.build_direct_packets(0x04, [(1, 2, 3)] * 8)
    assert len(pkts) == 1 and pkts[0][2] == 0x84 and pkts[0][4] == 8


def test_effect_packets():
    assert aura.build_effect_packet(2, 0xFF)[:6] == bytes([0xEC, 0x35, 2, 0, 0, 0xFF])
    assert aura.build_effect_packet(0, 1, shutdown=True)[4] == 1
    p = aura.build_effect_color_packet(1, 3, (9, 8, 7))
    mask = 0b1110
    assert p[1] == 0x36 and p[2] == mask >> 8 and p[3] == mask & 0xFF
    assert tuple(p[5 + 3:5 + 6]) == (9, 8, 7)
    assert tuple(p[5:8]) == (0, 0, 0)


def _config(argb=3, mb_leds=8, rgb_headers=2):
    cfg = bytearray(60)
    cfg[0x02] = argb
    cfg[0x1B] = mb_leds
    cfg[0x1D] = rgb_headers
    return bytes(cfg)


def test_parse_channels_b550f_ar32_numbering():
    ch, zones = aura.parse_channels("AULA3-AR32-0207", _config(), {"argb2": 45})
    # AR32: ARGB effect channels start at 0, board comes after them
    assert [(c.zone_id, c.effect_channel, c.direct_channel) for c in ch] == [
        ("mainboard", 3, 4), ("argb1", 0, 0), ("argb2", 1, 1), ("argb3", 2, 2)]
    assert [z.led_count for z in zones] == [8, aura.ARGB_DEFAULT_LEDS, 45, aura.ARGB_DEFAULT_LEDS]
    assert zones[1].resizable and not zones[0].resizable


def test_parse_channels_other_firmware():
    ch, _ = aura.parse_channels("AUMA0-E6K5-0106", _config(argb=2), {})
    assert [(c.zone_id, c.effect_channel) for c in ch] == [("mainboard", 0), ("argb1", 1), ("argb2", 2)]


class FakeHid:
    """Stands in for HidrawDevice and answers like an AULA3 controller."""
    def __init__(self, info=None):
        self.writes = []
        self._replies = []

    def open(self):
        pass

    def close(self):
        pass

    def drain(self):
        self._replies.clear()

    def write(self, data):
        data = bytes(data)
        assert len(data) == 65 and data[0] == 0xEC
        self.writes.append(data)
        if data[1] == 0x82:
            self._replies.append(bytes([0xEC, 0x02]) + b"AULA3-AR32-0207\x00" + bytes(47))
        elif data[1] == 0xB0:
            self._replies.append(bytes([0xEC, 0x30, 0, 0]) + _config() + bytes(1))
        return len(data)

    def read(self, size, timeout=0.5):
        return self._replies.pop(0) if self._replies else b""


def _fake_board(monkeypatch):
    monkeypatch.setattr(aura, "HidrawDevice", FakeHid)
    from feynosrgb.hidraw import HidInfo
    info = HidInfo("/dev/hidraw9", 0x0B05, 0x18F3, "AsusTek", "usb-1", "", 2)
    dev = aura.AsusAuraUSBMainboard(info, "ROG STRIX B550-F GAMING", {"argb1": 10})
    dev.open()
    return dev


def test_board_open_direct_and_save(monkeypatch):
    dev = _fake_board(monkeypatch)
    assert dev.info.firmware == "AULA3-AR32-0207"
    assert [z.id for z in dev.zones] == ["mainboard", "argb1", "argb2", "argb3"]
    w = dev._dev.writes
    assert w[-1][:5] == bytes([0xEC, 0x52, 0x53, 0x00, 0x01])
    w.clear()
    dev.set_zone_colors("argb1", [(255, 0, 0)] * 10)
    # entering direct mode sends 0x35 ... 0xFF for every channel, then the frame
    assert sum(1 for p in w if p[1] == 0x35 and p[5] == 0xFF) == 4
    assert w[-1][1] == 0x40 and w[-1][2] == 0x80 and w[-1][4] == 10
    w.clear()
    dev.save_to_device("static", (0, 255, 0))
    assert w[-1][:3] == bytes([0xEC, 0x3F, 0x55])
    shutdown = [p for p in w if p[1] == 0x35 and p[4] == 1]
    assert len(shutdown) == 1 and shutdown[0][2] == 3  # onboard channel only


def test_report_descriptor_scan():
    # Usage Page (vendor 0xFF72), Usage, Collection, Report ID 0xEC, ...
    rdesc = bytes([0x06, 0x72, 0xFF, 0x09, 0xA1, 0xA1, 0x01, 0x85, 0xEC, 0x09, 0x10, 0xC0])
    assert descriptor_has_report_id(rdesc, 0xEC)
    assert not descriptor_has_report_id(rdesc, 0x01)


def test_enumerate_fake_sysfs(tmp_path):
    node = tmp_path / "hidraw3" / "device"
    node.mkdir(parents=True)
    (node / "uevent").write_text("HID_ID=0003:00000B05:000018F3\nHID_NAME=AsusTek Computer Inc. AURA LED Controller\nHID_PHYS=usb-0000:02:00.0-8/input2\n")
    (node / "report_descriptor").write_bytes(bytes([0x85, 0xEC]))
    other = tmp_path / "hidraw1" / "device"
    other.mkdir(parents=True)
    (other / "uevent").write_text("HID_ID=0003:0000046D:0000C52B\n")
    found = enumerate_hid(0x0B05, {0x18F3}, sysfs_root=tmp_path)
    assert len(found) == 1 and found[0].path == "/dev/hidraw3" and found[0].product_id == 0x18F3


def test_udev_rule_covers_every_supported_device():
    rules = (ROOT / "data" / "60-feynosrgb.rules").read_text()
    pairs = set(re.findall(r'idVendor}=="([0-9a-f]{4})", ATTRS{idProduct}=="([0-9a-f]{4})"', rules))
    for vid, pid in SUPPORTED_USB_IDS:
        assert (f"{vid:04x}", f"{pid:04x}") in pairs


# --------------------------------------------------------------- profiles
def test_profile_roundtrip_and_store(tmp_path):
    store = ProfileStore(tmp_path)
    assert "Default" in store.names()  # built-ins created on first run
    p = Profile(name="Night/Mode", brightness=0.4, mode="onboard",
                zones={"*": EffectSpec("comet", {"speed": 99}), "dev/argb1": EffectSpec("static", {"color": "#00ff00"})},
                disabled=["dev/argb2"])
    store.save(p)
    again = ProfileStore(tmp_path).get("Night/Mode")
    assert again.brightness == 0.4 and again.mode == "onboard"
    assert again.zones["*"].params["speed"] == 5.0          # clamped
    assert again.spec_for("dev/argb1").effect == "static"
    assert again.spec_for("dev/other").effect == "comet"
    store.rename("Night/Mode", "Night")
    assert "Night" in store.names() and "Night/Mode" not in store.names()


def test_settings_persist(tmp_path):
    s = Settings(tmp_path / "settings.json")
    s.set_zone_size("dev", "argb1", 24)
    s["fps"] = 45
    s.save()
    s2 = Settings(tmp_path / "settings.json")
    assert s2["zone_sizes"]["dev"]["argb1"] == 24 and s2["fps"] == 45


# ----------------------------------------------------------------- engine
def test_engine_renders_to_virtual_device(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("feynosrgb.devices.DETECTORS", [])
    from feynosrgb.engine import Engine
    eng = Engine(Settings(tmp_path / "s.json"), demo=True)
    eng.scan()
    dev = eng.devices()[0]
    assert isinstance(dev, VirtualDevice)
    eng.set_profile(Profile(zones={"*": EffectSpec("rainbow_wave")}, disabled=[f"{dev.uid}/argb3"]))
    assert eng._frame(1 / 30) is True
    assert len(dev.frames["argb1"]) == 30
    assert "argb3" not in dev.frames or set(dev.frames["argb3"]) == {(0, 0, 0)}
    eng.set_power(False)
    eng._frame(1 / 30)
    assert set(dev.frames["argb1"]) == {(0, 0, 0)}
    eng.set_power(True)
    eng.set_profile(Profile(mode="onboard", onboard_mode="breathing", onboard_color="#ff0000"))
    assert eng._frame(1 / 30) is False
    assert dev.hw_mode == ("breathing", (255, 0, 0))
    assert eng.save_onboard_to_devices() == []
    assert dev.saved == ("breathing", (255, 0, 0))
    eng.resize_zone(dev.uid, "argb1", 12)
    assert dev.zone("argb1").led_count == 12


def test_engine_thread_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("feynosrgb.devices.DETECTORS", [])
    from feynosrgb.engine import Engine
    eng = Engine(Settings(tmp_path / "s.json"), demo=True)
    eng.scan()
    eng.set_profile(Profile(zones={"*": EffectSpec("comet")}))
    eng.start()
    time.sleep(0.5)
    eng.stop(restore="off")
    dev = eng._states[0].device
    assert set(dev.frames["argb1"]) == {(0, 0, 0)}


# ------------------------------------------------------------------ audio
def test_analyzer_finds_bass_tone():
    pytest.importorskip("numpy")
    from feynosrgb.audio import CHUNK, RATE, Analyzer
    an = Analyzer()
    tone = b"".join(struct.pack("<h", int(20000 * math.sin(2 * math.pi * 60 * i / RATE))) for i in range(CHUNK))
    for _ in range(5):
        f = an.process(tone)
    assert f.level > 0.5
    assert f.bands[0] + f.bands[1] + f.bands[2] > f.bands[-1] + f.bands[-2] + f.bands[-3]
    silent = an.process(bytes(CHUNK * 2))
    assert silent.level == 0.0 and max(silent.bands) == 0.0


# -------------------------------------------------------------------- ipc
def test_ipc_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    from feynosrgb import ipc
    srv = ipc.Server(lambda c: {"ok": True, "echo": c.get("cmd")})
    assert srv.start()
    try:
        assert ipc.send({"cmd": "ping"}) == {"ok": True, "echo": "ping"}
        assert ipc.Server(lambda c: {}).start() is False   # single instance
    finally:
        srv.stop()
    assert ipc.send({"cmd": "ping"}) is None


def test_service_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("feynosrgb.devices.DETECTORS", [])
    from feynosrgb.service import Service
    svc = Service(demo=True)
    svc.engine.scan()
    assert svc.handle({"cmd": "apply", "profile": "Music"})["ok"]
    assert svc.engine.profile.zones["*"].effect == "audio_spectrum"
    assert not svc.handle({"cmd": "apply", "profile": "nope"})["ok"]
    assert svc.handle({"cmd": "toggle"})["power"] is False
    st = svc.handle({"cmd": "status"})
    assert st["devices"][0]["zones"][0]["id"] == "mainboard"
    assert svc.handle({"cmd": "next"})["ok"]
