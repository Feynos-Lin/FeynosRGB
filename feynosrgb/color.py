"""Small color helpers. Colors are (r, g, b) tuples of ints 0-255."""

from __future__ import annotations

import colorsys
from typing import Iterable, Sequence

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


def clamp8(v: float) -> int:
    v = int(round(v))
    return 0 if v < 0 else 255 if v > 255 else v


def parse_hex(value: str | Sequence[int]) -> RGB:
    """Accept '#rrggbb', 'rrggbb' or an (r, g, b) sequence."""
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            raise ValueError(f"invalid color: {value!r}")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    r, g, b = value
    return (clamp8(r), clamp8(g), clamp8(b))


def to_hex(c: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    """h in [0, 1) (wraps), s and v in [0, 1]."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (clamp8(r * 255), clamp8(g * 255), clamp8(b * 255))


def scale(c: RGB, f: float) -> RGB:
    return (clamp8(c[0] * f), clamp8(c[1] * f), clamp8(c[2] * f))


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (
        clamp8(a[0] + (b[0] - a[0]) * t),
        clamp8(a[1] + (b[1] - a[1]) * t),
        clamp8(a[2] + (b[2] - a[2]) * t),
    )


def add(a: RGB, b: RGB) -> RGB:
    return (clamp8(a[0] + b[0]), clamp8(a[1] + b[1]), clamp8(a[2] + b[2]))


def gradient(stops: Sequence[RGB], t: float) -> RGB:
    """Sample a multi-stop gradient at t in [0, 1]."""
    if not stops:
        return BLACK
    if len(stops) == 1:
        return stops[0]
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    pos = t * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    return lerp(stops[i], stops[i + 1], pos - i)


def gradient_cyclic(stops: Sequence[RGB], t: float) -> RGB:
    """Sample a gradient that wraps from the last stop back to the first."""
    if not stops:
        return BLACK
    if len(stops) == 1:
        return stops[0]
    t %= 1.0
    pos = t * len(stops)
    i = int(pos) % len(stops)
    return lerp(stops[i], stops[(i + 1) % len(stops)], pos - int(pos))


def apply_brightness(colors: Iterable[RGB], brightness: float) -> list[RGB]:
    if brightness >= 0.999:
        return list(colors)
    # Perceptual-ish curve so the low end of the slider is usable.
    f = max(0.0, min(1.0, brightness)) ** 2.0
    return [scale(c, f) for c in colors]
