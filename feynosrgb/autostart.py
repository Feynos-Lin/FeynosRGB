"""Start-at-login via an XDG autostart entry (works on GNOME, KDE and others)."""

from __future__ import annotations

import os
from pathlib import Path

ENTRY = """[Desktop Entry]
Type=Application
Name=FeynosRGB
Comment=Apply your RGB lighting profile at login
Exec=feynosrgb --minimized
Icon=feynosrgb
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
X-KDE-autostart-after=panel
"""


def autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / "feynosrgb.desktop"


def is_enabled() -> bool:
    return autostart_file().exists()


def set_enabled(enabled: bool) -> None:
    f = autostart_file()
    if enabled:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(ENTRY)
    else:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
