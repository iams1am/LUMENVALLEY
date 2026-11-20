"""Themed sky gradients and parallax backdrop layers.

Each level theme gets a vertical color gradient plus a stack of parallax
layers (sun, clouds, hills, mountains, stars, stalactites, crystals) that
scroll at fractions of the camera speed for a sense of depth.
"""

import math

from render import VIEW_W


def _h(n):
    """Deterministic pseudo-random in [0, 1) from a float seed."""
    return (math.sin(n * 127.1 + 311.7) * 43758.5453) % 1.0


SKY = {
    "day": [17, 17, 18, 18, 19, 19, 20, 25, 25, 26,
            26, 31, 31, 32, 38, 38, 39, 39, 45, 45],
    "cave": [16, 16, 16, 16, 16, 16, 16, 17, 17, 17,
             17, 17, 17, 18, 18, 18, 18, 18, 19, 19],
    "dusk": [16, 16, 16, 17, 17, 18, 18, 54, 54, 55,
             55, 56, 57, 57, 92, 92, 93, 128, 129, 129],
    "night": [16, 16, 16, 16, 16, 17, 17, 17, 17, 17,
              18, 18, 18, 54, 54, 53, 53, 52, 52, 52],
    "dawn": [17, 17, 17, 17, 18, 18, 54, 54, 55, 55,
             128, 129, 129, 175, 175, 209, 210, 216, 223, 223],
}

CLOUD = [
    "....######....",
    "..##########..",
    ".############.",
    "##############",
]


def draw_sky(scr, theme, oy, gh):
    grad = SKY[theme]
    n = len(grad)
    for i in range(gh):
        c = grad[min(i, n - 1)]
        row = scr.cells[i + oy]
        blank = (" ", 252, c)
        for x in range(scr.w):
            row[x] = blank


def _fill_down(scr, x, y_from, oy, gh, color):
    col = scr.cells
    for yy in range(y_from, gh):
        cy = yy + oy
        if 0 <= cy < scr.h:
            col[cy][x] = (" ", 252, color)


def _disc(scr, cx, cy, r, color, oy):
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                scr.put(cx + dx, oy + cy + dy, " ", color, color)


def _hills(scr, cam, oy, gh, factor, base, amp, k1, k2, ph, color):
    for sx in range(VIEW_W):
        wx = sx + cam * factor
        hgt = base + amp * math.sin(wx * k1 + ph) \
            + amp * 0.6 * math.sin(wx * k2 + ph * 2.3)
        _fill_down(scr, sx, int(hgt), oy, gh, color)


def _mountains(scr, cam, oy, gh, factor, base, amp, k, ph, color):
    for sx in range(VIEW_W):
        wx = sx + cam * factor
        tri = abs(((wx * k + ph) % 2.0) - 1.0)
        hgt = gh - int(base + tri * amp + 2.0 * math.sin(wx * 0.53))
        _fill_down(scr, sx, hgt, oy, gh, color)


def _cloud(scr, x, y):
    w = len(CLOUD[0])
    for j, row in enumerate(CLOUD):
        for i, ch in enumerate(row):
            if ch == ".":
                continue
            edge = i == 0 or i == w - 1 or j == 0
            scr.put(x + i, y + j, " ", 255, 252 if edge else 255)


def _stars(scr, cam, oy, gh, t, n, seed, bright=188):
    span = 240.0
    for i in range(n):
        wx = _h(i * 3.71 + seed) * span
        wy = int(_h(i * 9.13 + seed) * (gh * 0.55))
        sx = int(wx - cam * 0.12) % int(span)
        if sx >= VIEW_W:
            continue
        if math.sin(t * 2.4 + i * 1.93) > 0.25:
            scr.over(sx, wy + oy, "*" if i % 4 == 0 else ".", bright)


