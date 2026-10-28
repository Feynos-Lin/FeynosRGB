"""Settings and lighting profiles, stored as JSON under ~/.config/feynosrgb."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .effects import EFFECTS_BY_ID, get_effect

log = logging.getLogger(__name__)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "feynosrgb"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


DEFAULT_SETTINGS = {
    "fps": 30,
    "start_minimized": False,
    "close_to_tray": True,
    "autostart": False,
    "active_profile": "Default",
    "zone_sizes": {},          # uid -> {zone_id: led_count}
    "demo_device": False,
    "restore_on_exit": "keep",  # keep | off | onboard
}


class Settings(dict):
    def __init__(self, path: Path | None = None):
        super().__init__(DEFAULT_SETTINGS)
        self.path = path or (config_dir() / "settings.json")
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                self.update(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read %s: %s", self.path, e)

    def save(self) -> None:
        _atomic_write(self.path, dict(self))

    def set_zone_size(self, uid: str, zone_id: str, count: int) -> None:
        self.setdefault("zone_sizes", {}).setdefault(uid, {})[zone_id] = int(count)


@dataclass
class EffectSpec:
    effect: str = "rainbow_wave"
    params: dict = field(default_factory=dict)

    def normalized(self) -> "EffectSpec":
        eff = get_effect(self.effect)
        return EffectSpec(eff.id, eff.normalize(self.params))


@dataclass
class Profile:
    name: str = "Default"
    brightness: float = 1.0
    #: "software" = rendered live by FeynosRGB, "onboard" = controller's own effect
    mode: str = "software"
    #: "*" applies to every zone; "<device uid>/<zone id>" overrides one zone.
    zones: dict[str, EffectSpec] = field(default_factory=lambda: {"*": EffectSpec()})
    #: zone keys that are switched off in this profile
    disabled: list[str] = field(default_factory=list)
    onboard_mode: str = "rainbow"
    onboard_color: str = "#ff0040"

    def spec_for(self, zone_key: str) -> EffectSpec:
        return self.zones.get(zone_key) or self.zones.get("*") or EffectSpec()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        zones = {}
        for k, v in (d.get("zones") or {}).items():
            if isinstance(v, dict) and v.get("effect") in EFFECTS_BY_ID:
                zones[k] = EffectSpec(v["effect"], dict(v.get("params") or {})).normalized()
        if "*" not in zones:
            zones["*"] = EffectSpec().normalized()
        mode = d.get("mode", "software")
        return cls(
            name=str(d.get("name", "Profile")),
            brightness=max(0.0, min(1.0, float(d.get("brightness", 1.0)))),
            mode=mode if mode in ("software", "onboard") else "software",
            zones=zones,
            disabled=[str(x) for x in d.get("disabled", [])],
            onboard_mode=str(d.get("onboard_mode", "rainbow")),
            onboard_color=str(d.get("onboard_color", "#ff0040")),
        )


_SAFE = re.compile(r"[^A-Za-z0-9 _.-]+")


def _filename(name: str) -> str:
    s = _SAFE.sub("_", name).strip(" .") or "profile"
    return s[:80] + ".json"


BUILTIN_PROFILES = [
    Profile("Default", zones={"*": EffectSpec("rainbow_wave").normalized()}),
    Profile("Calm blue", zones={"*": EffectSpec("breathing", {"colors": ["#0060ff", "#00c0ff"], "speed": 0.5}).normalized()}),
    Profile("Music", zones={"*": EffectSpec("audio_spectrum").normalized()}),
    Profile("CPU temperature", zones={"*": EffectSpec("temperature").normalized()}),
    Profile("Lights off", zones={"*": EffectSpec("off").normalized()}),
]


class ProfileStore:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or (config_dir() / "profiles")
        self._profiles: dict[str, Profile] = {}
        self.load()

    def load(self) -> None:
        self._profiles.clear()
        if self.dir.exists():
            for f in sorted(self.dir.glob("*.json")):
                try:
                    p = Profile.from_dict(json.loads(f.read_text()))
                    self._profiles[p.name] = p
                except (OSError, ValueError, TypeError) as e:
                    log.warning("skipping bad profile %s: %s", f, e)
        if not self._profiles:
            for p in BUILTIN_PROFILES:
                self.save(Profile.from_dict(p.to_dict()))

    def names(self) -> list[str]:
        return sorted(self._profiles, key=str.lower)

    def get(self, name: str) -> Profile | None:
        p = self._profiles.get(name)
        return Profile.from_dict(p.to_dict()) if p else None

    def save(self, profile: Profile) -> None:
        self._profiles[profile.name] = Profile.from_dict(profile.to_dict())
        _atomic_write(self.dir / _filename(profile.name), profile.to_dict())

    def delete(self, name: str) -> None:
        self._profiles.pop(name, None)
        try:
            (self.dir / _filename(name)).unlink()
        except FileNotFoundError:
            pass

    def rename(self, old: str, new: str) -> None:
        p = self.get(old)
        if p is None:
            return
        self.delete(old)
        p.name = new
        self.save(p)
