"""Temperature sensors from /sys/class/hwmon (plus nvidia-smi if present)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

HWMON = Path("/sys/class/hwmon")

CPU_PREFS = [("k10temp", "Tctl"), ("k10temp", "Tdie"), ("zenpower", "Tdie"),
             ("coretemp", "Package id 0"), ("k10temp", None), ("coretemp", None),
             ("cpu_thermal", None), ("acpitz", None)]
GPU_PREFS = [("amdgpu", "junction"), ("amdgpu", "edge"), ("nvidia", "GPU"),
             ("nouveau", None), ("i915", None), ("xe", None)]

AUTO_SENSORS = {"auto:cpu": "CPU (automatic)", "auto:gpu": "GPU (automatic)"}


class Sensors:
    def __init__(self, root: Path = HWMON) -> None:
        self.root = root
        self._paths: dict[str, Path] = {}
        self._labels: dict[str, str] = {}
        self._nvidia_cache: tuple[float, float | None] = (0.0, None)
        self.rescan()

    def rescan(self) -> None:
        self._paths.clear()
        self._labels.clear()
        if not self.root.exists():
            return
        for hw in sorted(self.root.iterdir()):
            try:
                chip = (hw / "name").read_text().strip()
            except OSError:
                continue
            for inp in sorted(hw.glob("temp*_input")):
                idx = inp.name[: -len("_input")]
                lbl_file = hw / f"{idx}_label"
                try:
                    label = lbl_file.read_text().strip() if lbl_file.exists() else idx
                except OSError:
                    label = idx
                key = f"{chip}/{label}"
                n = 2
                while key in self._paths:
                    key = f"{chip}/{label} #{n}"
                    n += 1
                self._paths[key] = inp
                self._labels[key] = f"{chip} — {label}"
        if shutil.which("nvidia-smi"):
            self._labels["nvidia/GPU"] = "NVIDIA GPU"

    def available(self) -> dict[str, str]:
        out = dict(AUTO_SENSORS)
        out.update(self._labels)
        return out

    def _resolve(self, key: str) -> str | None:
        if key in self._labels:
            return key
        prefs = CPU_PREFS if key == "auto:cpu" else GPU_PREFS if key == "auto:gpu" else []
        for chip, label in prefs:
            for k in self._labels:
                c, _, l = k.partition("/")
                if c == chip and (label is None or l == label):
                    return k
        return None

    def read(self, key: str) -> float | None:
        real = self._resolve(key)
        if real is None:
            return None
        if real == "nvidia/GPU":
            return self._read_nvidia()
        try:
            return int(self._paths[real].read_text().strip()) / 1000.0
        except (OSError, ValueError, KeyError):
            return None

    def read_many(self, keys) -> dict[str, float]:
        out = {}
        for k in set(keys):
            v = self.read(k)
            if v is not None:
                out[k] = v
        return out

    def _read_nvidia(self) -> float | None:
        ts, val = self._nvidia_cache
        if time.monotonic() - ts < 2.0:
            return val
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=2)
            val = float(r.stdout.strip().splitlines()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            val = None
        self._nvidia_cache = (time.monotonic(), val)
        return val