def draw_crystals(scr, cam, oy, t, factor, n, seed):
    span = 200.0
    colors = (44, 45, 201, 213, 50)
    for i in range(n):
        wx = _h(i * 5.31 + seed) * span
        wy = int(3 + _h(i * 8.97 + seed) * 13)
        sx = int(wx - cam * factor) % int(span)
        if sx >= VIEW_W:
            continue
        tw = math.sin(t * 2.0 + i * 2.7)
        if tw > -0.2:
            scr.over(sx, wy + oy, "*" if tw > 0.5 else "+", colors[i % 5])


def _stalactites(scr, cam, oy):
    span = 300.0
    for i in range(34):
        wx = i * 9.0 + _h(i * 4.37) * 6.0
        sx = int(wx - cam * 0.3) % int(span)
        if sx >= VIEW_W - 1:
            continue
        ln = 2 + int(_h(i * 7.31) * 2)
        col = 59 if i % 2 else 60
        for j in range(ln - 1):
            scr.over(sx - 1, oy + j, "\\", col)
            scr.over(sx, oy + j, "/", col)
        scr.over(sx, oy + ln - 1, "V", col)


def draw_parallax(scr, theme, cam, oy, gh, t):
    if theme == "day":
        _disc(scr, 66, 4, 4, 229, oy)
        _disc(scr, 66, 4, 3, 228, oy)
        _disc(scr, 66, 4, 2, 227, oy)
        span = 210.0
        for i in range(8):
            wx = _h(i * 3.37) * span
            wy = 1 + int(_h(i * 7.77) * 4)
            sx = int(wx - cam * 0.22 - t * (1.5 + i % 3)) % int(span)
            if sx <= VIEW_W - len(CLOUD[0]):
                _cloud(scr, sx, wy + oy)
        _hills(scr, cam, oy, gh, 0.30, gh - 9, 2.2, 0.043, 0.115, 1.3, 65)
        _hills(scr, cam, oy, gh, 0.55, gh - 6, 2.6, 0.061, 0.150, 4.1, 22)
    elif theme == "cave":
        _stars(scr, cam, oy, gh, t, 16, 9.0, bright=239)
        _stalactites(scr, cam, oy)
        draw_crystals(scr, cam, oy, t, 0.30, 12, 3.0)
        draw_crystals(scr, cam, oy, t, 0.50, 14, 77.0)
    elif theme == "night":
        _stars(scr, cam, oy, gh, t, 60, 13.0)
        moon_x = int(VIEW_W * 0.72 - cam * 0.06) % 200
        if moon_x < VIEW_W + 6:
            _disc(scr, moon_x, 4, 4, 189, oy)
            _disc(scr, moon_x, 4, 3, 255, oy)
        _mountains(scr, cam, oy, gh, 0.25, 8, 6.0, 0.10, 1.1, 53)
        _mountains(scr, cam, oy, gh, 0.50, 5, 7.0, 0.14, 4.4, 52)
        draw_crystals(scr, cam, oy, t * 0.5, 0.35, 10, 41.0)
    elif theme == "dawn":
        _stars(scr, cam, oy, gh, t, 22, 21.0, bright=215)
        sun_x = int(VIEW_W * 0.30 - cam * 0.05) % 180
        if sun_x < VIEW_W + 5:
            _disc(scr, sun_x, gh - 4, 4, 217, oy)
            _disc(scr, sun_x, gh - 4, 3, 223, oy)
        _mountains(scr, cam, oy, gh, 0.25, 8, 6.0, 0.09, 0.7, 96)
        _mountains(scr, cam, oy, gh, 0.50, 5, 7.0, 0.13, 3.3, 137)
    else:  # dusk
        _stars(scr, cam, oy, gh, t, 42, 5.0)
        sun_x = int(VIEW_W * 0.62 - cam * 0.08) % 160
        if sun_x < VIEW_W + 4:
            _disc(scr, sun_x, gh + 2, 5, 209, oy)
            _disc(scr, sun_x, gh + 2, 4, 208, oy)
        _mountains(scr, cam, oy, gh, 0.25, 8, 6.0, 0.09, 0.0, 53)
        _mountains(scr, cam, oy, gh, 0.50, 5, 7.0, 0.13, 2.6, 89)
