"""Baked, painterly terrain for the pygame/browser renderer.

A level's static geometry never changes during play, so it is painted once,
at load, into one surface and a frame is a single blit of the visible window.
That buys detail a per-tile renderer could never afford:

* textured rock whose value falls off with depth below the surface;
* organic, rounded silhouettes (a bilinear-upscaled cell mask thresholded at
  the cell boundary: straight edges stay exactly on the collision grid, only
  the corners round off);
* a lit moss lip, grass and accents on every walkable top, a backlit rim on
  exposed sides, hanging roots under overhangs, and a dark fall-off into pits;
* crisp carved bricks and one-way planks whose top is flush with the height
  the player actually stands on.

Hazards (spikes, lava) are deliberately *not* baked: their palette is a
measured readability contract and lava animates, so ``pygame_view`` draws them
every frame on top.  Pure pygame; nothing here touches simulation state.
"""

import math

import pygame

from . import fx
from .looks import look
from .scenery import hash01, fbm, crystal_cluster

CELL = 20
TOP_ROWS = 1          # painted rows above row 0 (grass can overhang)
BOT_ROWS = 2          # rows below the playfield: the ground carries on


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


# --------------------------------------------------------------- textures --
_TEX = {}


def rock_texture(theme, size=128):
    """A seamless rock/soil texture for one world (built from fills, fast)."""
    key = (theme, size)
    if key in _TEX:
        return _TEX[key]
    T = look(theme)["terrain"]
    body = T["body"]
    n = 10
    small = pygame.Surface((n + 2, n + 2))
    for y in range(n + 2):
        for x in range(n + 2):
            v = hash01((x % n) * 31 + (y % n) * 7, 17)
            small.set_at((x, y), _shade(body, 0.86 + 0.28 * v))
    big = pygame.transform.smoothscale(small, (int(size * (n + 2) / n),) * 2)
    off = int(size / n)
    tex = pygame.Surface((size, size))
    tex.blit(big, (-off, -off))
    speck = T["speck"]
    dark = _shade(body, 0.62)
    for i in range(90):
        x = hash01(i, 21) * size
        y = hash01(i, 22) * size
        r = 1 + int(hash01(i, 23) * 3)
        col = speck if hash01(i, 24) < 0.55 else dark
        for ox in (0, -size, size):
            for oy in (0, -size, size):
                pygame.draw.ellipse(tex, col, (int(x + ox), int(y + oy),
                                               r * 2 + 1, r + 1))
    # Strata: a couple of faint horizontal seams.
    for i in range(3):
        y = int(hash01(i, 25) * size)
        pts = [(x, y + 3 * math.sin(x * 0.09 + i)) for x in range(0, size + 8, 8)]
        pygame.draw.lines(tex, _shade(body, 0.74), False, pts, 1)
    _TEX[key] = tex
    return tex


def brick_tiles(theme):
    """Two cached 20x20 masonry variants for ``B`` cells."""
    key = ("brick", theme)
    if key in _TEX:
        return _TEX[key]
    lk = look(theme)
    T = lk["terrain"]
    out = []
    for v in range(2):
        s = 4
        big = pygame.Surface((CELL * s, CELL * s))
        big.fill(T["mortar"])
        rows = ((0, 0, 10), (10, 5 if v else -5, 10))
        for (y0, shift, hgt) in rows:
            for bx in range(-20, 40, 20):
                x0 = bx + shift
                r = pygame.Rect((x0 + 1) * s, (y0 + 1) * s, 18 * s, (hgt - 2) * s)
                pygame.draw.rect(big, T["brick"], r, border_radius=2 * s)
                pygame.draw.line(big, T["brick_lit"], (r.left + s, r.top + s),
                                 (r.right - 2 * s, r.top + s), s + 1)
                pygame.draw.line(big, T["brick_dark"], (r.left + s, r.bottom - s),
                                 (r.right - s, r.bottom - s), s + 1)
        kind = T.get("accent_kind")
        if kind == "rivets":
            for (rx, ry) in ((4, 4), (16, 4), (4, 15), (16, 15)):
                pygame.draw.circle(big, T["brick_lit"], (rx * s, ry * s), int(1.3 * s))
        elif kind == "crystals" and v == 1:
            g = T.get("glow") or T["brick_lit"]
            pygame.draw.line(big, g, (5 * s, 13 * s), (10 * s, 15 * s), s)
            pygame.draw.line(big, g, (10 * s, 15 * s), (15 * s, 12 * s), s)
        elif v == 1:
            pygame.draw.line(big, T["brick_dark"], (6 * s, 3 * s), (9 * s, 8 * s), s)
            pygame.draw.circle(big, T["moss"], (15 * s, 17 * s), 2 * s)
        out.append(pygame.transform.smoothscale(big, (CELL, CELL)))
    _TEX[key] = out
    return out


