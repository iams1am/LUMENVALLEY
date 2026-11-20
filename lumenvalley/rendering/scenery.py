"""Painted, layered environments for the pygame/browser renderer.

Every world is built once from its colour script (``lumenvalley.rendering.looks``)
into cached surfaces, then a frame is a handful of blits:

    sky (opaque, sun/moon/stars/glows baked in)
    -> distant silhouettes, each a seamless horizontal tile with aerial
       perspective, a rim of backlight and depth-of-field softness baked in
    -> volumetric light shafts (additive)
    -> a drifting mist band that separates the backdrop from the playfield
    ... playfield ...
    -> near foreground framing (dark, blurred, moves faster than the camera)

Nothing here reads or writes simulation state; the renderer passes the camera
offset in pixels and a cosmetic clock.  All generation is deterministic (a
hash, never the ``random`` module), so screenshots and tests are reproducible.
Pure pygame; the terminal renderer never imports this module.
"""

import math

import pygame

from . import fx
from .looks import look

W, H = 1600, 480
TILE = 1920                      # parallax tile width (>= W, seamless)

_BLUR = getattr(pygame.transform, "box_blur", None)


# ------------------------------------------------------------------ noise --
def hash01(i, seed=0):
    """Deterministic integer hash to [0, 1)."""
    x = (int(i) * 374761393 + int(seed) * 668265263 + 0x9E3779B9) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    x ^= x >> 16
    return (x & 0xFFFFFF) / float(0x1000000)


def _vnoise(x, period, seed):
    i = math.floor(x)
    f = x - i
    a = hash01(int(i) % period, seed)
    b = hash01(int(i + 1) % period, seed)
    u = f * f * (3.0 - 2.0 * f)
    return a + (b - a) * u


def fbm(px, width, seed, cells=6, octaves=4, gain=0.5, ridged=False):
    """Periodic fractal noise over ``width`` pixels (tiles seamlessly)."""
    total = norm = 0.0
    amp = 1.0
    n = int(cells)
    for o in range(octaves):
        v = _vnoise(px / float(width) * n, n, seed + o * 131)
        if ridged:
            v = 1.0 - abs(2.0 * v - 1.0)
        total += amp * v
        norm += amp
        amp *= gain
        n *= 2
    return total / norm


def _wrap(x, reach, width):
    """Copies of an element at ``x`` needed to tile across ``width``."""
    out = [x]
    if x - reach < 0:
        out.append(x + width)
    if x + reach > width:
        out.append(x - width)
    return out


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------- primitives --
def gradient(size, stops, dither=True):
    """Opaque vertical gradient from positioned ``(t, rgb)`` stops."""
    width, height = size
    surf = pygame.Surface((width, height))
    for y in range(height):
        t = y / float(max(1, height - 1))
        c = stops[-1][1]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
                k = k * k * (3.0 - 2.0 * k)          # smooth stop seams
                c = tuple(c0[i] + (c1[i] - c0[i]) * k for i in range(3))
                break
        surf.fill(tuple(max(0, min(255, int(v))) for v in c),
                  pygame.Rect(0, y, width, 1))
    if dither:
        _dither(surf)
    return surf


_DITHER = None


def _dither(surf):
    """Break 8-bit banding with a +/-1 ordered noise tile (cheap, baked)."""
    global _DITHER
    if _DITHER is None:
        tile = pygame.Surface((64, 64))
        for y in range(64):
            for x in range(64):
                v = int(hash01(x * 64 + y, 77) * 3)
                tile.set_at((x, y), (v, v, v))
        _DITHER = tile
    w, h = surf.get_size()
    for y in range(0, h, 64):
        for x in range(0, w, 64):
            surf.blit(_DITHER, (x, y), special_flags=pygame.BLEND_RGB_ADD)
    surf.fill((1, 1, 1), special_flags=pygame.BLEND_RGB_SUB)


