"""Software effects rendered by the engine at a fixed frame rate.

Each effect declares its parameters (used to build the editor UI and to
validate profiles) and renders a list of colors for one zone per frame.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..color import (BLACK, RGB, gradient, gradient_cyclic, hsv, lerp, parse_hex,
                     scale, to_hex)


# ---------------------------------------------------------------- parameters
@dataclass
class Param:
    id: str
    label: str
    kind: str                     # color | colors | float | int | choice | bool | sensor
    default: Any
    min: float = 0.0
    max: float = 1.0
    step: float = 0.01
    choices: list[tuple[str, str]] = field(default_factory=list)  # (value, label)
    suffix: str = ""

    def coerce(self, value: Any) -> Any:
        try:
            if self.kind == "color":
                return to_hex(parse_hex(value))
            if self.kind == "colors":
                vals = [to_hex(parse_hex(v)) for v in value]
                return vals or list(self.default)
            if self.kind == "float":
                return max(self.min, min(self.max, float(value)))
            if self.kind == "int":
                return int(max(self.min, min(self.max, int(value))))
            if self.kind == "bool":
                return bool(value)
            if self.kind == "choice":
                valid = {c[0] for c in self.choices}
                return value if value in valid else self.default
            if self.kind == "sensor":
                return str(value)
        except (TypeError, ValueError):
            pass
        return self.default


@dataclass
class AudioFrame:
    bands: list[float]            # 0..1, low -> high frequency
    level: float                  # 0..1 overall loudness
    bass: float                   # 0..1
    beat: bool = False


@dataclass
class FrameContext:
    t: float                      # seconds since the effect started
    dt: float                     # seconds since previous frame
    audio: AudioFrame | None = None
    temps: dict[str, float] = field(default_factory=dict)
    zone_index: int = 0           # position of this zone among rendered zones
    zone_count: int = 1


RenderFn = Callable[[dict, int, FrameContext, dict], list[RGB]]


@dataclass
class Effect:
    id: str
    name: str
    category: str                 # "Basic", "Animated", "Audio", "System"
    params: list[Param]
    render: RenderFn
    uses_audio: bool = False
    uses_sensors: bool = False
    description: str = ""

    def defaults(self) -> dict:
        return {p.id: p.default for p in self.params}

    def normalize(self, params: dict | None) -> dict:
        out = self.defaults()
        for p in self.params:
            if params and p.id in params:
                out[p.id] = p.coerce(params[p.id])
        return out


def _c(v) -> RGB:
    return parse_hex(v)


def _cs(vs) -> list[RGB]:
    return [parse_hex(v) for v in vs] or [BLACK]


SPEED = lambda d=1.0: Param("speed", "Speed", "float", d, 0.05, 5.0, 0.05, suffix="×")  # noqa: E731
DIRECTION = Param("direction", "Direction", "choice", "forward",
                  choices=[("forward", "Forward"), ("reverse", "Reverse")])


def _dir(p: dict) -> float:
    return -1.0 if p.get("direction") == "reverse" else 1.0


# ------------------------------------------------------------------ renderers
def r_off(p, n, ctx, st):
    return [BLACK] * n


def r_static(p, n, ctx, st):
    return [_c(p["color"])] * n


def r_breathing(p, n, ctx, st):
    colors = _cs(p["colors"])
    period = 4.0 / p["speed"]
    cycle = int(ctx.t / period)
    phase = (ctx.t % period) / period
    v = (1 - math.cos(phase * 2 * math.pi)) / 2      # 0 -> 1 -> 0
    v = p["min_brightness"] + (1 - p["min_brightness"]) * v
    c = colors[cycle % len(colors)]
    return [scale(c, v)] * n


def r_color_cycle(p, n, ctx, st):
    c = hsv(ctx.t * 0.1 * p["speed"], p["saturation"], 1.0)
    return [c] * n


def r_rainbow_wave(p, n, ctx, st):
    shift = ctx.t * 0.2 * p["speed"] * _dir(p)
    sat = p["saturation"]
    span = p["scale"]
    if n <= 1:
        return [hsv(shift, sat)] * n
    return [hsv(shift + span * i / n, sat) for i in range(n)]


def r_gradient(p, n, ctx, st):
    stops = _cs(p["colors"])
    shift = ctx.t * 0.1 * p["speed"] * _dir(p)
    if p["speed"] <= 0.051:
        return [gradient(stops, i / max(1, n - 1)) for i in range(n)]
    return [gradient_cyclic(stops, shift + i / max(1, n)) for i in range(n)]


def r_comet(p, n, ctx, st):
    if n == 0:
        return []
    bg = _c(p["background"])
    head_color = _c(p["color"])
    tail = max(1.0, p["tail"] * n)
    pos = (ctx.t * p["speed"] * 0.5 * n) % (n + tail)
    out = []
    for i in range(n):
        d = pos - i
        if 0 <= d <= tail:
            f = (1 - d / tail) ** 2
            col = hsv(ctx.t * 0.1 + i / n) if p["rainbow"] else head_color
            out.append(lerp(bg, col, f))
        else:
            out.append(bg)
    if _dir(p) < 0:
        out.reverse()
    return out


def r_twinkle(p, n, ctx, st):
    colors = _cs(p["colors"])
    bg = _c(p["background"])
    levels: list[float] = st.setdefault("levels", [0.0] * n)
    picks: list[RGB] = st.setdefault("picks", [colors[0]] * n)
    if len(levels) != n:
        levels[:] = [0.0] * n
        picks[:] = [colors[0]] * n
    rate = p["density"] * 4.0 * ctx.dt
    decay = math.exp(-ctx.dt * 2.5 * p["speed"])
    out = []
    for i in range(n):
        levels[i] *= decay
        if random.random() < rate:
            levels[i] = 1.0
            picks[i] = random.choice(colors)
        out.append(lerp(bg, picks[i], levels[i]))
    return out


def r_fire(p, n, ctx, st):
    heat: list[float] = st.setdefault("heat", [0.0] * n)
    if len(heat) != n:
        heat[:] = [0.0] * n
    if n == 0:
        return []
    cooling = 0.25 * ctx.dt * 30 * p["cooling"]
    for i in range(n):
        heat[i] = max(0.0, heat[i] - random.random() * cooling * 0.3)
    for i in range(n - 1, 1, -1):
        heat[i] = (heat[i - 1] + heat[i - 2] * 2) / 3
    if random.random() < p["sparks"]:
        k = random.randint(0, min(2, n - 1))
        heat[k] = min(1.0, heat[k] + 0.5 + random.random() * 0.5)
    stops = [BLACK, _c(p["color_low"]), _c(p["color_high"]), (255, 255, 200)]
    if n <= 2:  # single-LED zones flicker instead
        v = 0.6 + 0.4 * random.random()
        return [gradient(stops, v * 0.85)] * n
    return [gradient(stops, h) for h in heat]


def r_strobe(p, n, ctx, st):
    period = 1.0 / max(0.1, p["rate"])
    on = (ctx.t % period) < period * 0.25
    return [_c(p["color"]) if on else BLACK] * n


# ---- audio
def _audio(ctx) -> AudioFrame:
    return ctx.audio or AudioFrame(bands=[0.0] * 16, level=0.0, bass=0.0)


def _boost(v: float, sens: float) -> float:
    return max(0.0, min(1.0, v * sens))


def r_audio_spectrum(p, n, ctx, st):
    a = _audio(ctx)
    lo, hi = _c(p["color_low"]), _c(p["color_high"])
    bands = a.bands or [0.0]
    out = []
    for i in range(n):
        b = bands[min(len(bands) - 1, int(i / max(1, n) * len(bands)))]
        v = _boost(b, p["sensitivity"])
        out.append(scale(lerp(lo, hi, i / max(1, n - 1)), max(p["floor"], v)))
    if n <= 1:
        v = _boost(a.level, p["sensitivity"])
        return [scale(lerp(lo, hi, v), max(p["floor"], v))] * n
    return out


def r_audio_pulse(p, n, ctx, st):
    a = _audio(ctx)
    colors = _cs(p["colors"])
    if a.beat:
        st["idx"] = st.get("idx", 0) + 1
    target = _boost(a.bass if p["source"] == "bass" else a.level, p["sensitivity"])
    cur = st.get("v", 0.0)
    # Fast attack, smooth release.
    cur = target if target > cur else cur + (target - cur) * min(1.0, ctx.dt * 6)
    st["v"] = cur
    c = colors[st.get("idx", 0) % len(colors)]
    return [scale(c, max(p["floor"], cur))] * n


def r_audio_vu(p, n, ctx, st):
    a = _audio(ctx)
    level = _boost(a.level, p["sensitivity"])
    peak = max(st.get("peak", 0.0) - ctx.dt * 0.6, level)
    st["peak"] = peak
    stops = _cs(p["colors"])
    bg = _c(p["background"])
    if n <= 2:
        return [scale(gradient(stops, level), max(0.05, level))] * n
    lit = level * n
    pk = min(n - 1, int(peak * n))
    out = []
    for i in range(n):
        if i < lit:
            out.append(gradient(stops, i / (n - 1)))
        elif i == pk and peak > 0.02:
            out.append(gradient(stops, i / (n - 1)))
        else:
            out.append(bg)
    return out


# ---- system
def r_temperature(p, n, ctx, st):
    temp = ctx.temps.get(p["sensor"])
    if temp is None and ctx.temps:
        temp = next(iter(ctx.temps.values()))
    if temp is None:
        return [scale(_c(p["color_low"]), 0.3)] * n
    lo, hi = p["low"], max(p["low"] + 1, p["high"])
    f = (temp - lo) / (hi - lo)
    cur = st.get("f", f)
    cur += (f - cur) * min(1.0, ctx.dt * 2)  # smooth sudden jumps
    st["f"] = cur
    c = gradient([_c(p["color_low"]), _c(p["color_mid"]), _c(p["color_high"])], cur)
    if p["pulse_when_hot"] and cur >= 1.0:
        c = scale(c, 0.55 + 0.45 * (1 + math.sin(ctx.t * 6)) / 2)
    return [c] * n


# ------------------------------------------------------------------ registry
EFFECTS: list[Effect] = [
    Effect("static", "Static", "Basic", [Param("color", "Color", "color", "#00a2ff")], r_static,
           description="One solid color."),
    Effect("breathing", "Breathing", "Basic", [
        Param("colors", "Colors", "colors", ["#ff0040"]),
        SPEED(1.0),
        Param("min_brightness", "Minimum brightness", "float", 0.0, 0.0, 0.8, 0.05),
    ], r_breathing, description="Fades in and out, moving to the next color each breath."),
    Effect("color_cycle", "Spectrum cycle", "Basic", [
        SPEED(1.0), Param("saturation", "Saturation", "float", 1.0, 0.0, 1.0),
    ], r_color_cycle, description="All LEDs cycle through the color wheel together."),
    Effect("rainbow_wave", "Rainbow wave", "Animated", [
        SPEED(1.0), Param("scale", "Rainbows per strip", "float", 1.0, 0.1, 5.0, 0.1),
        Param("saturation", "Saturation", "float", 1.0, 0.0, 1.0), DIRECTION,
    ], r_rainbow_wave, description="A rainbow that flows along each strip."),
    Effect("gradient", "Gradient", "Animated", [
        Param("colors", "Colors", "colors", ["#ff0080", "#7a00ff", "#00c8ff"]),
        SPEED(0.5), DIRECTION,
    ], r_gradient, description="Your own color stops; set speed to minimum for a still gradient."),
    Effect("comet", "Comet", "Animated", [
        Param("color", "Color", "color", "#00ffd0"),
        Param("background", "Background", "color", "#000000"),
        Param("tail", "Tail length", "float", 0.3, 0.05, 1.0, 0.05),
        SPEED(1.0), Param("rainbow", "Rainbow head", "bool", False), DIRECTION,
    ], r_comet, description="A light that races along the strip with a fading tail."),
    Effect("twinkle", "Twinkle", "Animated", [
        Param("colors", "Colors", "colors", ["#ffffff", "#7fd4ff"]),
        Param("background", "Background", "color", "#000010"),
        Param("density", "Density", "float", 0.3, 0.02, 1.0),
        SPEED(1.0),
    ], r_twinkle, description="Random LEDs sparkle and fade."),
    Effect("fire", "Fire", "Animated", [
        Param("color_low", "Ember color", "color", "#c81e00"),
        Param("color_high", "Flame color", "color", "#ffa000"),
        Param("cooling", "Cooling", "float", 1.0, 0.2, 3.0, 0.1),
        Param("sparks", "Sparks", "float", 0.6, 0.05, 1.0),
    ], r_fire, description="Flickering flames rising along the strip."),
    Effect("strobe", "Strobe", "Animated", [
        Param("color", "Color", "color", "#ffffff"),
        Param("rate", "Flashes per second", "float", 4.0, 0.5, 15.0, 0.5),
    ], r_strobe, description="Fast flashing. Not recommended for photosensitive viewers."),
    Effect("audio_spectrum", "Spectrum analyzer", "Audio", [
        Param("color_low", "Bass color", "color", "#ff0050"),
        Param("color_high", "Treble color", "color", "#00a0ff"),
        Param("sensitivity", "Sensitivity", "float", 1.5, 0.2, 5.0, 0.1),
        Param("floor", "Idle glow", "float", 0.05, 0.0, 0.5),
    ], r_audio_spectrum, uses_audio=True,
       description="Each LED follows a frequency band of whatever is playing."),
    Effect("audio_pulse", "Beat pulse", "Audio", [
        Param("colors", "Colors", "colors", ["#ff0060", "#00e0ff", "#ffd000"]),
        Param("source", "React to", "choice", "bass", choices=[("bass", "Bass"), ("level", "Overall volume")]),
        Param("sensitivity", "Sensitivity", "float", 1.5, 0.2, 5.0, 0.1),
        Param("floor", "Idle glow", "float", 0.05, 0.0, 0.5),
    ], r_audio_pulse, uses_audio=True,
       description="Flashes with the beat and switches color on each kick."),
    Effect("audio_vu", "VU meter", "Audio", [
        Param("colors", "Colors", "colors", ["#00ff40", "#ffe000", "#ff0000"]),
        Param("background", "Background", "color", "#000000"),
        Param("sensitivity", "Sensitivity", "float", 1.5, 0.2, 5.0, 0.1),
    ], r_audio_vu, uses_audio=True, description="Fills the strip with the volume level."),
    Effect("temperature", "Temperature", "System", [
        Param("sensor", "Sensor", "sensor", "auto:cpu"),
        Param("low", "Cool at", "float", 40.0, 0.0, 100.0, 1.0, suffix=" °C"),
        Param("high", "Hot at", "float", 80.0, 10.0, 110.0, 1.0, suffix=" °C"),
        Param("color_low", "Cool color", "color", "#0080ff"),
        Param("color_mid", "Warm color", "color", "#ffd000"),
        Param("color_high", "Hot color", "color", "#ff0000"),
        Param("pulse_when_hot", "Pulse when hot", "bool", True),
    ], r_temperature, uses_sensors=True, description="Color follows a CPU or GPU temperature."),
    Effect("off", "Off", "Basic", [], r_off, description="LEDs off."),
]

EFFECTS_BY_ID: dict[str, Effect] = {e.id: e for e in EFFECTS}
CATEGORIES = ["Basic", "Animated", "Audio", "System"]


def get_effect(effect_id: str) -> Effect:
    return EFFECTS_BY_ID.get(effect_id, EFFECTS_BY_ID["static"])
