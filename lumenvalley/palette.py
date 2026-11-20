"""Colour system: ANSI index conversion, contrast, and hazard/collectible roles.

The game authors colour as xterm-256 indices (so the terminal build can use
them directly) and the pygame/browser renderer converts them to RGB here.  All
readability rules in ``docs/ART_BIBLE.md`` are expressed with the helpers in
this module, and ``tests/test_visual.py`` enforces them, so a palette edit
cannot silently make a hazard disappear into the background.

Pure standard library: importable with no renderer.
"""

import colorsys

# xterm system colours.  Kept identical to the values the terminal has always
# used; the pygame front-end and the checks below share this table.
ANSI_BASE16 = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)

_rgb_cache = {}


def rgb(n):
    """Convert an xterm-256 index (or an RGB triple) to an RGB triple."""
    if isinstance(n, (tuple, list)):
        return tuple(n)
    n = int(n) % 256
    cached = _rgb_cache.get(n)
    if cached is not None:
        return cached
    if n < 16:
        c = ANSI_BASE16[n]
    elif n < 232:
        i = n - 16
        r, g, b = i // 36, (i // 6) % 6, i % 6
        step = lambda u: 0 if u == 0 else 55 + 40 * u  # noqa: E731
        c = (step(r), step(g), step(b))
    else:
        v = 8 + (n - 232) * 10
        c = (v, v, v)
    _rgb_cache[n] = c
    return c


def lerp_rgb(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def luminance(c):
    """WCAG relative luminance of an RGB triple."""

    def channel(value):
        value /= 255.0
        return value / 12.92 if value <= 0.03928 \
            else ((value + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(c[0]) + 0.7152 * channel(c[1])
            + 0.0722 * channel(c[2]))


def contrast(a, b):
    """WCAG contrast ratio between two colours (1.0 = identical)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hue_gap(a, b):
    """Distance between two hues in degrees (0-180)."""
    ha = colorsys.rgb_to_hsv(*(v / 255.0 for v in a))[0] * 360.0
    hb = colorsys.rgb_to_hsv(*(v / 255.0 for v in b))[0] * 360.0
    d = abs(ha - hb) % 360.0
    return min(d, 360.0 - d)


def saturation(c):
    return colorsys.rgb_to_hsv(*(v / 255.0 for v in c))[1]


# ------------------------------------------------------------ fixed roles --
# Hazards are warm and outlined; collectibles are gold/cyan.  These are the
# only colours in the game that are not derived from a level theme.
OUTLINE_DARK = (12, 10, 18)
OUTLINE_LIGHT = (250, 246, 236)
LAVA_DEEP = (96, 16, 22)
LAVA_BODY = (140, 24, 28)
LAVA_HOT = (255, 170, 60)
COIN = rgb(220)
COIN_SHINE = rgb(227)
GEM = rgb(51)

# Prism Resonance (Phase 7): a hollow, pulsing ring.  Gems stay filled diamonds,
# hazards stay warm, so the traversal language is distinct by shape *and* by
# value even though both crystals are cyan.
PRISM = (140, 245, 255)
PRISM_CORE = (236, 255, 255)
PRISM_GLOW = (74, 206, 240)
PRISM_SPENT = (82, 112, 126)


def rim_for(fill):
    """Highest-contrast outline for *fill*: hazards must read on any surface.

    Whichever of dark/light wins is used, so an outline always separates the
    hazard from both bright skies and dark caves.
    """
    dark = contrast(fill, OUTLINE_DARK)
    light = contrast(fill, OUTLINE_LIGHT)
    return OUTLINE_DARK if dark >= light else OUTLINE_LIGHT


def rim_contrast(fill):
    return max(contrast(fill, OUTLINE_DARK), contrast(fill, OUTLINE_LIGHT))


def ensure_contrast(fg, bg, ratio=4.5, steps=20):
    """Return *fg*, lightened or darkened until it reaches *ratio* on *bg*.

    Used where a themed accent (say, the day theme's orange) is drawn as text:
    the hue stays recognisable but the value is pushed just far enough to be
    legible.  Deterministic, so screenshots and tests agree.
    """
    if contrast(fg, bg) >= ratio:
        return tuple(fg)
    towards = OUTLINE_LIGHT if luminance(bg) < 0.5 else OUTLINE_DARK
    best = tuple(fg)
    for step in range(1, steps + 1):
        cand = lerp_rgb(fg, towards, step / float(steps))
        best = cand
        if contrast(cand, bg) >= ratio:
            return cand
    return best
