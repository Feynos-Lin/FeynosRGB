"""ASUS Aura USB mainboard controller (AULA3 / AUMA0 family).

Covers ASUS boards from the X570/B550 generation onward whose onboard
lighting and ARGB/RGB headers are driven by an internal USB HID controller
with vendor ID 0x0B05, e.g. the ROG Strix B550-F Gaming (PID 0x18F3,
firmware AULA3-AR32).

Protocol notes (all packets are 65 bytes, byte 0 is HID report ID 0xEC):

  EC 82                      -> reply EC 02 <16-byte firmware string>
  EC B0                      -> reply EC 30 ?? ?? <60-byte config table>
  EC 52 53 00 01             "gen1" init used by the vendor software
  EC 35 <ch> 00 <sd> <mode>  set effect <mode> on effect channel <ch>
                             (mode 0xFF = direct/software control,
                             sd = 1 sets the shutdown effect)
  EC 36 <maskH> <maskL> <sd> <rgb...>
                             set effect color for LEDs selected by mask
  EC 3F 55                   commit (save to controller flash)
  EC 40 <ch|flags> <off> <n> <rgb * n>
                             direct colors, 20 LEDs per packet; flag 0x80
                             applies the frame, 0x10 marks offset > 255

Protocol knowledge comes from the OpenRGB project (Martin Hartl and
contributors) and liquidctl's aura_led driver (CaseySJ and contributors).
This file is an independent Python implementation.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from ..color import RGB
from ..hidraw import HidInfo, HidrawDevice, descriptor_has_report_id, enumerate_hid
from .base import DeviceInfo, HardwareMode, RGBDevice, Zone

log = logging.getLogger(__name__)

VID_ASUS = 0x0B05

# Mainboard-style controllers (same protocol).
MAINBOARD_PIDS = {
    0x18F3: "AURA Motherboard (gen 1)",   # B550-F Gaming, X570, B550 family
    0x1939: "AURA Motherboard (gen 2)",
    0x19AF: "AURA Motherboard (gen 3)",
    0x1AA6: "AURA Motherboard (gen 4)",
    0x1BED: "AURA Motherboard (gen 5)",
}

REPORT_ID = 0xEC
PACKET_LEN = 65
LEDS_PER_PACKET = 20
ARGB_MAX_LEDS = 120
ARGB_DEFAULT_LEDS = 30

CMD_FIRMWARE = 0x82
CMD_CONFIG = 0xB0
CMD_EFFECT = 0x35
CMD_EFFECT_COLOR = 0x36
CMD_COMMIT = 0x3F
CMD_DIRECT = 0x40

DIRECT_APPLY = 0x80
DIRECT_OVERFLOW = 0x10

MODE_DIRECT = 0xFF

HW_MODES = [
    HardwareMode("off", "Off", 0x00, False),
    HardwareMode("static", "Static", 0x01, True),
    HardwareMode("breathing", "Breathing", 0x02, True),
    HardwareMode("flashing", "Flashing", 0x03, True),
    HardwareMode("spectrum_cycle", "Spectrum cycle", 0x04, False),
    HardwareMode("rainbow", "Rainbow", 0x05, False),
    HardwareMode("spectrum_breathing", "Spectrum breathing", 0x06, False),
    HardwareMode("chase_fade", "Chase fade", 0x07, True),
    HardwareMode("spectrum_chase_fade", "Spectrum chase fade", 0x08, False),
    HardwareMode("chase", "Chase", 0x09, True),
    HardwareMode("spectrum_chase", "Spectrum chase", 0x0A, False),
    HardwareMode("spectrum_wave", "Spectrum wave", 0x0B, False),
    HardwareMode("chase_rainbow_pulse", "Chase rainbow pulse", 0x0C, False),
    HardwareMode("random_flicker", "Random flicker", 0x0D, False),
    HardwareMode("gentle_transition", "Gentle transition", 0x10, False),
    HardwareMode("wave_propagation", "Wave propagation", 0x11, False),
    HardwareMode("wave_propagation_pause", "Wave propagation (pause)", 0x12, False),
    HardwareMode("red_pulse", "Red pulse", 0x13, False),
]
_HW_BY_ID = {m.id: m for m in HW_MODES}


@dataclass
class _Channel:
    zone_id: str
    effect_channel: int
    direct_channel: int
    num_leds: int        # LEDs reported by the controller (1 for ARGB headers)
    fixed: bool          # True = onboard LEDs + 12V RGB headers


class AuraProtocolError(RuntimeError):
    pass


def _packet(*payload: int) -> bytes:
    buf = bytearray(PACKET_LEN)
    buf[0] = REPORT_ID
    for i, b in enumerate(payload, start=1):
        buf[i] = b & 0xFF
    return bytes(buf)


def build_effect_packet(effect_channel: int, mode: int, shutdown: bool = False) -> bytes:
    return _packet(CMD_EFFECT, effect_channel, 0x00, 0x01 if shutdown else 0x00, mode)


def build_effect_color_packet(start_led: int, led_count: int, color: RGB, shutdown: bool = False) -> bytes:
    mask = ((1 << led_count) - 1) << start_led
    buf = bytearray(_packet(CMD_EFFECT_COLOR, (mask >> 8) & 0xFF, mask & 0xFF, 0x01 if shutdown else 0x00))
    for i in range(led_count):
        o = 5 + 3 * (start_led + i)
        if o + 2 < PACKET_LEN:
            buf[o:o + 3] = bytes(color)
    return bytes(buf)


def build_direct_packets(direct_channel: int, colors: list[RGB]) -> list[bytes]:
    """Split one channel's colors into 20-LED direct packets (mirrors OpenRGB)."""
    packets: list[bytes] = []
    led_count = len(colors)
    command = direct_channel
    offset = 0
    configured = 0
    in_packet = LEDS_PER_PACKET
    while configured < led_count:
        if configured + in_packet < led_count:
            configured += in_packet
        else:
            command |= DIRECT_APPLY
            in_packet = led_count - configured
            configured = led_count
        buf = bytearray(PACKET_LEN)
        buf[0] = REPORT_ID
        buf[1] = CMD_DIRECT
        buf[2] = command & 0xFF
        buf[3] = offset & 0xFF
        buf[4] = in_packet
        first = configured - in_packet
        for i in range(in_packet):
            r, g, b = colors[first + i]
            buf[5 + 3 * i] = r
            buf[6 + 3 * i] = g
            buf[7 + 3 * i] = b
        packets.append(bytes(buf))
        offset += in_packet
        if configured > 255:
            command |= DIRECT_OVERFLOW
            offset = configured - 256
    return packets