# ------------------------------------------------------------------- bake --
class TerrainBake:
    """One level's static terrain painted into a single surface.

    ``x0``/``x1`` bake a column range (the level-select preview only needs
    the opening screen).  The surface starts ``TOP_ROWS`` cells above row 0;
    :meth:`draw` handles the offset.
    """

    def __init__(self, lvl, theme, quality=1, x0=0, x1=None):
        self.theme = theme
        self.x0 = max(0, int(x0))
        self.x1 = lvl.w if x1 is None else min(lvl.w, int(x1))
        self.quality = int(bool(quality))
        self.rows = lvl.h + TOP_ROWS + BOT_ROWS
        self.cols = self.x1 - self.x0
        self.surface = pygame.Surface((self.cols * CELL, self.rows * CELL),
                                      pygame.SRCALPHA)
        self.surface.fill((0, 0, 0, 0))
        self._grid(lvl)
        self._paint()
        self.surface = fx.accel(self.surface)

    # -- grid helpers ------------------------------------------------------
    def _grid(self, lvl):
        h = lvl.h
        self.h = h
        self.lvl_w = lvl.w

        def at(x, y):
            x = max(0, min(lvl.w - 1, x))
            if y < 0:
                # Solids in the top row carry on above the screen (a cave
                # ceiling, a tall wall); they never show a grassy top.
                return lvl.grid[0][x] if lvl.grid[0][x] in "#B" else " "
            if y >= h:
                return lvl.grid[h - 1][x] if lvl.grid[h - 1][x] in "#B" else " "
            return lvl.grid[y][x]

        self.at = at

    def ground(self, x, y):
        return self.at(x, y) == "#"

    def solid(self, x, y):
        return self.at(x, y) in "#B"

    def px(self, x):
        return (x - self.x0) * CELL

    def py(self, y):
        return (y + TOP_ROWS) * CELL

    # -- painting ------------------------------------------------------------
    def _paint(self):
        T = look(self.theme)["terrain"]
        self.T = T
        self._ground_body()
        self._pits()
        self._bricks()
        self._tops()
        self._overhangs()
        self._planks()

    def _cell_mask(self, pred, pad=1):
        """Small SRCALPHA mask, one pixel per cell (+pad), white where pred."""
        cw = self.cols + 2 * pad
        rh = self.rows + 2 * pad
        small = pygame.Surface((cw, rh), pygame.SRCALPHA)
        small.fill((255, 255, 255, 0))
        for yy in range(rh):
            y = yy - pad - TOP_ROWS
            for xx in range(cw):
                x = self.x0 + xx - pad
                if pred(x, y):
                    small.set_at((xx, yy), (255, 255, 255, 255))
        return small

    def _rounded(self, small, pad=1):
        """Bilinear-upscale a cell mask and threshold it at the cell edge."""
        cw, rh = small.get_size()
        big = pygame.transform.smoothscale(small, (cw * CELL, rh * CELL))
        # alpha' = 8 * max(0, a - 112): a hard-ish threshold at 50 % with a
        # ~4 px antialiased ramp.  Straight edges land exactly on the grid.
        big.fill((0, 0, 0, 112), special_flags=pygame.BLEND_RGBA_SUB)
        for _ in range(3):
            big.blit(big.copy(), (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        big.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
        out = pygame.Surface((self.cols * CELL, self.rows * CELL),
                             pygame.SRCALPHA)
        out.blit(big, (-pad * CELL, -pad * CELL))
        return out

    def _ground_body(self):
        T = self.T
        surf = self.surface
        w, h = surf.get_size()
        tex = rock_texture(self.theme)
        ts = tex.get_width()
        body = pygame.Surface((w, h), pygame.SRCALPHA)
        ox = (self.x0 * CELL) % ts
        for y in range(0, h + ts, ts):
            for x in range(-ox, w + ts, ts):
                body.blit(tex, (x, y))
        mask = self._rounded(self._cell_mask(self.ground))
        self.ground_mask = mask
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Depth: value falls off below the walkable surface.  Computed per
        # cell, then bilinear-upscaled, so steps in the ground do not leave
        # vertical seams in the shading.
        pad = 1
        cw, rh = self.cols + 2 * pad, self.rows + 2 * pad
        depth = pygame.Surface((cw, rh), pygame.SRCALPHA)
        depth.fill((255, 255, 255, 0))
        for xx in range(cw):
            x = self.x0 + xx - pad
            run = 0
            for yy in range(rh):
                y = yy - pad - TOP_ROWS
                if self.solid(x, y):
                    a = min(232, 26 + run * 52)
                    depth.set_at((xx, yy), (255, 255, 255, a))
                    run += 1
                else:
                    run = 0
        depth = pygame.transform.smoothscale(depth, (cw * CELL, rh * CELL))
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill(tuple(T["deep"]) + (255,))
        shade.blit(depth, (-pad * CELL, -pad * CELL - CELL // 2),
                   special_flags=pygame.BLEND_RGBA_MULT)
        shade.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        body.blit(shade, (0, 0))

        # Backlit rim on exposed faces: the mask minus its own erosion.
        if self.quality:
            eroded = mask.copy()
            for (dx, dy) in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                eroded.blit(mask, (dx, dy), special_flags=pygame.BLEND_RGBA_MIN)
            edge = mask.copy()
            edge.blit(eroded, (0, 0), special_flags=pygame.BLEND_RGBA_SUB)
            edge.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
            rim = pygame.Surface((w, h), pygame.SRCALPHA)
            rim.fill(tuple(T["side"]) + (255,))
            rim.blit(edge, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            rim.fill((255, 255, 255, 150), special_flags=pygame.BLEND_RGBA_MULT)
            body.blit(rim, (0, 0))
        surf.blit(body, (0, 0))

    def _pits(self):
        """Darkness falling away into every gap at the bottom of the level."""
        h = self.h
        T = self.T
        surf = self.surface
        x = self.x0
        while x < self.x1:
            if self.solid(x, h - 1):
                x += 1
                continue
            a = x
            while x < self.x1 and not self.solid(x, h - 1):
                x += 1
            top = self.py(h - 4)
            bot = self.py(h + BOT_ROWS)
            grad = pygame.Surface((1, bot - top), pygame.SRCALPHA)
            for y in range(bot - top):
                k = (y / float(bot - top - 1)) ** 1.6
                grad.set_at((0, y), tuple(_shade(T["deep"], 0.5)) + (int(230 * k),))
            grad = pygame.transform.scale(grad, ((x - a) * CELL + 8, bot - top))
            surf.blit(grad, (self.px(a) - 4, top))

    def _bricks(self):
        tiles = brick_tiles(self.theme)
        surf = self.surface
        T = self.T
        for y in range(-TOP_ROWS, self.h + BOT_ROWS - TOP_ROWS):
            for x in range(self.x0, self.x1):
                if self.at(x, y) != "B":
                    continue
                tile = tiles[int(hash01(x * 131 + y, 5) * 2) % 2]
                sx, sy = self.px(x), self.py(y)
                surf.blit(tile, (sx, sy))
                # Exposed faces get a lit edge; covered faces a contact shade.
                if not self.solid(x - 1, y):
                    pygame.draw.line(surf, T["brick_lit"], (sx, sy + 1),
                                     (sx, sy + CELL - 2))
                if not self.solid(x + 1, y):
                    pygame.draw.line(surf, T["brick_dark"], (sx + CELL - 1, sy + 1),
                                     (sx + CELL - 1, sy + CELL - 1))
                if not self.solid(x, y + 1) and y + 1 < self.h:
                    pygame.draw.line(surf, _shade(T["brick_dark"], 0.6),
                                     (sx, sy + CELL - 1), (sx + CELL - 1, sy + CELL - 1), 2)

    # -- walkable tops ------------------------------------------------------
    def _top_runs(self):
        """(y, x_start, x_end) runs of solid cells with open space above."""
        runs = []
        for y in range(0, self.h):
            x = self.x0
            while x < self.x1:
                if self.solid(x, y) and not self.solid(x, y - 1):
                    a = x
                    while (x < self.x1 and self.solid(x, y)
                           and not self.solid(x, y - 1)):
                        x += 1
                    runs.append((y, a, x))
                else:
                    x += 1
        return runs

    def _tops(self):
        T = self.T
        surf = self.surface
        accent_kind = T.get("accent_kind")
        quality = self.quality
        r = CELL // 2
        for (y, a, b) in self._top_runs():
            y0 = self.py(y)
            x_start, x_end = self.px(a), self.px(b)
            # Rounded convex corners only where the side is open and the run
            # is ground (bricks keep their carved square corners).
            left_open = not self.solid(a - 1, y) and self.ground(a, y)
            right_open = not self.solid(b, y) and self.ground(b - 1, y)

            def surface_y(px):
                dl = px - x_start
                dr = x_end - px
                if left_open and dl < r:
                    return y0 + r - math.sqrt(max(0.0, r * r - (r - dl) ** 2))
                if right_open and dr < r:
                    return y0 + r - math.sqrt(max(0.0, r * r - (r - dr) ** 2))
                return y0

            brick_run = all(self.at(x, y) == "B" for x in range(a, b))
            thick = 3 if brick_run else 6
            # Moss lip: a wavy band following the surface.
            top_pts, bot_pts = [], []
            step = 2
            for px in range(x_start, x_end + 1, step):
                sy = surface_y(px)
                wx = px + self.x0 * CELL
                lip = thick + 3 * fbm(wx, 4000, 3, 160, 3)
                top_pts.append((px, sy - 1))
                bot_pts.append((px, sy + lip))
            if len(top_pts) >= 2:
                pygame.draw.polygon(surf, T["moss"], top_pts + bot_pts[::-1])
                lit = [(p[0], p[1] + 1) for p in top_pts]
                pygame.draw.lines(surf, T["moss_lit"], False, lit, 2)
                pygame.draw.lines(surf, T["rim"], False, top_pts, 1)
            if not quality:
                continue
            # Grass blades / crystal moss.
            blades = T["blades"]
            px = x_start + 1
            i = 0
            while px < x_end - 1:
                wx = px + self.x0 * CELL
                hgt = 3 + 9 * hash01(wx, y * 7 + 1) ** 1.6
                if brick_run:
                    hgt *= 0.5
                lean = (hash01(wx, y * 7 + 2) - 0.5) * 6
                col = blades[int(hash01(wx, y * 7 + 3) * len(blades))]
                sy = surface_y(px) + 1
                pygame.draw.polygon(surf, col, [(px - 1, sy), (px + 1.5, sy),
                                                (px + lean, sy - hgt)])
                px += 2 + int(hash01(wx, y * 7 + 4) * 4)
                i += 1
            # Accents: flowers, crystals or rivets, sparse and deterministic.
            for x in range(a, b):
                wx = x * 37 + y * 101
                if hash01(wx, 9) > 0.22:
                    continue
                cx = self.px(x) + 4 + int(hash01(wx, 10) * 12)
                cy = surface_y(cx)
                col = T["accent"][int(hash01(wx, 11) * 2) % 2]
                if accent_kind == "flowers":
                    stem = 5 + int(hash01(wx, 12) * 6)
                    pygame.draw.line(surf, T["blades"][0], (cx, cy), (cx, cy - stem), 1)
                    pygame.draw.circle(surf, col, (cx, int(cy - stem)), 2)
                    surf.set_at((cx, int(cy - stem)), (255, 255, 240, 255))
                elif accent_kind == "crystals":
                    size = 5 + 5 * hash01(wx, 13)
                    glow = pygame.Surface((24, 24), pygame.SRCALPHA)
                    pygame.draw.circle(glow, tuple(col) + (40,), (12, 12), 11)
                    pygame.draw.circle(glow, tuple(col) + (50,), (12, 12), 6)
                    surf.blit(glow, (cx - 12, int(cy - size * 0.6) - 12))
                    crystal_cluster(surf, cx, cy + 2, size, col, True, wx)
                elif accent_kind == "rivets":
                    pygame.draw.circle(surf, T["brick_dark"], (cx, int(cy + 9)), 2)
                    pygame.draw.circle(surf, col, (cx - 1, int(cy + 8)), 1)

    def _overhangs(self):
        """Roots and drips under exposed bottoms (floating islands, ledges)."""
        if not self.quality:
            return
        T = self.T
        surf = self.surface
        crystal = T.get("accent_kind") == "crystals"
        for y in range(0, self.h - 1):
            for x in range(self.x0, self.x1):
                if not self.ground(x, y) or self.solid(x, y + 1):
                    continue
                sx = self.px(x)
                sy = self.py(y + 1)
                pygame.draw.line(surf, _shade(T["deep"], 0.8), (sx, sy - 1),
                                 (sx + CELL - 1, sy - 1), 2)
                for k in range(2):
                    wx = x * 53 + y * 17 + k
                    if hash01(wx, 31) > 0.7:
                        continue
                    rx = sx + 3 + int(hash01(wx, 32) * 14)
                    ln = 6 + int(hash01(wx, 33) * 22)
                    if crystal:
                        col = T["accent"][k % 2]
                        pygame.draw.polygon(surf, _shade(col, 0.7),
                                            [(rx - 2, sy - 1), (rx + 2, sy - 1),
                                             (rx, sy + ln * 0.45)])
                        pygame.draw.line(surf, col, (rx, sy), (rx, int(sy + ln * 0.4)), 1)
                    else:
                        pts = []
                        for s in range(6):
                            t = s / 5.0
                            pts.append((rx + math.sin(t * 3 + wx) * 3, sy - 1 + t * ln))
                        pygame.draw.lines(surf, T["roots"], False, pts, 2)
                        if hash01(wx, 34) < 0.5:
                            lx, ly = pts[-1]
                            pygame.draw.ellipse(surf, T["blades"][1],
                                                (int(lx - 2), int(ly - 1), 5, 3))

    def _planks(self):
        T = self.T
        surf = self.surface
        kind = T.get("accent_kind")
        for y in range(0, self.h):
            x = self.x0
            while x < self.x1:
                if self.at(x, y) != "-":
                    x += 1
                    continue
                a = x
                while x < self.x1 and self.at(x, y) == "-":
                    x += 1
                self._plank(surf, T, kind, a, x, y)

    def _plank(self, surf, T, kind, a, b, y):
        x0 = self.px(a) - 1
        x1 = self.px(b) + 1
        y0 = self.py(y)
        thick = 8
        body = pygame.Rect(x0, y0, x1 - x0, thick)
        pygame.draw.rect(surf, T["plank_dark"], body.move(0, 1), border_radius=3)
        pygame.draw.rect(surf, T["plank"], body, border_radius=3)
        pygame.draw.line(surf, T["plank_lit"], (x0 + 2, y0), (x1 - 3, y0), 2)
        pygame.draw.line(surf, T["rim"], (x0 + 3, y0), (x1 - 4, y0), 1)
        pygame.draw.line(surf, T["plank_dark"], (x0 + 2, y0 + thick - 1),
                         (x1 - 3, y0 + thick - 1), 1)
        if kind == "rivets":
            for cx in range(x0 + 6, x1 - 3, 10):
                pygame.draw.circle(surf, T["plank_lit"], (cx, y0 + 4), 1)
        elif kind == "crystals":
            glow = T.get("glow") or T["plank_lit"]
            for cx in range(x0 + 10, x1 - 6, 20):
                pygame.draw.circle(surf, glow, (cx, y0 + 4), 1)
        else:
            # Wooden boards: seams between the planks.
            for cx in range(x0 + 14, x1 - 6, 17):
                pygame.draw.line(surf, T["plank_dark"], (cx, y0 + 2),
                                 (cx, y0 + thick - 2), 1)
        # Supports: ropes / chains / crystal pegs dangling at both ends.
        for ex in (x0 + 4, x1 - 5):
            if kind == "crystals":
                pygame.draw.polygon(surf, _shade(T["plank"], 0.8),
                                    [(ex - 2, y0 + thick), (ex + 2, y0 + thick),
                                     (ex, y0 + thick + 7)])
            else:
                pygame.draw.line(surf, T["rope"], (ex, y0 + thick), (ex, y0 + thick + 9), 2)
                pygame.draw.line(surf, T["rope"], (ex - 2, y0 + thick + 2),
                                 (ex + 2, y0 + thick + 2), 1)
        # A few blades on top.
        if self.quality:
            for px in range(x0 + 3, x1 - 3, 7):
                wx = px + self.x0 * CELL + y * 13
                if hash01(wx, 41) < 0.5:
                    hgt = 2 + 5 * hash01(wx, 42)
                    col = T["blades"][int(hash01(wx, 43) * 3) % 3]
                    pygame.draw.line(surf, col, (px, y0), (px + 1, int(y0 - hgt)), 1)

    # -- per frame ---------------------------------------------------------
    def draw(self, surf, cam_px, oy):
        """Blit the visible window; ``cam_px`` is the camera's left edge."""
        x = int(cam_px) - self.x0 * CELL
        top = int(oy) - TOP_ROWS * CELL
        sw = surf.get_width()
        area = pygame.Rect(max(0, x), 0, sw, self.surface.get_height())
        surf.blit(self.surface, (max(0, -x), top), area)


def forget():
    _TEX.clear()
