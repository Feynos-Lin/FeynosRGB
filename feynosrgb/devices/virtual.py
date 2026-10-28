"""A virtual device for demo mode and tests. It only records what it is sent."""

from __future__ import annotations

from ..color import RGB
from .base import DeviceInfo, HardwareMode, RGBDevice, Zone


class VirtualDevice(RGBDevice):
    def __init__(self, uid: str = "virtual-demo", zone_sizes: dict[str, int] | None = None):
        self.uid = uid
        self.info = DeviceInfo(vendor="FeynosRGB", model="Virtual demo device",
                               category="virtual", location="memory", firmware="demo")
        sizes = zone_sizes or {}
        self.zones = [
            Zone("mainboard", "Onboard lighting", 8),
            Zone("argb1", "ARGB header 1", int(sizes.get("argb1", 30)), resizable=True, max_leds=120),
            Zone("argb2", "ARGB header 2", int(sizes.get("argb2", 30)), resizable=True, max_leds=120),
            Zone("argb3", "ARGB header 3", int(sizes.get("argb3", 30)), resizable=True, max_leds=120),
        ]
        self.frames: dict[str, list[RGB]] = {}
        self.hw_mode: tuple[str, RGB] | None = None
        self.saved: tuple[str, RGB] | None = None
        self.direct = False
        self.is_open = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def enter_direct_mode(self) -> None:
        self.direct = True

    def set_zone_colors(self, zone_id: str, colors: list[RGB]) -> None:
        if not self.direct:
            self.enter_direct_mode()
        self.frames[zone_id] = list(colors)

    def hardware_modes(self) -> list[HardwareMode]:
        return [HardwareMode("off", "Off", 0, False), HardwareMode("static", "Static", 1, True),
                HardwareMode("breathing", "Breathing", 2, True), HardwareMode("rainbow", "Rainbow", 5, False)]

    def set_hardware_mode(self, mode_id: str, color: RGB) -> None:
        self.direct = False
        self.hw_mode = (mode_id, color)

    @property
    def supports_save(self) -> bool:
        return True

    def save_to_device(self, mode_id: str, color: RGB) -> None:
        self.set_hardware_mode(mode_id, color)
        self.saved = (mode_id, color)
