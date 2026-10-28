"""Glue between engine, profiles and IPC, shared by the GUI and the daemon."""

from __future__ import annotations

import logging
from typing import Callable

from .config import ProfileStore, Settings
from .engine import Engine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, demo: bool = False):
        self.settings = Settings()
        self.store = ProfileStore()
        self.engine = Engine(self.settings, demo=demo)
        self.on_show: Callable[[], None] | None = None
        self.on_profile_changed: Callable[[str], None] | None = None

    def start(self) -> None:
        self.engine.scan()
        name = self.settings.get("active_profile") or "Default"
        profile = self.store.get(name) or self.store.get(self.store.names()[0])
        if profile:
            self.engine.set_profile(profile, restart_time=True)
        self.engine.start()

    def activate(self, name: str) -> bool:
        p = self.store.get(name)
        if p is None:
            return False
        self.engine.set_profile(p, restart_time=True)
        self.settings["active_profile"] = name
        self.settings.save()
        if self.on_profile_changed:
            self.on_profile_changed(name)
        return True

    def cycle(self, step: int = 1) -> str:
        names = self.store.names()
        if not names:
            return ""
        cur = self.settings.get("active_profile")
        i = names.index(cur) if cur in names else -1
        nxt = names[(i + step) % len(names)]
        self.activate(nxt)
        return nxt

    def handle(self, cmd: dict) -> dict:
        c = cmd.get("cmd")
        e = self.engine
        if c == "ping":
            return {"ok": True}
        if c == "apply":
            name = str(cmd.get("profile", ""))
            ok = self.activate(name)
            return {"ok": ok, "error": None if ok else f"no profile named {name!r}"}
        if c == "next":
            return {"ok": True, "profile": self.cycle(1)}
        if c == "prev":
            return {"ok": True, "profile": self.cycle(-1)}
        if c in ("on", "off", "toggle"):
            e.set_power(True if c == "on" else False if c == "off" else not e.power)
            return {"ok": True, "power": e.power}
        if c == "brightness":
            p = e.profile
            p.brightness = max(0.0, min(1.0, float(cmd.get("value", 1.0))))
            e.set_profile(p)
            return {"ok": True}
        if c == "show":
            if self.on_show:
                self.on_show()
                return {"ok": True}
            return {"ok": False, "error": "running headless (daemon); no window"}
        if c == "rescan":
            e.scan()
            return {"ok": True}
        if c == "status":
            st = e.status()
            return {
                "ok": True,
                "power": e.power,
                "profile": self.settings.get("active_profile"),
                "profiles": self.store.names(),
                "fps": round(st.fps, 1),
                "devices": [
                    {"name": s.device.name, "uid": s.device.uid, "ok": s.ok, "error": s.error,
                     "firmware": s.device.info.firmware, "location": s.device.info.location,
                     "zones": [{"id": z.id, "name": z.name, "leds": z.led_count} for z in s.device.zones]}
                    for s in st.devices
                ],
                "detect_errors": st.detect_errors,
                "audio_error": st.audio_error,
            }
        return {"ok": False, "error": f"unknown command {c!r}"}

    def shutdown(self) -> None:
        self.engine.stop()
