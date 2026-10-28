"""Minimal Linux hidraw access with no third-party dependencies.

Devices are found through sysfs (/sys/class/hidraw/*/device/uevent), which
gives the bus/vendor/product of each hidraw node, and then written to
directly through /dev/hidrawN. Writes and reads use the same layout as
hidapi on Linux: the first byte is the report ID.
"""

from __future__ import annotations

import errno
import os
import select
from dataclasses import dataclass
from pathlib import Path

SYSFS_HIDRAW = Path("/sys/class/hidraw")


class HidPermissionError(PermissionError):
    """Raised when a device node exists but the user cannot open it."""


@dataclass(frozen=True)
class HidInfo:
    path: str               # /dev/hidrawN
    vendor_id: int
    product_id: int
    name: str
    phys: str
    uniq: str
    interface: int          # USB interface number, -1 if unknown
    report_descriptor: bytes = b""

    @property
    def key(self) -> str:
        """A stable-ish identifier for this device across reboots."""
        loc = self.uniq or self.phys or self.path
        return f"{self.vendor_id:04x}:{self.product_id:04x}:{loc}"


def _parse_uevent(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _interface_number(dev_dir: Path) -> int:
    # .../hidrawN/device -> the HID device; its parent is the USB interface,
    # which has a bInterfaceNumber attribute.
    try:
        real = dev_dir.resolve()
        for parent in [real.parent, real.parent.parent]:
            f = parent / "bInterfaceNumber"
            if f.exists():
                return int(f.read_text().strip(), 16)
    except (OSError, ValueError):
        pass
    return -1


def enumerate_hid(vendor_id: int | None = None, product_ids: set[int] | None = None,
                  sysfs_root: Path = SYSFS_HIDRAW) -> list[HidInfo]:
    found: list[HidInfo] = []
    if not sysfs_root.exists():
        return found
    for node in sorted(sysfs_root.iterdir()):
        dev_dir = node / "device"
        try:
            ue = _parse_uevent((dev_dir / "uevent").read_text())
        except OSError:
            continue
        hid_id = ue.get("HID_ID", "")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        try:
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
        except ValueError:
            continue
        if vendor_id is not None and vid != vendor_id:
            continue
        if product_ids is not None and pid not in product_ids:
            continue
        try:
            rdesc = (dev_dir / "report_descriptor").read_bytes()
        except OSError:
            rdesc = b""
        found.append(HidInfo(
            path=f"/dev/{node.name}",
            vendor_id=vid,
            product_id=pid,
            name=ue.get("HID_NAME", ""),
            phys=ue.get("HID_PHYS", ""),
            uniq=ue.get("HID_UNIQ", ""),
            interface=_interface_number(dev_dir),
            report_descriptor=rdesc,
        ))
    return found


def descriptor_has_report_id(rdesc: bytes, report_id: int) -> bool:
    """Return True if the HID report descriptor declares the given report ID.

    Walks the short items of the descriptor looking for Report ID (0x85).
    """
    i = 0
    n = len(rdesc)
    while i < n:
        prefix = rdesc[i]
        if prefix == 0xFE:  # long item
            if i + 1 >= n:
                break
            i += 3 + rdesc[i + 1]
            continue
        size = prefix & 0x03
        size = 4 if size == 3 else size
        tag = prefix & 0xFC
        if tag == 0x84 and size >= 1 and i + 1 < n:
            if rdesc[i + 1] == report_id:
                return True
        i += 1 + size
    return False


class HidrawDevice:
    def __init__(self, info: HidInfo):
        self.info = info
        self._fd: int | None = None

    def open(self) -> None:
        try:
            self._fd = os.open(self.info.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError as e:
            raise HidPermissionError(
                f"No permission to open {self.info.path}. The FeynosRGB udev rule "
                "grants access to your login session; if you just installed the "
                "package, run 'sudo udevadm trigger' or reboot."
            ) from e

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def write(self, data: bytes | bytearray | list[int]) -> int:
        if self._fd is None:
            raise OSError(errno.EBADF, "device not open")
        buf = bytes(data)
        # The fd is non-blocking for reads; a write can briefly return EAGAIN
        # if the device queue is full, so wait until it is writable.
        for _ in range(50):
            try:
                return os.write(self._fd, buf)
            except BlockingIOError:
                select.select([], [self._fd], [], 0.02)
        raise TimeoutError(f"write to {self.info.path} timed out")

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if self._fd is None:
            raise OSError(errno.EBADF, "device not open")
        r, _, _ = select.select([self._fd], [], [], timeout)
        if not r:
            return b""
        try:
            return os.read(self._fd, size)
        except BlockingIOError:
            return b""

    def drain(self) -> None:
        """Discard any input reports already queued."""
        if self._fd is None:
            return
        for _ in range(64):
            try:
                if not os.read(self._fd, 256):
                    break
            except (BlockingIOError, OSError):
                break

    def __enter__(self) -> "HidrawDevice":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
