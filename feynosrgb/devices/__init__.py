"""Device registry. Add new drivers by appending their detect() to DETECTORS."""

from __future__ import annotations

import logging

from . import asus_aura_usb
from .base import Detector, DeviceInfo, HardwareMode, RGBDevice, Zone
from .virtual import VirtualDevice

log = logging.getLogger(__name__)

DETECTORS: list[tuple[str, Detector]] = [
    ("ASUS Aura USB mainboard", asus_aura_usb.detect),
]

#: USB vendor/product pairs the udev rule must cover (kept in sync by a test).
SUPPORTED_USB_IDS: list[tuple[int, int]] = [
    (asus_aura_usb.VID_ASUS, pid) for pid in sorted(asus_aura_usb.MAINBOARD_PIDS)
]


def detect_all(settings: dict, demo: bool = False) -> tuple[list[RGBDevice], list[str]]:
    """Return (devices, error messages). Devices are not yet opened."""
    devices: list[RGBDevice] = []
    errors: list[str] = []
    for label, detector in DETECTORS:
        try:
            devices.extend(detector(settings))
        except Exception as e:  # a broken driver must not take the app down
            log.exception("detector %s failed", label)
            errors.append(f"{label}: {e}")
    if demo or settings.get("demo_device"):
        sizes = settings.get("zone_sizes", {}).get("virtual-demo", {})
        devices.append(VirtualDevice(zone_sizes=sizes))
    return devices, errors


__all__ = ["DETECTORS", "SUPPORTED_USB_IDS", "detect_all", "RGBDevice", "Zone",
           "HardwareMode", "DeviceInfo", "VirtualDevice"]