def add_glow(diameter, colour, power=2.0, strength=1.0):
    """Additive radial light (RGB, black edge) for ``BLEND_RGB_ADD``."""
    diameter = max(4, int(diameter) // 2 * 2)
    strength = round(max(0.0, float(strength)) * 16.0) / 16.0
    power = round(float(power), 1)
    key = ("addglow", diameter, tuple(colour), power, strength)

    def build():
        # Scale the cached grey falloff, then tint it with one C-level
        # multiply: a cache miss used to be a 1,024-pixel Python loop and
        # was the main source of first-use frame spikes (measured).
        img = pygame.transform.smoothscale(_falloff(power), (diameter, diameter))
        img.fill(tuple(min(255, int(c * strength)) for c in colour),
                 special_flags=pygame.BLEND_RGB_MULT)
        return img

    return fx._cached(key, build)


def _falloff(power):
    """32x32 grey radial ``(1 - d) ** power`` (cached per power)."""
    key = ("falloff", power)

    def build():
        n = 32
        small = pygame.Surface((n, n))
        small.fill((0, 0, 0))
        c = (n - 1) / 2.0
        for y in range(n):
            for x in range(n):
                d = math.hypot(x - c, y - c) / c
                if d < 1.0:
                    v = int(255 * (1.0 - d) ** power)
                    small.set_at((x, y), (v, v, v))
        return small

    return fx._cached(key, build)


def light(surf, centre, diameter, colour, power=2.0, strength=1.0):
    """Blit an additive light centred on ``centre``."""
    if diameter < 4 or strength <= 0.03:
        return
    img = add_glow(diameter, colour, power, strength)
    surf.blit(img, (int(centre[0] - diameter / 2), int(centre[1] - diameter / 2)),
              special_flags=pygame.BLEND_RGB_ADD)


def _mask(size):
    m = pygame.Surface(size, pygame.SRCALPHA)
    m.fill((255, 255, 255, 0))
    return m


WHITE = (255, 255, 255, 255)


def colorize(mask, top, bottom, rim=None, rim_w=2, rim_alpha=200,
             side=0, glow=True):
    """Fill a white alpha mask with a vertical ramp and a backlit rim.

    ``side`` adds light on the left (-1) or right (+1) faces as well, for a
    layer lit by a low sun.  The rim is the cheap trick that turns a flat
    cut-out into a silhouette catching light.
    """
    w, h = mask.get_size()
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    out.blit(gradient((w, h), ((0.0, top), (1.0, bottom)), dither=False),
             (0, 0))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if rim is None:
        return out
    edge = mask.copy()
    edge.blit(mask, (0, rim_w), special_flags=pygame.BLEND_RGBA_SUB)
    if side:
        side_edge = mask.copy()
        side_edge.blit(mask, (-side * rim_w, 0),
                       special_flags=pygame.BLEND_RGBA_SUB)
        edge.blit(side_edge, (0, 0), special_flags=pygame.BLEND_RGBA_MAX)
    edge.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    layer = pygame.Surface((w, h), pygame.SRCALPHA)
    layer.fill(tuple(rim) + (255,))
    layer.blit(edge, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if rim_alpha < 255:
        layer.fill((255, 255, 255, rim_alpha),
                   special_flags=pygame.BLEND_RGBA_MULT)
    if glow and _BLUR is not None:
        soft = _BLUR(layer, 3)
        soft.fill((255, 255, 255, 120), special_flags=pygame.BLEND_RGBA_MULT)
        # Keep the soft light inside the silhouette.
        soft.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        out.blit(soft, (0, 0))
    out.blit(layer, (0, 0))
    return out


def _crop(surf):
    """Trim fully transparent rows; return ``(surface, y_offset)``."""
    rect = surf.get_bounding_rect(min_alpha=1)
    if rect.height == 0:
        return None, 0
    top, bottom = rect.top, rect.bottom
    out = pygame.Surface((surf.get_width(), bottom - top), pygame.SRCALPHA)
    out.blit(surf, (0, -top))
    return out, top


# ----------------------------------------------------------- silhouettes --
def _ridge_points(tw, h, spec, ridged=False, step=6, cells=5, octaves=5):
    base = spec["base"] * h
    amp = spec["amp"] * h
    seed = spec["seed"]
    pts = []
    for x in range(0, tw + step, step):
        n = fbm(x % tw, tw, seed, cells=cells, octaves=octaves,
                ridged=ridged)
        pts.append((x, base - amp * n))
    return pts


def mask_hills(tw, h, spec):
    m = _mask((tw, h))
    pts = _ridge_points(tw, h, spec, cells=3, octaves=4)
    pygame.draw.polygon(m, WHITE, pts + [(tw, h), (0, h)])
    return m


def mask_mountains(tw, h, spec):
    m = _mask((tw, h))
    pts = _ridge_points(tw, h, spec, ridged=True, cells=4, octaves=5)
    pygame.draw.polygon(m, WHITE, pts + [(tw, h), (0, h)])
    return m


def _leaf_clump(m, x, y, size, seed):
    """An irregular clump of foliage: many overlapping circles, not a cloud."""
    for k in range(16):
        a = hash01(k, seed) * math.tau
        d = size * 0.62 * hash01(k, seed + 1) ** 0.7
        r = size * (0.14 + 0.18 * hash01(k, seed + 2))
        pygame.draw.circle(m, WHITE, (int(x + math.cos(a) * d * 1.3),
                                      int(y + math.sin(a) * d * 0.8)), int(r))


def mask_forest(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    amp = spec["amp"] * h
    style = spec.get("style", "pine")

    def ground_y(x):
        return base + 8 - 14 * fbm(x % tw, tw, seed + 5, 4, 3)

    ground = [(x, ground_y(x)) for x in range(0, tw + 8, 8)]
    pygame.draw.polygon(m, WHITE, ground + [(tw, h), (0, h)])
    x = 0.0
    i = 0
    while x < tw:
        r = hash01(i, seed)
        th = amp * (0.40 + 0.60 * hash01(i, seed + 1))
        gy = ground_y(x) + 4
        if style == "pine":
            wid = 9 + 13 * r
            for cx in _wrap(x, wid * 1.2, tw):
                tiers = 5
                for k in range(tiers):
                    ty = gy - th * (k / float(tiers)) * 0.92
                    half = wid * (1.0 - k / (tiers + 0.6)) + 2
                    pygame.draw.polygon(m, WHITE, [
                        (cx - half, ty), (cx + half, ty),
                        (cx + half * 0.25, ty - th / tiers * 1.5),
                        (cx, ty - th / tiers * 1.9),
                        (cx - half * 0.25, ty - th / tiers * 1.5)])
            x += 7 + 13 * hash01(i, seed + 2)
        else:
            wid = 16 + 24 * r
            for cx in _wrap(x, wid * 1.8, tw):
                _leaf_clump(m, cx, gy - th * 0.55, wid * 1.6, seed + i * 7)
                _leaf_clump(m, cx + wid * 0.5, gy - th * 0.25, wid * 1.2,
                            seed + i * 7 + 3)
            x += 16 + 26 * hash01(i, seed + 2)
        i += 1
    return m


def mask_trunks(tw, h, spec):
    """Giant mossy trunks with root flares and a few branches."""
    m = _mask((tw, h))
    seed = spec["seed"]
    count = 5
    for i in range(count):
        cx0 = (i + 0.2 + 0.6 * hash01(i, seed)) * tw / count
        width = 40 + 60 * hash01(i, seed + 1)
        lean = (hash01(i, seed + 2) - 0.5) * 60
        for cx in _wrap(cx0, width * 2.5 + 140, tw):
            left, right = [], []
            for k in range(0, 13):
                t = k / 12.0
                y = h - t * (h + 40)
                wob = math.sin(t * 5.0 + i) * 6
                half = width * (0.5 + 0.18 * (1 - t)) / 2 + width * 0.25 * (
                    max(0.0, 0.18 - t) / 0.18) ** 2 * 2.2
                xx = cx + lean * t + wob
                left.append((xx - half, y))
                right.append((xx + half, y))
            pygame.draw.polygon(m, WHITE, left + right[::-1])
            # Root flares.
            for s in (-1, 1):
                for k in range(2):
                    rx = cx + s * width * (0.7 + 0.5 * k)
                    pygame.draw.polygon(m, WHITE, [
                        (cx + s * width * 0.2, h - 70 - 20 * k),
                        (rx, h), (cx, h)])
            # Branches with leaf clumps.
            for b in range(2):
                by = h * (0.22 + 0.22 * hash01(i * 3 + b, seed + 5))
                s = -1 if hash01(i * 3 + b, seed + 6) < 0.5 else 1
                bx = cx + lean * (1 - by / h)
                ex = bx + s * (70 + 60 * hash01(i + b, seed + 7))
                ey = by - 30 - 40 * hash01(i + b, seed + 8)
                mx = (bx + ex) / 2
                my = (by + ey) / 2 + 12
                pygame.draw.lines(m, WHITE, False,
                                  [(int(bx), int(by)), (int(mx), int(my)),
                                   (int(ex), int(ey))],
                                  max(4, int(width * 0.14)))
                _leaf_clump(m, ex, ey, 46 + 20 * hash01(i + b, seed + 11),
                            seed + i * 31 + b)
    return m


def _bark(image, mask, spec, tw, h):
    """Vertical bark grain and moss light, clipped to the trunks."""
    detail = pygame.Surface((tw, h), pygame.SRCALPHA)
    dark = tuple(max(0, c - 10) for c in spec["bottom"])
    moss = tuple(min(255, int(c * 0.5 + r * 0.5))
                 for c, r in zip(spec["top"], spec["rim"]))
    seed = spec["seed"]
    for i in range(int(tw / 9)):
        x = hash01(i, seed + 70) * tw
        y0 = hash01(i, seed + 71) * h * 0.6
        ln = 40 + 160 * hash01(i, seed + 72)
        pygame.draw.line(detail, dark + (150,), (int(x), int(y0)),
                         (int(x + 3), int(y0 + ln)), 2)
    for i in range(int(tw / 14)):
        x = hash01(i, seed + 80) * tw
        y = h * (0.3 + 0.7 * hash01(i, seed + 81))
        pygame.draw.ellipse(detail, moss + (70,), (int(x), int(y), 10, 22))
    detail.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    image.blit(detail, (0, 0))


def mask_cavewall(tw, h, spec):
    """Far cavern: a jagged ceiling and floor framing a glowing distance."""
    m = _mask((tw, h))
    seed = spec["seed"]
    top, bottom = [], []
    for x in range(0, tw + 6, 6):
        n = fbm(x % tw, tw, seed, 5, 5, ridged=True)
        top.append((x, h * (0.06 + 0.26 * n)))
        n2 = fbm(x % tw, tw, seed + 9, 6, 5, ridged=True)
        bottom.append((x, h * (0.60 + 0.22 * (1.0 - n2))))
    pygame.draw.polygon(m, WHITE, [(0, 0), (tw, 0)] + top[::-1])
    pygame.draw.polygon(m, WHITE, bottom + [(tw, h), (0, h)])
    # A few slender natural pillars joining ceiling and floor.
    for i in range(3):
        cx0 = (i + 0.3 + 0.4 * hash01(i, seed + 2)) * tw / 3
        waist = 10 + 14 * hash01(i, seed + 3)
        for cx in _wrap(cx0, 90, tw):
            left, right = [], []
            for k in range(13):
                t = k / 12.0
                y = t * h
                bulge = (abs(t - 0.5) * 2) ** 3
                half = waist + 70 * bulge + 4 * math.sin(t * 11 + i)
                left.append((cx - half, y))
                right.append((cx + half, y))
            pygame.draw.polygon(m, WHITE, left + right[::-1])
    return m


def _spike(m, x, y0, length, width, down=True, curve=0.0):
    tip = y0 + (length if down else -length)
    pts = [(x - width / 2, y0), (x + width / 2, y0),
           (x + width * 0.12 + curve, (y0 + tip) / 2), (x + curve, tip),
           (x - width * 0.12 + curve, (y0 + tip) / 2)]
    pygame.draw.polygon(m, WHITE, pts)


def mask_stalactites(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    ceil = []
    for x in range(0, tw + 8, 8):
        ceil.append((x, h * (0.06 + 0.10 * fbm(x % tw, tw, seed, 6, 3))))
    pygame.draw.polygon(m, WHITE, [(0, 0), (tw, 0)] + ceil[::-1])
    x, i = 0.0, 0
    while x < tw:
        length = h * (0.12 + 0.36 * hash01(i, seed + 1) ** 1.5)
        width = 14 + 34 * hash01(i, seed + 2)
        for cx in _wrap(x, width, tw):
            _spike(m, cx, h * 0.06, length, width, True,
                   (hash01(i, seed + 3) - 0.5) * 10)
        x += 16 + 46 * hash01(i, seed + 4)
        i += 1
    floor = []
    for x in range(0, tw + 8, 8):
        floor.append((x, h * (0.84 + 0.08 * fbm(x % tw, tw, seed + 7, 6, 3))))
    pygame.draw.polygon(m, WHITE, floor + [(tw, h), (0, h)])
    x, i = 0.0, 0
    while x < tw:
        length = h * (0.06 + 0.22 * hash01(i, seed + 11) ** 1.6)
        width = 16 + 30 * hash01(i, seed + 12)
        for cx in _wrap(x, width, tw):
            _spike(m, cx, h * 0.90, length, width, False)
        x += 30 + 70 * hash01(i, seed + 13)
        i += 1
    return m


def mask_columns(tw, h, spec):
    """Near cave: two great rock columns and a rubble floor with teeth."""
    m = _mask((tw, h))
    seed = spec["seed"]
    for i in range(2):
        cx0 = (i + 0.25 + 0.5 * hash01(i, seed)) * tw / 2
        waist = 20 + 14 * hash01(i, seed + 1)
        for cx in _wrap(cx0, 140, tw):
            left, right = [], []
            for k in range(0, 41):
                t = k / 40.0
                y = -10 + t * (h + 20)
                bulge = (abs(t - 0.5) * 2) ** 2.6
                # Rock is lumpy: two noise octaves per edge, not a lathe.
                jag_l = 26 * (fbm(t * 400, 400, seed + 10 + i, 6, 3) - 0.5)
                jag_r = 26 * (fbm(t * 400, 400, seed + 20 + i, 6, 3) - 0.5)
                half = waist + 80 * bulge
                left.append((cx - half + jag_l, y))
                right.append((cx + half * 0.9 + jag_r, y))
            pygame.draw.polygon(m, WHITE, left + right[::-1])
    floor = []
    for x in range(0, tw + 6, 6):
        floor.append((x, h * (0.90 + 0.06 * fbm(x % tw, tw, seed + 3, 8, 3))))
    pygame.draw.polygon(m, WHITE, floor + [(tw, h), (0, h)])
    # Hanging teeth from the top edge.
    x, i = 0.0, 0
    while x < tw:
        length = h * (0.06 + 0.14 * hash01(i, seed + 4) ** 1.5)
        width = 16 + 22 * hash01(i, seed + 5)
        for cx in _wrap(x, width, tw):
            _spike(m, cx, -4, length, width, True)
        x += 50 + 120 * hash01(i, seed + 6)
        i += 1
    return m


def mask_islands(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    for i in range(7):
        cx0 = (i + 0.5) * tw / 7 + (hash01(i, seed) - 0.5) * 120
        wid = 40 + 110 * hash01(i, seed + 1)
        cy = h * (spec["base"] - spec["amp"] * hash01(i, seed + 2))
        depth = wid * (0.8 + 0.6 * hash01(i, seed + 3))
        for cx in _wrap(cx0, wid * 1.3, tw):
            top = []
            for k in range(9):
                t = k / 8.0
                top.append((cx - wid + 2 * wid * t,
                            cy - 6 * math.sin(t * math.pi) - 4 * hash01(k, i)))
            bottom = []
            for k in range(9):
                t = k / 8.0
                jag = (hash01(k + i * 9, seed + 4) - 0.5) * wid * 0.25
                bottom.append((cx + wid - 2 * wid * t + jag,
                               cy + depth * math.sin(t * math.pi) ** 0.8))
            pygame.draw.polygon(m, WHITE, top + bottom)
            # A ruined tower on the larger islands.
            if wid > 90:
                tx = cx + (hash01(i, seed + 5) - 0.5) * wid
                th = 30 + 50 * hash01(i, seed + 6)
                pygame.draw.rect(m, WHITE, (int(tx - 7), int(cy - th), 14,
                                            int(th) + 4))
                pygame.draw.polygon(m, WHITE, [(tx - 10, cy - th),
                                               (tx + 10, cy - th),
                                               (tx, cy - th - 16)])
    return m


def mask_clouds(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    pygame.draw.rect(m, WHITE, (0, int(base + 20), tw, h))
    x, i = 0.0, 0
    while x < tw:
        r = 26 + 46 * hash01(i, seed)
        y = base + 24 - r * 0.6 - 18 * fbm(x % tw, tw, seed + 2, 5, 3)
        for cx in _wrap(x, r, tw):
            pygame.draw.circle(m, WHITE, (int(cx), int(y)), int(r))
        x += r * (0.7 + 0.5 * hash01(i, seed + 1))
        i += 1
    return m


def mask_spires(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    x, i = 0.0, 0
    while x < tw:
        wid = 18 + 40 * hash01(i, seed)
        th = h * spec["amp"] * (0.35 + 0.65 * hash01(i, seed + 1))
        for cx in _wrap(x, wid + 20, tw):
            top = base - th
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2), int(top), int(wid),
                                        int(h - top)))
            kind = hash01(i, seed + 2)
            if kind < 0.4:
                pygame.draw.polygon(m, WHITE, [(cx - wid / 2 - 4, top),
                                               (cx + wid / 2 + 4, top),
                                               (cx, top - wid * 1.4)])
            elif kind < 0.7:
                pygame.draw.circle(m, WHITE, (int(cx), int(top)),
                                   int(wid / 2 + 2))
                pygame.draw.line(m, WHITE, (int(cx), int(top - wid / 2)),
                                 (int(cx), int(top - wid / 2 - 14)), 2)
            else:
                # Broken crown.
                for k in range(3):
                    bx = cx - wid / 2 + k * wid / 2.5
                    pygame.draw.rect(m, WHITE, (int(bx), int(top - 6 - 8 * k % 3),
                                                int(wid / 4), 10))
            # Windows let the sky show through.
            for k in range(int(th // 34)):
                wy = top + 14 + k * 34
                pygame.draw.rect(m, (255, 255, 255, 0),
                                 (int(cx - 3), int(wy), 6, 12),
                                 border_top_left_radius=3,
                                 border_top_right_radius=3)
        # Bridge to the next spire.
        if hash01(i, seed + 3) < 0.35:
            by = base - th * 0.5
            for cx in _wrap(x, 90, tw):
                pygame.draw.rect(m, WHITE, (int(cx), int(by), 70, 8))
                pygame.draw.circle(m, (255, 255, 255, 0),
                                   (int(cx + 35), int(by + 30)), 26)
        x += wid + 30 + 90 * hash01(i, seed + 4)
        i += 1
    pygame.draw.rect(m, WHITE, (0, int(base), tw, h))
    return m


def mask_ruins(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    ground = []
    for x in range(0, tw + 8, 8):
        ground.append((x, base + 8 - 18 * fbm(x % tw, tw, seed, 5, 3)))
    pygame.draw.polygon(m, WHITE, ground + [(tw, h), (0, h)])
    x, i = 0.0, 0
    while x < tw:
        th = h * spec["amp"] * (0.4 + 0.6 * hash01(i, seed + 1))
        wid = 14 + 12 * hash01(i, seed + 2)
        broken = hash01(i, seed + 3) < 0.5
        for cx in _wrap(x, 120, tw):
            top = base - th
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2), int(top), int(wid),
                                        int(th + 10)))
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2 - 5), int(top - 6),
                                        int(wid + 10), 7))
            if broken:
                pygame.draw.polygon(m, (255, 255, 255, 0), [
                    (cx - wid, top - 8), (cx + wid, top - 8),
                    (cx + wid, top + 10), (cx - 2, top + 22)])
            elif hash01(i, seed + 4) < 0.6:
                # Arch to the right.
                span = 60 + 40 * hash01(i, seed + 5)
                rect = pygame.Rect(int(cx), int(top - span * 0.4), int(span),
                                   int(span * 0.9))
                pygame.draw.arc(m, WHITE, rect, 0.0, math.pi, 9)
        x += 70 + 120 * hash01(i, seed + 6)
        i += 1
    return m


def mask_obsidian(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    pygame.draw.rect(m, WHITE, (0, int(base), tw, h))
    x, i = 0.0, 0
    while x < tw:
        th = h * spec["amp"] * (0.3 + 0.7 * hash01(i, seed + 1) ** 1.4)
        wid = 16 + 30 * hash01(i, seed + 2)
        lean = (hash01(i, seed + 3) - 0.5) * wid * 0.8
        for cx in _wrap(x, wid + 30, tw):
            pts = [(cx - wid / 2, base + 4), (cx - wid * 0.42, base - th * 0.7),
                   (cx + lean, base - th), (cx + wid * 0.4, base - th * 0.8),
                   (cx + wid / 2, base + 4)]
            pygame.draw.polygon(m, WHITE, pts)
            # Smaller shards clustered at the foot.
            for k in range(2):
                sw = wid * 0.5
                sx = cx + (k * 2 - 1) * wid * 0.55
                sh = th * (0.25 + 0.2 * hash01(i + k, seed + 4))
                pygame.draw.polygon(m, WHITE, [(sx - sw / 2, base + 4),
                                               (sx + (k - 0.5) * 6, base - sh),
                                               (sx + sw / 2, base + 4)])
        x += wid + 20 + 70 * hash01(i, seed + 5)
        i += 1
    return m


def mask_crags(tw, h, spec):
    m = _mask((tw, h))
    pts = _ridge_points(tw, h, spec, ridged=True, step=5, cells=7, octaves=4)
    pygame.draw.polygon(m, WHITE, pts + [(tw, h), (0, h)])
    seed = spec["seed"]
    x, i = 0.0, 0
    while x < tw:
        y = spec["base"] * h - spec["amp"] * h * fbm(x % tw, tw, seed, 7, 4,
                                                     ridged=True)
        th = 30 + 60 * hash01(i, seed + 1)
        for cx in _wrap(x, 30, tw):
            pygame.draw.polygon(m, WHITE, [(cx - 10, y + 8), (cx + 3, y - th),
                                           (cx + 12, y + 8)])
        x += 90 + 160 * hash01(i, seed + 2)
        i += 1
    return m


def mask_vault(tw, h, spec):
    m = _mask((tw, h))
    seed = spec["seed"]
    base = spec["base"] * h
    pygame.draw.rect(m, WHITE, (0, int(base), tw, h))
    pillars = 6
    step = tw / float(pillars)
    for i in range(pillars):
        cx0 = (i + 0.5) * step
        wid = 30 + 16 * hash01(i, seed)
        top = h * 0.08
        for cx in _wrap(cx0, step, tw):
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2), int(top), int(wid),
                                        int(h)))
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2 - 8), int(base - 14),
                                        int(wid + 16), 16))
            pygame.draw.rect(m, WHITE, (int(cx - wid / 2 - 8), int(top + 20),
                                        int(wid + 16), 12))
            # Arch to the next pillar.
            rect = pygame.Rect(int(cx), int(top + 26), int(step),
                               int(step * 0.7))
            pygame.draw.arc(m, WHITE, rect, 0.0, math.pi, 14)
            # Hanging chain.
            if hash01(i, seed + 1) < 0.7:
                chx = cx + step * (0.3 + 0.4 * hash01(i, seed + 2))
                ln = h * (0.2 + 0.25 * hash01(i, seed + 3))
                for k in range(int(ln // 9)):
                    cy = top + 40 + k * 9
                    pygame.draw.ellipse(m, WHITE, (int(chx - 3 + (k % 2)),
                                                   int(cy), 6, 10), 2)
    pygame.draw.rect(m, WHITE, (0, 0, tw, int(h * 0.08)))
    return m


MASKS = {
    "hills": mask_hills, "mountains": mask_mountains,
    "forest": mask_forest, "trunks": mask_trunks,
    "cavewall": mask_cavewall, "stalactites": mask_stalactites,
    "columns": mask_columns, "islands": mask_islands,
    "clouds": mask_clouds, "spires": mask_spires, "ruins": mask_ruins,
    "obsidian": mask_obsidian, "crags": mask_crags, "vault": mask_vault,
}


# ------------------------------------------------------------------ gears --
def gear_mask(radius, teeth, seed):
    """A clockwork gear: rim, teeth, spokes and a hub (white alpha mask)."""
    size = int(radius * 2 + 8)
    m = _mask((size, size))
    c = size / 2.0
    tooth = radius * 0.16
    pts = []
    for k in range(teeth * 4):
        a = k / float(teeth * 4) * math.tau
        r = radius if (k % 4) in (1, 2) else radius - tooth
        pts.append((c + math.cos(a) * r, c + math.sin(a) * r))
    pygame.draw.polygon(m, WHITE, pts)
    clear = (255, 255, 255, 0)
    inner = radius - tooth - radius * 0.14
    pygame.draw.circle(m, clear, (int(c), int(c)), int(inner))
    spokes = 4 + int(hash01(seed, 3) * 3)
    for k in range(spokes):
        a = k / float(spokes) * math.tau
        pygame.draw.line(m, WHITE, (int(c), int(c)),
                         (int(c + math.cos(a) * inner), int(c + math.sin(a) * inner)),
                         max(3, int(radius * 0.12)))
    pygame.draw.circle(m, WHITE, (int(c), int(c)), int(radius * 0.22))
    pygame.draw.circle(m, clear, (int(c), int(c)), int(radius * 0.08))
    return m


class GearLayer:
    """Slowly turning gears: pre-rendered frames over one tooth period."""

    FRAMES = 8

    def __init__(self, spec, h, tw=TILE, budget=1.0):
        self.spec = spec
        self.factor = spec["factor"]
        self.spin = spec.get("spin", 0.08)
        self.tw = tw
        self.gears = []
        seed = spec["seed"]
        count = 4
        for i in range(count):
            radius = int(min(130, (60 + 100 * hash01(i, seed))
                             * (0.6 + 0.4 * spec["amp"] / 0.45)))
            teeth = 10 + int(hash01(i, seed + 1) * 8)
            cx = (i + 0.5) * tw / count + (hash01(i, seed + 2) - 0.5) * 120
            cy = h * spec["base"] - radius * (0.2 + 0.7 * hash01(i, seed + 3))
            mask = gear_mask(radius, teeth, seed + i)
            base = colorize(mask, spec["top"], spec["bottom"], spec["rim"],
                            rim_w=3, rim_alpha=150)
            if spec.get("blur") and _BLUR is not None:
                base = _BLUR(base, int(spec["blur"]))
            frames = []
            period = 360.0 / teeth
            n = max(1, int(self.FRAMES * budget))
            for f in range(n):
                frames.append(fx.accel(pygame.transform.rotozoom(
                    base, -period * f / n, 1.0)))
            self.gears.append(dict(x=cx, y=cy, r=radius, frames=frames,
                                   period=period, dirn=1 if i % 2 else -1))

    def draw(self, surf, cam_px, t):
        off = (cam_px * self.factor) % self.tw
        # Clockwork ticks: one frame step per beat, like an escapement.
        beat = 0.55 if abs(self.spin) < 0.1 else 0.32
        ticks = int(t / beat)
        for g in self.gears:
            frames = g["frames"]
            n = len(frames)
            img = frames[(ticks * g["dirn"]) % n]
            iw, ih = img.get_size()
            for x in (g["x"] - off, g["x"] - off + self.tw):
                sx = int(x - iw / 2)
                if sx > W or sx + iw < 0:
                    continue
                surf.blit(img, (sx, int(g["y"] - ih / 2)))


# ----------------------------------------------------------------- layers --
class Layer:
    def __init__(self, image, y, factor, tw):
        self.image = fx.accel(image)
        self.y = y
        self.factor = factor
        self.tw = tw

    def draw(self, surf, cam_px, t=0.0, drift=0.0):
        if self.image is None:
            return
        off = int((cam_px * self.factor + t * drift) % self.tw)
        surf.blit(self.image, (-off, self.y))
        if self.tw - off < W:
            surf.blit(self.image, (self.tw - off, self.y))


def build_layer(spec, tw=TILE, h=H):
    kind = spec["kind"]
    if kind == "gears":
        return GearLayer(spec, h, tw)
    mask = MASKS[kind](tw, h, spec)
    side = spec.get("side", 0)
    blur = int(spec.get("blur", 0))
    # Far layers are shapes in the haze, not line drawings: their rim is a
    # faint lift; near layers catch a crisp, glowing edge of backlight.
    rim_alpha = 0 if blur >= 2 else (130 if blur == 1 else 185)
    rim = spec.get("rim") if rim_alpha else None
    if side and rim is not None:
        # A low sun lights the faces turned toward it; the top edge only gets
        # a faint lift, so leaf clumps do not grow a cartoon outline.
        rim_alpha = int(rim_alpha * 0.8)
    image = colorize(mask, spec["top"], spec["bottom"], rim,
                     rim_w=2 if blur >= 2 else 3, rim_alpha=rim_alpha,
                     side=side, glow=blur <= 1)
    if kind == "trunks":
        _bark(image, mask, spec, tw, h)
    if spec.get("crystals"):
        _sprinkle_crystals(image, mask, spec, tw, h)
    blur = int(spec.get("blur", 0))
    if blur and _BLUR is not None:
        image = _BLUR(image, blur)
    image, y = _crop(image)
    return Layer(image, y, spec["factor"], tw)


def crystal_cluster(surf, x, y, size, colour, up=True, seed=0):
    """Small faceted crystal cluster with a lit face (drawn in place)."""
    dark = _lerp(colour, (10, 10, 30), 0.55)
    lit = _lerp(colour, (255, 255, 255), 0.5)
    sgn = -1 if up else 1
    for k in range(3):
        ang = (hash01(k, seed) - 0.5) * 0.9 + (k - 1) * 0.35
        ln = size * (0.6 + 0.6 * hash01(k, seed + 1)) * (1.2 if k == 1 else 0.8)
        wd = size * 0.28
        tipx = x + math.sin(ang) * ln
        tipy = y + sgn * math.cos(ang) * ln
        bx = x + (k - 1) * wd * 0.6
        pts = [(bx - wd / 2, y), (tipx - wd * 0.15, tipy + sgn * -wd * 0.4),
               (tipx, tipy), (bx + wd / 2, y)]
        pygame.draw.polygon(surf, dark, pts)
        pygame.draw.polygon(surf, colour, [pts[0], pts[1], pts[2],
                                           ((pts[0][0] + pts[3][0]) / 2, y)])
        pygame.draw.line(surf, lit, (int(pts[1][0]), int(pts[1][1])),
                         (int(tipx), int(tipy)), 1)


def _sprinkle_crystals(image, mask, spec, tw, h):
    """Glowing crystal clusters along the silhouette's upper edges."""
    seed = spec["seed"]
    cols = spec["crystals"]
    glow = pygame.Surface(image.get_size())
    glow.fill((0, 0, 0))
    count = 16
    for i in range(count):
        x = (i + hash01(i, seed + 40)) * tw / count
        # Crystals grow on the floor: scan up from the bottom for the first
        # open pixel, so a ceiling mass cannot hide them under the top edge.
        xi = int(x) % tw
        y_edge = None
        for y in range(h - 1, 0, -3):
            if mask.get_at((xi, y)).a < 128:
                y_edge = y + 3
                break
        if y_edge is None or y_edge > h - 8 or y_edge < h * 0.3:
            continue
        col = _lerp(cols[i % len(cols)], spec["top"], 0.38)
        size = 10 + 14 * hash01(i, seed + 41)
        for cx in _wrap(x, size, tw):
            crystal_cluster(image, cx, y_edge + 4, size, col, True, seed + i)
            light(glow, (cx, y_edge - size * 0.3), int(size * 6), col, 2.2, 0.42)
    image.blit(glow, (0, 0), special_flags=pygame.BLEND_RGB_ADD)


# --------------------------------------------------------------------- sky --
def build_sky(theme, size=(W, H), stars_on=True, span=None):
    """The painted sky.  ``span`` widens it into a seamless panorama while
    sun, glows and stars keep their positions inside the first ``W`` px."""
    lk = look(theme)
    w, h = size
    if span is not None and span > w:
        sky = gradient((span, h), lk["sky"])
    else:
        sky = gradient(size, lk["sky"])
    sw = sky.get_width()

    def wrapped(x, reach):
        # A panorama tiles: a light near one edge is painted on the other too.
        out = [x]
        if sw > w:
            if x - reach < 0:
                out.append(x + sw)
            if x + reach > sw:
                out.append(x - sw)
        return out

    for (pos, diam, col, k) in lk.get("glows", ()):
        for gx in wrapped(pos[0] * w, diam / 2):
            light(sky, (gx, pos[1] * h), diam, col, 1.6, k)
    aurora = lk.get("aurora")
    if aurora:
        band = pygame.Surface((w // 4, h // 4))
        band.fill((0, 0, 0))
        for k, col in enumerate(aurora):
            pts = []
            for x in range(0, w // 4 + 8, 8):
                y = h / 4 * (0.16 + 0.07 * k) + 9 * math.sin(x * 0.02 + k * 2)
                pts.append((x, y))
            dim = tuple(int(c * 0.30) for c in col)
            pygame.draw.lines(band, dim, False, pts, 7)
        if _BLUR is not None:
            band = _BLUR(band, 4)
        band = pygame.transform.smoothscale(band, (w, h))
        sky.blit(band, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    if stars_on and lk.get("stars"):
        n = lk["stars"]
        sw = sky.get_width()
        for i in range(int(n * sw / w)):
            x = int(hash01(i, 501) * sw)
            y = int(hash01(i, 502) ** 1.4 * h * 0.62)
            b = 0.25 + 0.75 * hash01(i, 503) ** 2
            c = tuple(int(v * b) for v in (210, 220, 255))
            if b > 0.8:
                light(sky, (x, y), 9, c, 2.2, 1.0)
            sky.fill(c, (x, y, 1 + (b > 0.7), 1 + (b > 0.7)))
    src = lk.get("light")
    if src:
        cx, cy = src["x"] * w, src["y"] * h
        for gx in wrapped(cx, src["glow_r"] * 1.2):
            light(sky, (gx, cy), src["glow_r"] * 2.4, src["glow"], 2.6,
                  src["glow_a"] * 0.55)
            light(sky, (gx, cy), src["glow_r"], src["glow"], 2.0, src["glow_a"])
        d = src["r"] * 2
        if src["kind"] == "moon":
            body = fx.sphere(d, src["core"], src["edge"],
                             light=(-0.45, -0.5, 0.75), ambient=0.36,
                             maria=True)
        else:
            body = fx.sphere(d, src["core"], src["edge"],
                             light=(0.0, 0.0, 1.0), ambient=0.80)
        sky.blit(body, (int(cx - d / 2), int(cy - d / 2)))
        light(sky, (cx, cy), int(d * 1.6), src["core"], 1.4, 0.45)
    return sky


# -------------------------------------------------------------------- rays --
def build_rays(theme, size=(W, H)):
    lk = look(theme)
    spec = lk.get("rays")
    if not spec:
        return None
    w, h = size
    qw, qh = (w + 400) // 4, h // 4
    acc = pygame.Surface((qw, qh))
    acc.fill((0, 0, 0))
    sx = (spec["source"][0] * w + 200) / 4.0
    sy = spec["source"][1] * h / 4.0
    col = spec["colour"]
    strength = spec["strength"]
    seed = int(sum(col)) + spec["count"]
    for i in range(spec["count"]):
        frac = (i + 0.5) / spec["count"]
        ang = math.pi / 2 + (frac - 0.5) * spec["spread"] * 2 + \
            (hash01(i, seed) - 0.5) * 0.12
        if spec["source"][0] > 0.5:
            ang += 0.18
        else:
            ang -= 0.18
        length = qh * 1.6
        w0 = 1.0 + 2 * hash01(i, seed + 1)
        w1 = 8.0 + 18 * hash01(i, seed + 2)
        inten = 0.45 + 0.55 * hash01(i, seed + 3)
        dx, dy = math.cos(ang), math.sin(ang)
        px, py = -dy, dx
        segs = 10
        beam = pygame.Surface((qw, qh))
        beam.fill((0, 0, 0))
        for s in range(segs):
            t0, t1 = s / segs, (s + 1) / segs
            k = inten * (1.0 - t0) ** 1.3 * (min(1.0, t0 * 5 + 0.2))
            c = tuple(int(min(255, v * k * strength / 60.0)) for v in col)
            a0, a1 = w0 + (w1 - w0) * t0, w0 + (w1 - w0) * t1
            x0, y0 = sx + dx * length * t0, sy + dy * length * t0
            x1, y1 = sx + dx * length * t1, sy + dy * length * t1
            pygame.draw.polygon(beam, c, [(x0 - px * a0, y0 - py * a0),
                                          (x0 + px * a0, y0 + py * a0),
                                          (x1 + px * a1, y1 + py * a1),
                                          (x1 - px * a1, y1 - py * a1)])
        acc.blit(beam, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    if _BLUR is not None:
        acc = _BLUR(acc, 3)
    # Crop to the columns that actually carry light: an additive blit still
    # pays for every black pixel it touches.
    lo, hi = qw, 0
    for x in range(qw):
        for y in range(0, qh, 2):
            if sum(acc.get_at((x, y))[:3]) > 6:
                lo = min(lo, x)
                hi = max(hi, x)
                break
    if hi <= lo:
        return None
    lo, hi = max(0, lo - 1), min(qw, hi + 2)
    acc = acc.subsurface((lo, 0, hi - lo, qh)).copy()
    rays = pygame.transform.smoothscale(acc, ((hi - lo) * 4, qh * 4))
    return rays, lo * 4 - 200


# --------------------------------------------------------------------- fog --
def build_fog(theme, tw=TILE, size=(W, H)):
    lk = look(theme)
    spec = lk.get("fog")
    if not spec:
        return None
    w, h = size
    fh = int(spec["h"] * h)
    qw, qh = tw // 4, max(8, fh // 4)
    m = _mask((qw, qh))
    seed = int(spec["y"] * 1000)
    for i in range(70):
        x = hash01(i, seed) * qw
        y = qh * (0.35 + 0.55 * hash01(i, seed + 1))
        rw = qw * (0.04 + 0.10 * hash01(i, seed + 2))
        rh = qh * (0.18 + 0.30 * hash01(i, seed + 3))
        a = int(60 + 120 * hash01(i, seed + 4))
        for cx in _wrap(x, rw, qw):
            pygame.draw.ellipse(m, (255, 255, 255, a),
                                (int(cx - rw), int(y - rh), int(rw * 2),
                                 int(rh * 2)))
    # Dense floor.
    for y in range(qh):
        t = y / float(max(1, qh - 1))
        a = int(255 * max(0.0, (t - 0.45) / 0.55) ** 1.5 * 0.8)
        if a:
            band = pygame.Surface((qw, 1), pygame.SRCALPHA)
            band.fill((255, 255, 255, a))
            m.blit(band, (0, y), special_flags=pygame.BLEND_RGBA_MAX)
    if _BLUR is not None:
        m = _BLUR(m, 3)
    m = pygame.transform.smoothscale(m, (tw, fh))
    m.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    # Feather the band's top so the mist rises out of nothing: a clipped
    # blob at the strip's edge read as a hard horizontal line.
    ramp = pygame.Surface((tw, fh), pygame.SRCALPHA)
    for y in range(fh):
        t = y / float(max(1, fh - 1))
        a = int(255 * min(1.0, t / 0.45) ** 1.6)
        ramp.fill((255, 255, 255, a), pygame.Rect(0, y, tw, 1))
    m.blit(ramp, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    out = pygame.Surface((tw, fh), pygame.SRCALPHA)
    out.fill(tuple(spec["colour"]) + (255,))
    out.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    out.fill((255, 255, 255, spec["alpha"]),
             special_flags=pygame.BLEND_RGBA_MULT)
    return Layer(out, int(spec["y"] * h), spec["factor"], tw)


# -------------------------------------------------------- framing layers --
def build_canopy(theme, tw=TILE):
    lk = look(theme)
    spec = lk.get("canopy")
    if not spec:
        return None
    depth = spec["depth"]
    hgt = depth + 60
    m = _mask((tw, hgt))
    seed = 313
    style = spec.get("style", "leaves")
    if style == "stone":
        pts = [(0, 0)]
        for x in range(0, tw + 6, 6):
            pts.append((x, depth * 0.45 + depth * 0.4 * fbm(x, tw, seed, 9, 3,
                                                            ridged=True)))
        pts.append((tw, 0))
        pygame.draw.polygon(m, WHITE, pts)
        x, i = 0.0, 0
        while x < tw:
            ln = 18 + 50 * hash01(i, seed + 1) ** 2
            wd = 8 + 16 * hash01(i, seed + 2)
            for cx in _wrap(x, wd, tw):
                _spike(m, cx, depth * 0.5, ln, wd, True)
            x += 20 + 50 * hash01(i, seed + 3)
            i += 1
    else:
        pygame.draw.rect(m, WHITE, (0, 0, tw, int(depth * 0.25)))
        x, i = 0.0, 0
        while x < tw:
            r = 10 + 20 * hash01(i, seed)
            y = depth * 0.25 + (hash01(i, seed + 1) - 0.3) * depth * 0.5
            for cx in _wrap(x, r, tw):
                pygame.draw.circle(m, WHITE, (int(cx), int(y)), int(r))
            x += r * 0.9
            i += 1
        # Hanging vines with leaves.
        for i in range(26):
            vx = hash01(i, seed + 5) * tw
            ln = depth * 0.4 + 60 * hash01(i, seed + 6)
            for cx in _wrap(vx, 20, tw):
                pts = []
                for k in range(8):
                    t = k / 7.0
                    pts.append((cx + math.sin(t * 3 + i) * 5, depth * 0.3 + t * ln))
                pygame.draw.lines(m, WHITE, False, pts, 2)
                for k in range(1, 7):
                    lx, ly = pts[k]
                    s = -1 if k % 2 else 1
                    pygame.draw.ellipse(m, WHITE, (int(lx + (s - 1) * 4),
                                                   int(ly - 3), 8, 5))
    img = colorize(m, spec["colour"], spec["colour"], None)
    if _BLUR is not None:
        img = _BLUR(img, 1)
    img, y = _crop(img)
    return Layer(img, y, spec["factor"], tw)


def build_foreground(theme, tw=W):
    lk = look(theme)
    spec = lk.get("foreground")
    if not spec:
        return None
    hgt = spec["height"] + 50
    m = _mask((tw, hgt))
    base = hgt - 6
    style = spec.get("style", "ferns")
    seed = 911
    x, i = 0.0, 0
    while x < tw:
        cluster = hash01(i, seed) < 0.80
        if cluster:
            n = 4 + int(5 * hash01(i, seed + 1))
            tall = spec["height"] * (0.5 + 0.9 * hash01(i, seed + 2))
            for k in range(n):
                bx = x + (k - n / 2) * 4
                lean = (hash01(i * 13 + k, seed + 3) - 0.5) * 34
                bh = tall * (0.45 + 0.55 * hash01(i * 17 + k, seed + 4))
                for cx in _wrap(bx, 40, tw):
                    if style in ("ferns", "grass"):
                        pts = [(cx - 3, base), (cx + lean * 0.5, base - bh * 0.6),
                               (cx + lean, base - bh), (cx + 3, base)]
                        pygame.draw.polygon(m, WHITE, pts)
                        if style == "ferns" and k % 2 == 0:
                            for f in range(1, 6):
                                t = f / 6.0
                                fx_ = cx + lean * t * 0.9
                                fy_ = base - bh * t
                                pygame.draw.line(m, WHITE, (int(fx_), int(fy_)),
                                                 (int(fx_ + 9 * (1 - t) + 3),
                                                  int(fy_ + 4)), 2)
                                pygame.draw.line(m, WHITE, (int(fx_), int(fy_)),
                                                 (int(fx_ - 9 * (1 - t) - 3),
                                                  int(fy_ + 4)), 2)
                    elif style == "shards":
                        pygame.draw.polygon(m, WHITE, [
                            (cx - 7, base + 4), (cx + lean * 0.4, base - bh),
                            (cx + 8, base + 4)])
                    else:  # cogs: debris and gear teeth
                        if k == 0:
                            r = int(10 + bh * 0.3)
                            pygame.draw.circle(m, WHITE, (int(cx), base), r)
                            for tth in range(10):
                                a = tth / 10.0 * math.tau
                                pygame.draw.circle(
                                    m, WHITE, (int(cx + math.cos(a) * r),
                                               int(base + math.sin(a) * r)), 4)
                        else:
                            pygame.draw.rect(m, WHITE, (int(cx), int(base - bh * 0.4),
                                                        5, int(bh * 0.4)))
            x += 40 + 120 * hash01(i, seed + 5)
        else:
            r = 6 + 12 * hash01(i, seed + 6)
            for cx in _wrap(x, r * 2, tw):
                pygame.draw.ellipse(m, WHITE, (int(cx - r * 1.4), int(base - r * 0.7),
                                               int(r * 2.8), int(r * 1.6)))
            x += 60 + 140 * hash01(i, seed + 7)
        i += 1
    pygame.draw.rect(m, WHITE, (0, base - 2, tw, hgt))
    img = colorize(m, spec["colour"], spec["colour"], None)
    if _BLUR is not None:
        img = _BLUR(img, 2)
    img, y = _crop(img)
    return Layer(img, H - hgt + y + 14, spec["factor"], tw)


# ------------------------------------------------------------------ motes --
class Motes:
    """Stateless ambient particles: fireflies, spores, motes, petals, dust.

    Positions are pure functions of the cosmetic clock and the camera, so
    there is no per-particle state to save, reset or budget.
    """

    def __init__(self, theme, count_scale=1.0):
        lk = look(theme)
        spec = lk.get("motes") or {"kind": "dust", "colours": ((255, 255, 255),),
                                   "count": 0}
        self.kind = spec["kind"]
        self.colours = spec["colours"]
        self.count = int(spec["count"] * count_scale)
        self.span = W + 200
        seed = 4242
        self.items = []
        for i in range(self.count):
            depth = 0.35 + 0.9 * hash01(i, seed + 2)
            col = self.colours[i % len(self.colours)]
            size = int(8 + 10 * hash01(i, seed + 5) + 10 * depth)
            sprites = tuple(add_glow(size, col, 2.4, 0.25 + 0.19 * b)
                            for b in range(5))
            core = _lerp(col, (255, 255, 255), 0.5)
            self.items.append((
                hash01(i, seed) * self.span,
                70 + hash01(i, seed + 1) * (H - 110),
                depth,
                hash01(i, seed + 3) * math.tau,           # phase
                0.4 + 0.8 * hash01(i, seed + 4),          # speed
                sprites, size // 2, core))

    def draw(self, surf, cam_px, t, strength=1.0):
        span = self.span
        kind = self.kind
        add = pygame.BLEND_RGB_ADD
        for (bx, by, depth, ph, spd, sprites, half, core) in self.items:
            if kind == "fireflies":
                x = bx + math.sin(t * 0.5 * spd + ph) * 40 + t * 6 * spd
                y = by + math.sin(t * 0.8 * spd + ph * 2) * 22
                pulse = max(0.0, math.sin(t * 1.7 * spd + ph)) ** 2
            elif kind == "petals":
                x = bx + t * 22 * spd + math.sin(t * 1.3 + ph) * 18
                y = (by + t * 12 * spd) % (H - 60) + 60
                pulse = 0.55 + 0.45 * math.sin(t * 2.0 + ph)
            elif kind == "spores":
                x = bx + math.sin(t * 0.4 + ph) * 30 + t * 4 * spd
                y = (by - t * 9 * spd) % (H - 60) + 60
                pulse = 0.5 + 0.5 * math.sin(t * 2.2 * spd + ph)
            else:  # motes / dust
                x = bx + math.sin(t * 0.3 * spd + ph) * 26 + t * 3 * spd
                y = by + math.sin(t * 0.5 * spd + ph) * 30
                pulse = 0.45 + 0.55 * math.sin(t * 1.4 * spd + ph) ** 2
            sx = (x - cam_px * depth) % span - 100
            if sx < -20 or sx > W + 20:
                continue
            k = pulse * strength * (0.5 + 0.5 * min(1.0, depth))
            if k < 0.06:
                continue
            bucket = int(k * 4.99)
            surf.blit(sprites[bucket], (int(sx) - half, int(y) - half),
                      special_flags=add)
            if bucket >= 3:
                surf.fill(core, (int(sx), int(y), 2, 2))


# ------------------------------------------------------------------ world --
class Scenery:
    """Everything behind and in front of the playfield for one world."""

    def __init__(self, theme, quality=1, web=False):
        self.theme = theme
        lk = look(theme)
        self.look = lk
        self.quality = quality
        self.web = web
        rich = quality and not web
        specs = list(lk["layers"])
        if not quality:
            # Low graphics keeps the farthest and nearest silhouettes only.
            specs = [specs[0], specs[-1]]
        elif web and len(specs) > 3:
            specs = specs[:1] + specs[2:]
        # The farthest layer barely moves (<= 7 % of the camera), so it is
        # painted into a seamless sky panorama: one opaque blit per frame
        # instead of an opaque sky plus a full-width alpha layer.
        self.sky_factor = 0.0
        self.sky = build_sky(theme)
        self.panorama = None
        if specs and specs[0]["kind"] != "gears" and specs[0]["factor"] <= 0.07:
            far = specs.pop(0)
            self.sky_factor = far["factor"]
            pano = build_sky(theme, span=TILE)
            layer = build_layer(far)
            if layer.image is not None:
                pano.blit(layer.image, (0, layer.y))
            self.panorama = pano
        self.layers = [build_layer(s) for s in specs]
        rays = build_rays(theme) if quality else None
        self.rays, self.rays_x = rays if rays else (None, 0)
        self.fog = build_fog(theme) if quality else None
        self.canopy = build_canopy(theme) if rich else None
        self.front = build_foreground(theme) if quality else None
        self.motes = Motes(theme, 1.0 if rich else (0.35 if quality else 0.0))
        self.ambient = lk.get("ambient")
        self.grade = lk.get("grade")
        src = lk.get("rays") or {}
        self.ray_dir = 1 if src.get("source", (0.5, 0))[0] > 0.5 else -1
        if web:
            self._bake_for_web()

    def _bake_for_web(self):
        """Fold the full-width passes into the panorama for the browser.

        Under wasm every blended pixel is paid in scalar code, so the light
        shafts (an additive pass ~1,000 px wide), the mist band (a 1600 px
        alpha blend) and the colour grade (a full-screen add) are painted into
        the scrolling sky once instead of every frame.  They lose their sway
        and drift and sit behind the silhouettes; the desktop keeps them live.
        """
        if self.panorama is None:
            self.panorama = build_sky(self.theme, span=TILE)
        if self.rays is not None:
            # The panorama tiles, so a shaft crossing either edge is painted
            # on the other side as well.
            for ox in (-TILE, 0, TILE):
                self.panorama.blit(self.rays, (self.rays_x - 30 + ox, 0),
                                   special_flags=pygame.BLEND_RGB_ADD)
            self.rays = None
        if self.fog is not None and self.fog.image is not None:
            self.panorama.blit(self.fog.image, (0, self.fog.y))
            self.fog = None
        if self.grade:
            self.panorama.fill(self.grade, special_flags=pygame.BLEND_RGB_ADD)
            self.grade = None

    def draw_sky(self, surf, cam_px):
        if self.panorama is None:
            surf.blit(self.sky, (0, 0))
            return
        off = int(cam_px * self.sky_factor) % TILE
        surf.blit(self.panorama, (-off, 0))
        if TILE - off < W:
            surf.blit(self.panorama, (TILE - off, 0))

    def draw_back(self, surf, cam_px, t):
        self.draw_sky(surf, cam_px)
        n = len(self.layers)
        for i, layer in enumerate(self.layers):
            layer.draw(surf, cam_px, t)
            if i == max(0, n - 2) and self.rays is not None:
                sway = math.sin(t * 0.21) * 22
                off = self.rays_x + int(cam_px * 0.04) % 60 + sway
                surf.blit(self.rays, (int(off) - 30, 0),
                          special_flags=pygame.BLEND_RGB_ADD)
        if self.fog is not None:
            self.fog.draw(surf, cam_px, t, drift=self.look["fog"]["drift"])

    def draw_motes(self, surf, cam_px, t):
        if self.motes.count:
            self.motes.draw(surf, cam_px, t)

    def draw_front(self, surf, cam_px, t, canopy=True):
        if self.front is not None:
            self.front.draw(surf, cam_px)
        if canopy and self.canopy is not None:
            self.canopy.draw(surf, cam_px)


_SCENES = {}


def scenery(theme, quality=1, web=False):
    """Cached :class:`Scenery`; keeps the two most recent worlds."""
    key = (theme, int(bool(quality)), bool(web))
    sc = _SCENES.get(key)
    if sc is None:
        if len(_SCENES) >= 2:
            _SCENES.pop(next(iter(_SCENES)))
        sc = Scenery(theme, quality, web)
        _SCENES[key] = sc
    else:
        _SCENES.pop(key)
        _SCENES[key] = sc
    return sc


def forget():
    _SCENES.clear()