def parse_channels(firmware: str, config: bytes, argb_counts: dict[str, int]) -> tuple[list[_Channel], list[Zone]]:
    """Turn the controller's 60-byte config table into channels and zones."""
    if len(config) < 0x1E:
        raise AuraProtocolError("config table too short")
    total_mb_leds = config[0x1B]
    rgb_headers = config[0x1D]
    argb_headers = config[0x02]
    if total_mb_leds < rgb_headers:
        rgb_headers = 0

    channels: list[_Channel] = []
    zones: list[Zone] = []
    effect_channel = 0
    # AULA3-AR32 numbers ARGB effect channels from 0 and puts the board after them.
    argb_from_zero = firmware.startswith("AULA3-AR32")

    if total_mb_leds > 0:
        mb_effect = argb_headers if argb_from_zero else effect_channel
        channels.append(_Channel("mainboard", mb_effect, 0x04, total_mb_leds, True))
        name = "Onboard lighting"
        if rgb_headers:
            name += f" + {rgb_headers} RGB header" + ("s" if rgb_headers > 1 else "")
        zones.append(Zone("mainboard", name, total_mb_leds, kind="linear"))
        if not argb_from_zero:
            effect_channel += 1

    for i in range(argb_headers):
        zid = f"argb{i + 1}"
        channels.append(_Channel(zid, effect_channel, i, 1, False))
        count = int(argb_counts.get(zid, ARGB_DEFAULT_LEDS))
        zones.append(Zone(zid, f"ARGB header {i + 1}", max(0, min(ARGB_MAX_LEDS, count)),
                          resizable=True, min_leds=0, max_leds=ARGB_MAX_LEDS))
        effect_channel += 1
    return channels, zones


