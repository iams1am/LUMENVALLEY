"""Atmospheric and chrome effects for the pygame front-end.

The browser/desktop renderer used flat fills, 4 px sky bands and hard-edged
polygons, which reads as 8-bit chrome rather than a modern release.  Everything
here is still drawn procedurally (no imported art), but at a higher quality
level: smooth gradients, soft radial light, blurred billboards and a vignette.

Two rules keep it cheap and predictable:

* every expensive surface is built once per (key) and cached, so a frame only
  blits cached surfaces;
* nothing here touches the simulation, the theme tables or the palette roles
  that ``tools/check_visual.py`` audits — these are additive effects on top.

Pure pygame; the terminal renderer never imports this module.
"""

import math

import pygame

_CACHE = {}

# Masks are authored small and smooth-scaled up.  Scaling is the cheapest blur
# available in pygame, and at these sizes the seams are invisible.
_MASK = 24


def _cached(key, builder):
    surf = _CACHE.get(key)
    if surf is None:
        surf = builder()
        _CACHE[key] = surf
    return surf


def vertical_gradient(size, stops, dither=True):
    """Vertical gradient across ``size`` from ``stops`` (top to bottom).

    Rows are filled one at a time.  A 1-value dither breaks the visible banding
    that a 20 px-step fill produced on dark skies; it costs one add per row and
    is baked into the cached surface.
    """
    width, height = size
    key = ("grad", width, height, tuple(tuple(c) for c in stops), dither)

    def build():
        surf = pygame.Surface((width, height))
        n = len(stops)
        rows = []
        for y in range(height):
            t = y / float(max(1, height - 1))
            pos = t * (n - 1)
            i = min(n - 2, int(pos))
            f = pos - i
            a, b = stops[i], stops[i + 1]
            rows.append((a[0] + (b[0] - a[0]) * f,
                         a[1] + (b[1] - a[1]) * f,
                         a[2] + (b[2] - a[2]) * f))
        # The theme tables are coarse (a dozen hand-picked sky values), so a
        # straight interpolation leaves a visible seam at every stop.  A short
        # box blur across rows turns those seams into a gradient.  The radius
        # scales with the surface, so a 480 px sky gets a soft transition where
        # the 20-row HUD strip deliberately keeps its two-tone chrome.
        radius = max(1, height // 36)
        if radius > 1:
            smooth = []
            for y in range(height):
                lo, hi = max(0, y - radius), min(height, y + radius + 1)
                span = hi - lo
                smooth.append(tuple(
                    sum(rows[k][c] for k in range(lo, hi)) / span
                    for c in range(3)))
            rows = smooth
        for y, row in enumerate(rows):
            c = [int(v) for v in row]
            if dither:
                d = 1 if y % 2 else -1
                c = [max(0, min(255, v + d)) for v in c]
            surf.fill(tuple(c), pygame.Rect(0, y, width, 1))
        return surf

    return _cached(key, build)


def panel_gradient(size, top=(28, 29, 38), bottom=(14, 15, 20), alpha=244):
    """Near-opaque dark panel fill with a vertical lift.

    A flat black rectangle under a one-pixel border reads as a prototype
    dialog.  The gradient is only a few values apart, so it keeps the neutral
    chrome the art bible asks for while giving the panel a surface.  It is
    cached per size, and the alpha is baked in so a frame is one blit.
    """
    key = ("panel", size, tuple(top), tuple(bottom), alpha)

    def build():
        surf = vertical_gradient(size, [tuple(top), tuple(bottom)]).copy()
        surf.set_alpha(alpha)
        return surf

    return _cached(key, build)


def sky_gradient(size, stops, haze_colour=(180, 200, 230), haze_alpha=46):
    """Gradient with the horizon haze baked in: one blit instead of two.

    The sky is repainted every frame, and compositing two full-screen layers
    (gradient plus alpha haze) cost ~0.5 ms of a 16.7 ms budget.  Building them
    into one cached surface once per theme halves that.
    """
    width, height = size
    key = ("sky", width, height, tuple(tuple(c) for c in stops),
           tuple(haze_colour), haze_alpha)

    def build():
        surf = vertical_gradient(size, stops).copy()
        surf.blit(haze(size, haze_colour, 0, haze_alpha), (0, 0))
        return surf

    return _cached(key, build)


def vignette_bands(size, band=120, edge=(6, 6, 10), strength=110):
    """Top and bottom darkening bands, returned as one pair.

    A full-frame vignette alpha-blends every pixel; most of the effect lives in
    the first hundred pixels from each edge, so two bands buy the same mood for
    roughly half the fill rate.
    """
    width, height = size
    band = max(8, min(band, height // 2))
    key = ("vigband", width, height, band, tuple(edge), strength)

    def build():
        mask = pygame.Surface((_MASK, _MASK), pygame.SRCALPHA)
        for y in range(_MASK):
            t = y / float(_MASK - 1)
            a = int(strength * (1.0 - t) ** 2.2)
            if a > 0:
                pygame.draw.line(mask, tuple(edge) + (a,),
                                 (0, y), (_MASK, y))
        ramp = pygame.transform.smoothscale(mask, (width, band))
        top = ramp
        bottom = pygame.transform.flip(ramp, False, True)
        return (top, bottom)

    return _cached(key, build)


def radial(diameter, power=1.8, colour=(255, 255, 255), alpha=255):
    """A soft radial falloff, built small and smooth-scaled to *diameter*."""
    key = ("radial", diameter, power, tuple(colour), alpha)

    def build():
        # Scale a cached white falloff mask, then tint colour and alpha with
        # two C-level multiplies (a miss used to loop 576 pixels in Python).
        img = pygame.transform.smoothscale(_radial_mask(power),
                                           (diameter, diameter))
        img.fill(tuple(colour) + (255,), special_flags=pygame.BLEND_RGBA_MULT)
        if alpha < 255:
            img.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        return img

    return _cached(key, build)


def _radial_mask(power):
    """White mask with alpha ``255 * (1 - d) ** power`` (cached per power)."""
    key = ("radialmask", power)

    def build():
        mask = pygame.Surface((_MASK, _MASK), pygame.SRCALPHA)
        mask.fill((255, 255, 255, 0))
        c = (_MASK - 1) / 2.0
        for y in range(_MASK):
            for x in range(_MASK):
                d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5 / c
                if d < 1.0:
                    a = int(255 * (1.0 - d) ** power)
                    if a > 0:
                        mask.set_at((x, y), (255, 255, 255, a))
        return mask

    return _cached(key, build)


def glow(surf, centre, diameter, colour=(255, 255, 255), alpha=120,
         power=2.2):
    """Blit a soft radial glow centred on ``centre`` (screen pixels).

    Alpha-blended, not added: ``BLEND_RGBA_ADD`` adds the full colour of every
    pixel in the sprite and ignores its alpha, which painted a hard bright
    square around every light (measured, and obvious in the first capture).
    """
    if diameter < 4:
        return
    img = radial(int(diameter), power, colour, alpha)
    surf.blit(img, (int(centre[0] - diameter / 2),
                    int(centre[1] - diameter / 2)))


def vignette(size, edge=(6, 6, 10), strength=120):
    """A soft dark frame: bright at the centre, falling off to the edges.

    Built on the small mask grid and smooth-scaled, so it costs one blit per
    frame instead of a per-pixel loop.
    """
    width, height = size
    key = ("vig", width, height, tuple(edge), strength)

    def build():
        mask = pygame.Surface((_MASK, _MASK), pygame.SRCALPHA)
        c = (_MASK - 1) / 2.0
        for y in range(_MASK):
            for x in range(_MASK):
                dx = abs(x - c) / c
                dy = abs(y - c) / c
                d = min(1.0, (dx * dx * 0.55 + dy * dy) ** 0.7)
                a = int(strength * (d ** 2.4))
                if a > 0:
                    mask.set_at((x, y), tuple(edge) + (a,))
        return pygame.transform.smoothscale(mask, (width, height))

    return _cached(key, build)


def haze(size, colour=(180, 200, 230), alpha_top=0, alpha_bottom=54):
    """Vertical atmospheric band: clear at the top, hazy at the horizon."""
    width, height = size
    key = ("haze", width, height, tuple(colour), alpha_top, alpha_bottom)

    def build():
        # Full-resolution rows, not a 24-row mask scaled up: scaling a 24 px
        # ramp across 420 px produced 24 visible steps in the sky.
        band = pygame.Surface((width, height), pygame.SRCALPHA)
        for y in range(height):
            t = y / float(max(1, height - 1))
            a = int(alpha_top + (alpha_bottom - alpha_top) * t)
            if a > 0:
                band.fill(tuple(colour) + (a,), pygame.Rect(0, y, width, 1))
        return band

    return _cached(key, build)


_CLOUD_BLOBS = (
    ((0.02, 0.42, 0.34, 0.34), (0.12, 0.24, 0.34, 0.42),
     (0.30, 0.10, 0.28, 0.48), (0.44, 0.26, 0.34, 0.42),
     (0.56, 0.44, 0.30, 0.30)),
    ((0.03, 0.38, 0.30, 0.38), (0.14, 0.20, 0.36, 0.46),
     (0.32, 0.14, 0.30, 0.46), (0.46, 0.30, 0.32, 0.38),
     (0.58, 0.42, 0.28, 0.32)),
    ((0.02, 0.44, 0.30, 0.32), (0.13, 0.24, 0.32, 0.42),
     (0.28, 0.16, 0.30, 0.44), (0.42, 0.30, 0.34, 0.38),
     (0.55, 0.42, 0.30, 0.32)),
)


def _tinted(mask, colour, alpha=255):
    """Colour an alpha mask without letting transparent RGB bleed grey in.

    Smooth-scaled masks keep white RGB where they are opaque and blend toward
    black at the edge; multiplying a flat colour by that mask would darken the
    rim.  The mask is used for its alpha only.
    """
    layer = pygame.Surface(mask.get_size(), pygame.SRCALPHA)
    layer.fill(tuple(colour) + (255,))
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if alpha < 255:
        layer.fill((255, 255, 255, alpha),
                   special_flags=pygame.BLEND_RGBA_MULT)
    return layer


def cloud(size, body=(244, 248, 255), shade=(186, 199, 224), alpha=235,
          variant=0):
    """A soft cloud billboard: a blurred alpha mask with a shaded base.

    The previous version blurred an already-coloured surface, so the soft edge
    averaged toward transparent black and every cloud came out grey with 6 px
    terraces.  Here the mask is authored at a quarter size, blurred while it is
    still white, and only then tinted - shape and colour stay independent.
    """
    width, height = size
    key = ("cloud", width, height, tuple(body), tuple(shade), alpha,
           variant % len(_CLOUD_BLOBS))

    def build():
        blobs = _CLOUD_BLOBS[variant % len(_CLOUD_BLOBS)]
        mw = max(28, width // 4)
        mh = max(9, height // 4)
        mask = pygame.Surface((mw, mh), pygame.SRCALPHA)
        for (bx, by, bw, bh) in blobs:
            pygame.draw.ellipse(
                mask, (255, 255, 255, 255),
                pygame.Rect(int(bx * mw), int(by * mh),
                            max(2, int(bw * mw)), max(2, int(bh * mh))))
        blur = getattr(pygame.transform, "box_blur", None)
        if blur is not None:
            mask = blur(mask, max(1, mh // 6))
        mask = pygame.transform.smoothscale(mask, (width, height))
        # Blurring and scaling both average the RGB toward transparent black.
        # Force it back to white while leaving the alpha ramp alone.
        mask.fill((255, 255, 255, 255), special_flags=pygame.BLEND_RGB_MAX)

        body_layer = _tinted(mask, body, alpha)
        shade_layer = _tinted(mask, shade, alpha)
        out = pygame.Surface((width, height), pygame.SRCALPHA)
        out.blit(shade_layer, (0, int(height * 0.10)))
        out.blit(body_layer, (0, 0))
        return out

    return _cached(key, build)


def sphere(diameter, body, edge, light=(-0.45, -0.55, 0.82), ambient=0.42,
           maria=False):
    """A shaded, antialiased ball for the sun and moon.

    The flat disc with a rim and a specular dot still read as a sticker.  This
    builds a lit sphere once per theme at an internal 72 px and smooth-scales
    it, so the terminator is soft and the limb still has an edge.  ``maria``
    adds faint darker patches for the moon.
    """
    diameter = max(2, int(diameter))
    key = ("sphere", diameter, tuple(body), tuple(edge), tuple(light),
           ambient, bool(maria))

    def build():
        n = 72
        surf = pygame.Surface((n, n), pygame.SRCALPHA)
        c = (n - 1) / 2.0
        ln = math.sqrt(sum(v * v for v in light)) or 1.0
        lx, ly, lz = (v / ln for v in light)
        for y in range(n):
            dy = (y - c) / c
            for x in range(n):
                dx = (x - c) / c
                d2 = dx * dx + dy * dy
                if d2 >= 1.0:
                    continue
                z = math.sqrt(1.0 - d2)
                diff = max(0.0, dx * lx + dy * ly + z * lz)
                t = ambient + (1.0 - ambient) * (diff ** 1.2)
                t = min(1.0, t * (0.80 + 0.20 * z))
                col = tuple(int(edge[i] + (body[i] - edge[i]) * t)
                            for i in range(3))
                surf.set_at((x, y), col + (255,))
        if maria:
            spots = pygame.Surface((n, n), pygame.SRCALPHA)
            for (sx, sy, sw, sh, a) in ((0.26, 0.34, 0.30, 0.20, 44),
                                        (0.48, 0.26, 0.22, 0.14, 30),
                                        (0.42, 0.56, 0.34, 0.18, 26)):
                pygame.draw.ellipse(
                    spots, (58, 64, 92, a),
                    pygame.Rect(int(sx * n), int(sy * n),
                                max(2, int(sw * n)), max(2, int(sh * n))))
            blur = getattr(pygame.transform, "box_blur", None)
            if blur is not None:
                spots = blur(spots, 2)
            # Clip the maria to the lit disc so they cannot spill into the sky.
            disc = surf.copy()
            disc.fill((255, 255, 255), special_flags=pygame.BLEND_RGB_MAX)
            spots.blit(disc, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(spots, (0, 0))
        return pygame.transform.smoothscale(surf, (diameter, diameter))

    return _cached(key, build)


def shadow(width, height, alpha=90):
    """Soft elliptical contact shadow for anything that stands on the ground."""
    key = ("shadow", width, height, alpha)

    def build():
        img = pygame.Surface((width, height), pygame.SRCALPHA)
        small = pygame.Surface((_MASK, _MASK), pygame.SRCALPHA)
        pygame.draw.ellipse(small, (0, 0, 0, alpha),
                            pygame.Rect(2, 6, _MASK - 4, _MASK - 12))
        small = pygame.transform.smoothscale(small, (width, height))
        img.blit(small, (0, 0))
        return img

    return _cached(key, build)


def accel(surf):
    """Run-length encode a static alpha surface for fast blits.

    A parallax layer or a baked terrain strip is mostly fully transparent or
    fully opaque.  RLE lets SDL skip the empty runs and copy the solid ones,
    blending only the antialiased edge: measured 0.43 ms -> 0.085 ms for a
    1920x480 cave layer.  Needs a display format, so it quietly returns the
    surface unchanged where no video mode exists (offline tools).
    """
    if surf is None:
        return None
    try:
        out = surf.convert_alpha()
        out.set_alpha(255, pygame.RLEACCEL)
        return out
    except pygame.error:
        return surf


def aerial(colour, sky, factor):
    """Blend *colour* toward the sky by *factor* (aerial perspective)."""
    factor = max(0.0, min(1.0, factor))
    return (int(colour[0] + (sky[0] - colour[0]) * factor),
            int(colour[1] + (sky[1] - colour[1]) * factor),
            int(colour[2] + (sky[2] - colour[2]) * factor))


def forget():
    """Drop every cached surface (used by tests that change settings)."""
    _CACHE.clear()
