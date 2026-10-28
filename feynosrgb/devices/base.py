"""Device abstraction shared by every RGB driver.

To add support for new hardware, subclass RGBDevice, implement the abstract
methods, and register a detector function in devices/__init__.py.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable

from ..color import RGB


@dataclass
class Zone:
    id: str                      # unique within the device, e.g. "mainboard", "argb1"
    name: str                    # shown to the user
    led_count: int
    resizable: bool = False      # True for ARGB headers where the user sets the count
    min_leds: int = 0
    max_leds: int = 0
    kind: str = "linear"         # "linear" | "single" | "matrix"


@dataclass
class HardwareMode:
    """An effect the controller runs by itself (survives the app closing)."""
    id: str
    name: str
    value: int
    takes_color: bool


@dataclass
class DeviceInfo:
    vendor: str
    model: str
    category: str                # "motherboard", "keyboard", "mouse", "ram", "gpu", "cooler", "virtual"
    location: str
    firmware: str = ""
    serial: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class RGBDevice(abc.ABC):
    """Base class for all devices. Methods are called from the engine thread."""

    #: Stable identifier used in profiles and settings.
    uid: str
    info: DeviceInfo
    zones: list[Zone]

    @property
    def name(self) -> str:
        return f"{self.info.vendor} {self.info.model}".strip()

    # ---- lifecycle -------------------------------------------------------
    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    # ---- software ("direct") control ------------------------------------
    @abc.abstractmethod
    def enter_direct_mode(self) -> None:
        """Prepare the controller to accept per-LED updates."""

    @abc.abstractmethod
    def set_zone_colors(self, zone_id: str, colors: list[RGB]) -> None:
        """Send one frame of colors for one zone (len == zone.led_count)."""

    def flush(self) -> None:
        """Called once per frame after all zones were set."""

    # ---- hardware (onboard) effects --------------------------------------
    def hardware_modes(self) -> list[HardwareMode]:
        return []

    def set_hardware_mode(self, mode_id: str, color: RGB) -> None:
        raise NotImplementedError

    def save_to_device(self, mode_id: str, color: RGB) -> None:
        """Persist an onboard effect so it is used at boot and after shutdown."""
        raise NotImplementedError

    @property
    def supports_save(self) -> bool:
        return False

    # ---- zone configuration ---------------------------------------------
    def resize_zone(self, zone_id: str, led_count: int) -> None:
        for z in self.zones:
            if z.id == zone_id and z.resizable:
                z.led_count = max(z.min_leds, min(z.max_leds, int(led_count)))
                return

    def zone(self, zone_id: str) -> Zone | None:
        for z in self.zones:
            if z.id == zone_id:
                return z
        return None

    def zone_key(self, zone: Zone) -> str:
        return f"{self.uid}/{zone.id}"


#: A detector receives the settings dict and returns devices it found.
Detector = Callable[[dict], list[RGBDevice]]