class AsusAuraUSBMainboard(RGBDevice):
    def __init__(self, hid: HidInfo, board_name: str, argb_counts: dict[str, int] | None = None):
        self.hid_info = hid
        self.uid = f"asus-aura-usb-{hid.product_id:04x}"
        self.info = DeviceInfo(
            vendor="ASUS",
            model=board_name or MAINBOARD_PIDS.get(hid.product_id, "Aura USB"),
            category="motherboard",
            location=hid.path,
        )
        self._argb_counts = dict(argb_counts or {})
        self._dev = HidrawDevice(hid)
        self._channels: list[_Channel] = []
        self.zones: list[Zone] = []
        self._lock = threading.Lock()
        self._in_direct = False
        self._last_direct_init = 0.0

    # ---- lifecycle -------------------------------------------------------
    def open(self) -> None:
        self._dev.open()
        try:
            firmware = self._request_firmware()
            config = self._request_config()
            self.info.firmware = firmware
            self._channels, self.zones = parse_channels(firmware, config, self._argb_counts)
            self.info.extra["Config table"] = config[:30].hex(" ")
            self._write(_packet(0x52, 0x53, 0x00, 0x01))  # gen1 init
        except Exception:
            self._dev.close()
            raise
        log.info("Opened %s (%s), zones=%s", self.name, self.info.firmware,
                 [(z.id, z.led_count) for z in self.zones])

    def close(self) -> None:
        self._dev.close()
        self._in_direct = False

    # ---- low level -------------------------------------------------------
    def _write(self, pkt: bytes) -> None:
        self._dev.write(pkt)

    def _request(self, cmd: int, reply: int) -> bytes:
        for _ in range(3):
            self._dev.drain()
            self._write(_packet(cmd))
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                data = self._dev.read(PACKET_LEN, timeout=0.3)
                if len(data) >= 2 and data[0] == REPORT_ID and data[1] == reply:
                    return data
                # Some kernels strip the report ID on reads.
                if len(data) >= 1 and data[0] == reply:
                    return bytes([REPORT_ID]) + data
        raise AuraProtocolError(f"no reply to command 0x{cmd:02X}")

    def _request_firmware(self) -> str:
        data = self._request(CMD_FIRMWARE, 0x02)
        raw = data[2:18]
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()

    def _request_config(self) -> bytes:
        data = self._request(CMD_CONFIG, 0x30)
        cfg = bytes(data[4:64])
        return cfg.ljust(60, b"\x00")

    def _channel(self, zone_id: str) -> _Channel:
        for c in self._channels:
            if c.zone_id == zone_id:
                return c
        raise KeyError(zone_id)

    # ---- direct mode -----------------------------------------------------
    def enter_direct_mode(self) -> None:
        with self._lock:
            for ch in self._channels:
                self._write(build_effect_packet(ch.effect_channel, MODE_DIRECT))
            self._in_direct = True
            self._last_direct_init = time.monotonic()

    def set_zone_colors(self, zone_id: str, colors: list[RGB]) -> None:
        if not colors:
            return
        # Re-assert direct mode now and then: the controller falls back to
        # its saved effect after suspend/resume.
        if not self._in_direct or time.monotonic() - self._last_direct_init > 5.0:
            self.enter_direct_mode()
        ch = self._channel(zone_id)
        with self._lock:
            for pkt in build_direct_packets(ch.direct_channel, colors):
                self._write(pkt)

    # ---- hardware effects ------------------------------------------------
    def hardware_modes(self) -> list[HardwareMode]:
        return list(HW_MODES)

    @property
    def supports_save(self) -> bool:
        return True

    def _send_mode_all(self, mode: HardwareMode, color: RGB, shutdown: bool) -> None:
        start_led = 0
        for ch in self._channels:
            if shutdown and not ch.fixed:
                start_led += ch.num_leds
                continue  # the shutdown effect only applies to onboard lighting
            self._write(build_effect_packet(ch.effect_channel, mode.value, shutdown))
            c = color if mode.takes_color else (0, 0, 0)
            self._write(build_effect_color_packet(start_led, ch.num_leds, c, shutdown))
            start_led += ch.num_leds

    def set_hardware_mode(self, mode_id: str, color: RGB) -> None:
        mode = _HW_BY_ID[mode_id]
        with self._lock:
            self._in_direct = False
            self._send_mode_all(mode, color, shutdown=False)

    def save_to_device(self, mode_id: str, color: RGB) -> None:
        mode = _HW_BY_ID[mode_id]
        with self._lock:
            self._in_direct = False
            self._send_mode_all(mode, color, shutdown=False)
            self._send_mode_all(mode, color, shutdown=True)
            self._write(_packet(CMD_COMMIT, 0x55))


def _board_name() -> str:
    try:
        vendor = open("/sys/class/dmi/id/board_vendor").read().strip()
        name = open("/sys/class/dmi/id/board_name").read().strip()
        if "ASUS" in vendor.upper():
            return name
        return f"{vendor} {name}".strip()
    except OSError:
        return ""


def detect(settings: dict) -> list[RGBDevice]:
    infos = enumerate_hid(VID_ASUS, set(MAINBOARD_PIDS))
    # A controller may expose several hidraw interfaces; keep the one that
    # declares report ID 0xEC (fall back to the first per product).
    chosen: dict[int, HidInfo] = {}
    for info in infos:
        good = descriptor_has_report_id(info.report_descriptor, REPORT_ID)
        prev = chosen.get(info.product_id)
        if prev is None or (good and not descriptor_has_report_id(prev.report_descriptor, REPORT_ID)):
            chosen[info.product_id] = info
    devices: list[RGBDevice] = []
    board = _board_name()
    for pid, info in chosen.items():
        uid = f"asus-aura-usb-{pid:04x}"
        counts = settings.get("zone_sizes", {}).get(uid, {})
        devices.append(AsusAuraUSBMainboard(info, board, counts))
    return devices
