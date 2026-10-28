"""The lighting engine: owns the devices and renders profiles on a thread.

The engine has no GUI dependencies so it can run headless (``feynosrgb
daemon``) or inside the Qt app.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .audio import AudioCapture
from .color import BLACK, RGB, apply_brightness, parse_hex
from .config import Profile, Settings
from .devices import RGBDevice, detect_all
from .effects import FrameContext, get_effect
from .sensors import Sensors

log = logging.getLogger(__name__)

RECONNECT_INTERVAL = 5.0


@dataclass
class DeviceState:
    device: RGBDevice
    ok: bool = False
    error: str = ""
    last_attempt: float = 0.0


@dataclass
class EngineStatus:
    devices: list[DeviceState] = field(default_factory=list)
    detect_errors: list[str] = field(default_factory=list)
    audio_error: str | None = None
    audio_backend: str | None = None
    fps: float = 0.0


class Engine:
    def __init__(self, settings: Settings, demo: bool = False):
        self.settings = settings
        self.demo = demo
        self.sensors = Sensors()
        self.audio = AudioCapture()
        self._states: list[DeviceState] = []
        self._detect_errors: list[str] = []
        self._profile = Profile()
        self._power = True
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._dirty = True                  # onboard/off state must be (re)applied
        self._effect_t0 = time.monotonic()
        self._zone_state: dict[str, dict] = {}
        self._temps: dict[str, float] = {}
        self._last_temp_read = 0.0
        self._fps = 0.0
        self._last_full_send = 0.0
        self.last_frame: dict[str, list[RGB]] = {}
        self.on_status: Callable[[], None] | None = None

    # ------------------------------------------------------------ devices
    def scan(self) -> None:
        with self._lock:
            for st in self._states:
                try:
                    st.device.close()
                except Exception:
                    pass
            devices, errors = detect_all(self.settings, demo=self.demo)
            self._states = [DeviceState(d) for d in devices]
            self._detect_errors = errors
            for st in self._states:
                self._try_open(st)
            self._dirty = True
            self.last_frame.clear()
        self._notify()
        self._wake.set()

    def _try_open(self, st: DeviceState) -> None:
        st.last_attempt = time.monotonic()
        try:
            st.device.open()
            st.ok, st.error = True, ""
        except Exception as e:
            st.ok, st.error = False, str(e)
            log.warning("could not open %s: %s", st.device.name, e)

    def devices(self) -> list[RGBDevice]:
        with self._lock:
            return [s.device for s in self._states if s.ok]

    def status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(
                devices=list(self._states),
                detect_errors=list(self._detect_errors),
                audio_error=self.audio.error if self.audio.running else None,
                audio_backend=self.audio.backend if self.audio.running else None,
                fps=self._fps,
            )

    def zone_keys(self) -> list[tuple[str, str, int]]:
        """[(zone_key, display name, led_count)] for every usable zone."""
        out = []
        for d in self.devices():
            for z in d.zones:
                out.append((d.zone_key(z), f"{d.name} · {z.name}", z.led_count))
        return out

    def resize_zone(self, uid: str, zone_id: str, count: int) -> None:
        with self._lock:
            for d in self.devices():
                if d.uid == uid:
                    d.resize_zone(zone_id, count)
                    z = d.zone(zone_id)
                    if z:
                        self.settings.set_zone_size(uid, zone_id, z.led_count)
                        self.settings.save()
            self._zone_state.clear()
            self._dirty = True
        self._wake.set()

    # ------------------------------------------------------------ profile
    @property
    def profile(self) -> Profile:
        with self._lock:
            return Profile.from_dict(self._profile.to_dict())

    def set_profile(self, profile: Profile, restart_time: bool = False) -> None:
        with self._lock:
            old_mode = self._profile.mode
            self._profile = Profile.from_dict(profile.to_dict())
            if restart_time or old_mode != profile.mode:
                self._effect_t0 = time.monotonic()
                self._zone_state.clear()
            self._dirty = True
        self._wake.set()

    @property
    def power(self) -> bool:
        return self._power

    def set_power(self, on: bool) -> None:
        with self._lock:
            self._power = bool(on)
            self._dirty = True
        self._wake.set()
        self._notify()

    def save_onboard_to_devices(self) -> list[str]:
        """Write the profile's onboard effect to controller flash. Returns errors."""
        p = self.profile
        errors = []
        color = parse_hex(p.onboard_color)
        with self._lock:
            for d in self.devices():
                if not d.supports_save:
                    continue
                modes = {m.id for m in d.hardware_modes()}
                mode = p.onboard_mode if p.onboard_mode in modes else "static"
                try:
                    d.save_to_device(mode, color)
                except Exception as e:
                    errors.append(f"{d.name}: {e}")
            self._dirty = True
        self._wake.set()
        return errors

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rgb-engine", daemon=True)
        self._thread.start()

    def stop(self, restore: str | None = None) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.audio.stop()
        restore = restore or self.settings.get("restore_on_exit", "keep")
        with self._lock:
            for st in self._states:
                if not st.ok:
                    continue
                d = st.device
                try:
                    if restore == "off":
                        for z in d.zones:
                            d.set_zone_colors(z.id, [BLACK] * z.led_count)
                    elif restore == "onboard" and d.hardware_modes():
                        p = self._profile
                        d.set_hardware_mode(p.onboard_mode, parse_hex(p.onboard_color))
                except Exception as e:
                    log.warning("restore on %s failed: %s", d.name, e)
                try:
                    d.close()
                except Exception:
                    pass

    def _notify(self) -> None:
        cb = self.on_status
        if cb:
            try:
                cb()
            except Exception:
                log.exception("status callback failed")

    def _run(self) -> None:
        frames = 0
        fps_t = time.monotonic()
        prev = time.monotonic()
        while not self._stop.is_set():
            fps = max(5, min(60, int(self.settings.get("fps", 30))))
            frame_start = time.monotonic()
            dt = min(0.25, frame_start - prev)
            prev = frame_start
            animating = False
            try:
                animating = self._frame(dt)
            except Exception:
                log.exception("frame failed")
            self._reconnect()
            frames += 1
            if frame_start - fps_t >= 1.0:
                self._fps = frames / (frame_start - fps_t)
                frames, fps_t = 0, frame_start
            if animating:
                delay = max(0.0, 1.0 / fps - (time.monotonic() - frame_start))
                self._stop.wait(delay)
            else:
                self._fps = 0.0
                self._wake.wait(1.0)
                self._wake.clear()

    def _reconnect(self) -> None:
        now = time.monotonic()
        changed = False
        with self._lock:
            for st in self._states:
                if not st.ok and now - st.last_attempt > RECONNECT_INTERVAL:
                    try:
                        st.device.close()
                    except Exception:
                        pass
                    self._try_open(st)
                    if st.ok:
                        changed = True
                        self._dirty = True
        if changed:
            self._notify()

    def _fail(self, st: DeviceState, e: Exception) -> None:
        log.warning("%s stopped responding: %s", st.device.name, e)
        st.ok, st.error = False, str(e)
        st.last_attempt = time.monotonic()
        self._notify()

    def _frame(self, dt: float) -> bool:
        """Render one frame. Returns True while something is animating."""
        with self._lock:
            profile = self._profile
            power = self._power
            dirty = self._dirty
            self._dirty = False
            states = [s for s in self._states if s.ok]

        if not power:
            self._ensure_audio(False)
            if dirty:
                for st in states:
                    try:
                        for z in st.device.zones:
                            st.device.set_zone_colors(z.id, [BLACK] * z.led_count)
                        st.device.flush()
                    except Exception as e:
                        self._fail(st, e)
                self.last_frame = {}
            return False

        if profile.mode == "onboard":
            self._ensure_audio(False)
            if dirty:
                color = parse_hex(profile.onboard_color)
                for st in states:
                    d = st.device
                    try:
                        modes = {m.id for m in d.hardware_modes()}
                        if modes:
                            d.set_hardware_mode(profile.onboard_mode if profile.onboard_mode in modes else "static", color)
                        else:
                            for z in d.zones:
                                d.set_zone_colors(z.id, [color] * z.led_count)
                    except Exception as e:
                        self._fail(st, e)
                self.last_frame = {}
            return False

        # --- software rendering
        specs = {}
        all_zones = []
        for st in states:
            for z in st.device.zones:
                key = st.device.zone_key(z)
                all_zones.append((st, z, key))
                if key not in profile.disabled:
                    specs[key] = profile.spec_for(key)

        needs_audio = any(get_effect(s.effect).uses_audio for s in specs.values())
        self._ensure_audio(needs_audio)
        sensor_keys = [s.params.get("sensor", "auto:cpu") for s in specs.values()
                       if get_effect(s.effect).uses_sensors]
        now = time.monotonic()
        if sensor_keys and now - self._last_temp_read > 1.0:
            self._temps = self.sensors.read_many(sensor_keys)
            self._last_temp_read = now

        audio = self.audio.snapshot() if needs_audio else None
        # Static profiles re-send every few seconds so a controller that was
        # reset (e.g. after suspend/resume) picks the colors back up.
        force = dirty or now - self._last_full_send > 5.0
        if force:
            self._last_full_send = now
        t = now - self._effect_t0
        frame: dict[str, list[RGB]] = {}
        animating = False
        failed: set[int] = set()
        for idx, (st, z, key) in enumerate(all_zones):
            if id(st) in failed or z.led_count <= 0:
                continue
            spec = specs.get(key)
            if spec is None:
                colors = [BLACK] * z.led_count
            else:
                eff = get_effect(spec.effect)
                ctx = FrameContext(t=t, dt=dt, audio=audio, temps=self._temps,
                                   zone_index=idx, zone_count=len(all_zones))
                zstate = self._zone_state.setdefault(key, {})
                colors = eff.render(spec.params, z.led_count, ctx, zstate)
                if len(colors) != z.led_count:  # defensive: never send a short frame
                    colors = (list(colors) + [BLACK] * z.led_count)[: z.led_count]
                colors = apply_brightness(colors, profile.brightness)
                if eff.id not in ("static", "off"):
                    animating = True
            frame[key] = colors
            if not animating and not force and self.last_frame.get(key) == colors:
                continue
            try:
                st.device.set_zone_colors(z.id, colors)
            except Exception as e:
                failed.add(id(st))
                self._fail(st, e)
        for st in states:
            if id(st) not in failed:
                try:
                    st.device.flush()
                except Exception as e:
                    self._fail(st, e)
        self.last_frame = frame
        return animating

    def _ensure_audio(self, needed: bool) -> None:
        if needed and not self.audio.running:
            self.audio.start()
            self._notify()
        elif not needed and self.audio.running:
            self.audio.stop()
            self._notify()
