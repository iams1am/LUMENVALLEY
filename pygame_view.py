"""High-resolution pygame front-end for Lumenvalley.

Reuses every piece of game logic (states, physics, enemies, scoring,
audio) from the headless `lumenvalley.sim` core through the `game.Game` facade,
and renders it as a luminous, painted world: layered parallax with aerial
perspective and volumetric light, baked organic terrain, hand-posed vector
characters sized to their hitboxes, dynamic light in dark worlds, bloom, and a
floating glass HUD.  None of it changes what the simulation does.

Run with:  python pygame_view.py
"""

import math
import os
import struct
import sys
import time

import pygame

from game import Game, STEP, VERSION, VIEW_W, VIEW_H, GAME_OY, GAME_H, THEMES
from lumenvalley import audio_mix as mixmod
from lumenvalley import motion
from lumenvalley.bestiary import BOSS_KINDS, enemy_pose, profile
from lumenvalley import palette as pal
from lumenvalley.diagnostics import (
    OVERLAY_LINE_H, OVERLAY_PAD, FrameRecorder, entity_counts, memory_mb,
    overlay_box, overlay_lines,
)
from lumenvalley.ghosts import playhead
from lumenvalley.palette import contrast, rgb, rim_for
from lumenvalley.progress import medal_for, pace_line, threat_count
from lumenvalley.rendering import art
from lumenvalley.rendering import fx
from lumenvalley.rendering import scenery as scen
from lumenvalley.rendering import terrain as terr
from lumenvalley.rendering.looks import HERO
from lumenvalley.ui import HINTS, ROW_H
from lumenvalley.timing import FixedStepClock
from lumenvalley.constants import PAR_TIMES
from levels import Level
import music as musicmod
import sound as sndmod
import sprites as sp
from entities import PLAYER_W, PLAYER_H

HERE = os.path.dirname(os.path.abspath(__file__))
# Built by tools/build_assets.py.  Shipping files is what makes the browser
# build possible: there is no subprocess, winsound or runtime synthesiser
# there.  The synthesisers remain as a fallback for a source checkout without
# assets, so the desktop build never depends on a build step.
ASSET_DIR = os.path.join(HERE, "assets")
FONT_PATH = os.path.join(ASSET_DIR, "fonts", "FreeSansBold.ttf")

# The browser runs the same code through wasm under a software rasterizer, where
# every alpha-blended pixel costs several times what it does natively.  The web
# profile keeps the look but drops the full-screen passes (bloom, the dynamic
# light map, the canopy and one parallax layer) and scales halos down.
ON_WASM = sys.platform == "emscripten"
GLOW_SCALE = 0.55 if ON_WASM else 1.0

CELL = 20                       # px per terminal cell
HPX = CELL // 2                 # px per sprite half-block pixel
W = VIEW_W * CELL
H = VIEW_H * CELL
GAME_TOP = GAME_OY * CELL
GAME_BOT = H

# ----------------------------------------------------------------- chrome --
# Glass chrome: deep blue-black surfaces, one crystal-cyan accent shared with
# the hero's pendant, and a focused row that is a filled shape with a lit bar.
UI_BG = (12, 16, 26)
UI_BG_TOP = (28, 36, 56)
UI_BG_BOT = (12, 16, 27)
HUD_BG = (8, 10, 18)
PANEL_EDGE = (62, 84, 112)
PANEL_INNER = (120, 160, 200)
FOCUS_BG = (24, 44, 66)
FOCUS_FG = (246, 250, 255)
FOCUS_BAR = (124, 226, 255)
ACCENT = (124, 214, 255)
TEXT_BODY = (226, 232, 242)
TEXT_HEAD = (244, 247, 252)
TEXT_DIM = (154, 168, 190)
TEXT_OFF = (150, 162, 186)

# Muted status colours: enough hue to carry meaning, not enough saturation to
# turn every results row into a warning light.  All pass the UI contrast gate.
UI_GOOD = (130, 226, 172)
UI_WARN = (238, 194, 122)
UI_BAD = (238, 132, 148)
UI_GOLD = (242, 208, 122)
UI_SILVER = (208, 216, 232)
UI_BRONZE = (218, 166, 118)
UI_COIN = (246, 214, 132)
UI_GEM = (140, 228, 248)
UI_LIFE = (240, 120, 150)

RADIUS = 14                     # panel corner radius

# The bundled font is all-caps by convention in the menu data.  Rendering
# mixed case is a display transform only: the simulation still owns stable
# labels and actions.
_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "back", "but", "by", "for", "from", "in",
    "into", "of", "on", "or", "the", "to", "with",
}
_ACRONYMS = {"FPS", "HUD", "SFX", "UI"}


def display_case(s):
    """Title-case an all-caps label without turning acronyms into words."""
    out = []
    for i, word in enumerate(str(s).split(" ")):
        core = word.strip(".,:;!?()[]")
        if not core:
            out.append(word)
            continue
        if core.upper() in _ACRONYMS:
            out.append(word.upper())
        elif i and core.lower() in _MINOR_WORDS:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


# The diagnostics overlay colours its lines by role, so the decision of what is
# healthy lives in lumenvalley.diagnostics and only the paint lives here.
DIAG_COLORS = {
    "heading": (120, 230, 255),
    "good": (90, 240, 140),
    "warn": (255, 220, 90),
    "bad": (255, 110, 110),
    "body": TEXT_BODY,
    "hint": TEXT_DIM,
}


# -------------------------------------------------------------- sprites --
_surf_cache = {}


def sprite_surface(rows, ghost=False):
    """Legacy pixel-grid sprite (the editor palette and tools still use it)."""
    key = (tuple(rows), ghost)
    s = _surf_cache.get(key)
    if s is not None:
        return s
    h = len(rows)
    w = len(rows[0])
    surf = pygame.Surface((w * HPX, h * HPX), pygame.SRCALPHA)
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch in ". ":
                continue
            col = rgb(sp.PAL.get(ch, 252)) if not ghost else (105, 110, 125)
            rect = pygame.Rect(i * HPX, j * HPX, HPX, HPX)
            if ghost:
                surf.fill((col[0], col[1], col[2], 110), rect)
            else:
                surf.fill(col + (255,), rect)
    _surf_cache[key] = surf
    return surf


def blit_bottom(surf, rows_img, cx_cells, bottom_cells, flip=False, oy=0):
    """Blit anchored so the sprite's feet sit on ``bottom_cells``.

    ``oy`` is the screen-space top of the playfield in whole pixels.  The HUD
    occupies the top three rows, so every world-space object must add it; the
    tiles already do.  Mixing the two spaces made sprites float 60 px above
    the ground.
    """
    w_px, h_px = rows_img.get_size()
    x = int((cx_cells - w_px / CELL / 2) * CELL)
    y = int(math.floor(bottom_cells * CELL) - h_px) + int(oy)
    if flip:
        rows_img = pygame.transform.flip(rows_img, True, False)
    surf.blit(rows_img, (x, y))


# ------------------------------------------------------------------ type --
def load_font(size, bold=True):
    """Bundled font first: a browser has no system font named Consolas."""
    if os.path.exists(FONT_PATH):
        try:
            return pygame.font.Font(FONT_PATH, size)
        except (OSError, pygame.error):
            pass
    return pygame.font.SysFont("consolas", size, bold=bold)


class Type:
    """A font with optional letter-spacing and a render cache.

    Tracked uppercase is what makes small labels read as designed rather than
    typed; caching stops the HUD from re-rasterising the same strings every
    frame.  It quacks like ``pygame.font.Font`` (``render``/``size``/
    ``get_height``), so every caller, test and tool keeps working.
    """

    def __init__(self, size, tracking=0.0):
        self.font = load_font(size)
        self.tracking = float(tracking)
        self._cache = {}

    def get_height(self):
        return self.font.get_height()

    def get_linesize(self):
        return self.font.get_linesize()

    def get_ascent(self):
        return self.font.get_ascent()

    def size(self, s):
        if not self.tracking or len(s) < 2:
            return self.font.size(s)
        w = sum(self.font.size(ch)[0] for ch in s)
        return (w + int(round(self.tracking * (len(s) - 1))),
                self.font.get_height())

    def render(self, s, aa=True, color=(255, 255, 255), background=None):
        key = (s, tuple(color))
        img = self._cache.get(key)
        if img is not None:
            return img
        if not self.tracking or len(s) < 2:
            img = self.font.render(s, aa, color)
        else:
            w, h = self.size(s)
            img = pygame.Surface((max(1, w), max(1, h + 2)), pygame.SRCALPHA)
            x = 0.0
            for ch in s:
                g = self.font.render(ch, aa, color)
                img.blit(g, (int(round(x)), 0))
                x += self.font.size(ch)[0] + self.tracking
            img = img.subsurface((0, 0, img.get_width(), h + 2)).copy()
        if len(self._cache) > 300:
            self._cache.clear()
        self._cache[key] = img
        return img


# ----------------------------------------------------------------- scarf --
class Scarf:
    """The hero's scarf tail: a short verlet rope owned by the renderer.

    Secondary motion is what makes a small character feel alive.  The rope
    lives in world pixels (camera moves do not shake it), never touches the
    simulation, and resets itself when the anchor teleports (respawn).
    """

    N = 8

    def __init__(self, seg=3.3, width=3.4):
        self.seg = seg
        self.width = width
        self.pts = None
        self.old = None
        self.t = None

    def update(self, anchor, face, t, wind=1.0, gravity=420.0):
        ax, ay = anchor
        if (self.pts is None or self.t is None
                or abs(self.pts[0][0] - ax) + abs(self.pts[0][1] - ay) > 90):
            # Start hanging behind and below the knot, the pose a resting
            # scarf settles into, so a first frame never shows a stiff bar.
            self.pts = [[ax - face * i * self.seg * 0.45,
                         ay + i * self.seg * 0.88] for i in range(self.N)]
            self.old = [list(p) for p in self.pts]
            self.t = t
            return
        dt = max(0.0, min(1.0 / 24.0, t - self.t))
        self.t = t
        self.pts[0] = [ax, ay]
        if dt <= 0.0:
            return
        gust = math.sin(t * 2.3) * 0.5 + math.sin(t * 5.1) * 0.25
        for i in range(1, self.N):
            p, o = self.pts[i], self.old[i]
            vx = (p[0] - o[0]) * 0.94
            vy = (p[1] - o[1]) * 0.94
            o[0], o[1] = p[0], p[1]
            flutter = math.sin(t * 9.0 + i * 0.9) * 26.0 * wind
            p[0] += vx + (-face * 70.0 * wind * (1.0 + gust)) * dt * dt * 60
            p[1] += vy + (gravity + flutter) * dt * dt
        for _ in range(3):
            for i in range(1, self.N):
                a, b = self.pts[i - 1], self.pts[i]
                dx, dy = b[0] - a[0], b[1] - a[1]
                d = math.hypot(dx, dy) or 1e-6
                k = (d - self.seg) / d
                if i == 1:
                    b[0] -= dx * k
                    b[1] -= dy * k
                else:
                    a[0] += dx * k * 0.5
                    a[1] += dy * k * 0.5
                    b[0] -= dx * k * 0.5
                    b[1] -= dy * k * 0.5

    def draw(self, surf, ox, oy, scale=1.0):
        if self.pts is None:
            return
        pts = [(p[0] - ox, p[1] - oy) for p in self.pts]
        left, right = [], []
        n = len(pts)
        for i, (x, y) in enumerate(pts):
            a = pts[max(0, i - 1)]
            b = pts[min(n - 1, i + 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            d = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / d, dx / d
            w = self.width * scale * (1.0 - 0.55 * i / (n - 1)) * 0.5
            left.append((x + nx * w, y + ny * w))
            right.append((x - nx * w, y - ny * w))
        poly = left + right[::-1]
        pygame.draw.polygon(surf, HERO["scarf_dark"], poly)
        inner = [(x * 0.6 + lx * 0.4, y * 0.6 + ly * 0.4)
                 for (x, y), (lx, ly) in zip(pts, left)]
        pygame.draw.polygon(surf, HERO["scarf"], inner + pts[::-1])
        if n > 2:
            pygame.draw.aalines(surf, HERO["scarf_lit"], False, inner[:-1])


# ------------------------------------------------------------ main class --
class PygameGame(Game):

    def __init__(self, seed=None, store=None, audio=None):
        global AUDIO
        if AUDIO is None:
            AUDIO = Audio()
        sndmod.play = AUDIO.play_sfx
        super().__init__(seed=seed, store=store, audio=audio or AUDIO)
        self.font_hud = Type(22)
        self.font_ui = Type(26)
        self.font_small = Type(20)
        self.font_big = Type(60)
        self.font_label = Type(15, 1.8)       # tracked caps: HUD labels
        self.font_title = Type(18, 3.0)       # tracked caps: HUD level name
        self.font_head = Type(26, 3.2)        # tracked caps: panel headings
        self.font_sub = Type(18, 1.4)
        # Optional layout probe: tools/check_ui.py turns this into a list and
        # measures every drawn string instead of trusting the eye.
        self.ui_probe = None
        self._panel_rect = None
        # Development-only overlay.  It records nothing until F3 is pressed, so
        # normal play pays no deque or percentile cost.
        self.diag = FrameRecorder()
        self.show_diag = False
        self._diag_memory = False
        self._diag_owns_memory = False
        # Renderer-owned presentation state (never read by the simulation).
        self._bake = None
        self._bake_key = None
        self._bake_lvl = None
        self._thumbs = {}
        self._skies = {}
        self._lightmap = None
        self._scarf = Scarf()
        self._title_scarf = Scarf(seg=9.0, width=10.0)
        self._vfx = []
        self._obs = None
        self._obs_key = None
        self._fade_t0 = None
        self._last_state = None
        self._toast_seen = None

    def step(self, dt, keys, edges):
        return super().step(dt, keys, edges)

    # ------------------------------------------------------- diagnostics --
    def toggle_diagnostics(self):
        """Show/hide the developer overlay and reset its rolling window."""
        self.show_diag = not self.show_diag
        if self.show_diag:
            self.diag.clear()
            self._diag_memory = self._start_memory_tracing()
            return self.show_diag
        if self._diag_owns_memory:
            # Tracing costs every allocation in the process, so it stops with
            # the overlay that asked for it; someone else's tracer is left on.
            try:
                import tracemalloc
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
            except (ImportError, RuntimeError):
                pass
        self._diag_memory = False
        self._diag_owns_memory = False
        return self.show_diag

    def _start_memory_tracing(self):
        """Start heap tracing when asked; return whether memory is readable."""
        if os.environ.get("LV_DIAG_MEMORY") != "1":
            return False
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                tracemalloc.start()
                self._diag_owns_memory = True
            return tracemalloc.is_tracing()
        except (ImportError, RuntimeError):
            return False

    def record_frame(self, frame_ms, update_ms, render_ms):
        if self.show_diag:
            self.diag.add(frame_ms, update_ms, render_ms)

    def draw_diagnostics(self, surf):
        """Draw the measured frame-time distribution in the corner.

        The overlay is intentionally not part of ``draw_all``: the shipping
        screens and the layout audit stay free of developer text.  It also
        bypasses the UI probe so enabling it can never invalidate a layout
        audit.
        """
        if not self.show_diag:
            return
        summary = self.diag.summary()
        mem = memory_mb() if self._diag_memory else None
        lines = overlay_lines(summary, entity_counts(self), mem)
        rect = pygame.Rect(*overlay_box(lines, self.font_small.size,
                                        self.font_small.get_height(), W, H))
        probe = self.ui_probe
        self.ui_probe = None
        try:
            pygame.draw.rect(surf, (8, 10, 16), rect)
            pygame.draw.rect(surf, (70, 90, 110), rect, width=2)
            yy = rect.top + OVERLAY_PAD
            for text, role in lines:
                self.text(surf, text, self.font_small,
                          DIAG_COLORS.get(role, TEXT_BODY), yy, center=False,
                          x=rect.left + OVERLAY_PAD,
                          style="heading" if role == "heading" else
                          "hint" if role == "hint" else "body")
                yy += OVERLAY_LINE_H
        finally:
            self.ui_probe = probe

    # ------------------------------------------------------------ utils --
    def _probe(self, s, rect, color, style, bg):
        if self.ui_probe is None:
            return
        self.ui_probe.append({
            "text": s, "rect": tuple(rect), "color": tuple(color),
            "style": style, "bg": tuple(bg if bg is not None else UI_BG),
            "panel": tuple(self._panel_rect) if self._panel_rect else None,
        })

    def _sample_bg(self, surf, rect, color):
        """Worst background colour under *rect*, sampled before text is drawn.

        The old checker assumed every string sat on a dark panel.  The title
        menu is painted over the sky, where that assumption was false: white
        labels measured 3.2:1 and the footer 1.3:1.  Sampling the real surface
        turns ``tools/check_ui.py`` into a measurement instead of an alibi.
        """
        x, y, w, h = rect
        worst, worst_ratio = UI_BG, None
        for gx in range(5):
            for gy in range(3):
                px = min(W - 1, max(0, int(x + (w - 1) * gx / 4.0)))
                py = min(H - 1, max(0, int(y + (h - 1) * gy / 2.0)))
                bg = surf.get_at((px, py))[:3]
                ratio = contrast(color, bg)
                if worst_ratio is None or ratio < worst_ratio:
                    worst, worst_ratio = bg, ratio
        return worst

    def text(self, surf, s, font, color, y, center=True, x=None,
             shadow=False, style="body", bg=None, right=None):
        img = font.render(s, True, color)
        if right is not None:
            px = right - img.get_width()
        elif center:
            px = (W - img.get_width()) // 2
        else:
            px = x
        rect = (px, y, img.get_width(), img.get_height())
        if self.ui_probe is not None and bg is None:
            bg = self._sample_bg(surf, rect, color)
        if shadow:
            sh = font.render(s, True, (4, 6, 10))
            surf.blit(sh, (px + 1, y + 2))
        surf.blit(img, (px, y))
        self._probe(s, rect, color, style, bg)
        return img.get_height()

    def quality(self):
        return self.settings.get("quality", 1)

    def motion_on(self):
        return self.settings.get("motion", 1) != 0

    def scene(self, theme):
        return scen.scenery(theme, self.quality(), ON_WASM)

    # ------------------------------------------------------------ glass --
    def _glass(self, size):
        """Cached frosted-glass fill: lifted gradient, rounded, per-pixel alpha."""
        key = ("glass", tuple(size))

        def build():
            w, h = size
            g = pygame.Surface((w, h), pygame.SRCALPHA)
            for y in range(h):
                t = y / float(max(1, h - 1))
                c = tuple(int(UI_BG_TOP[i] + (UI_BG_BOT[i] - UI_BG_TOP[i])
                              * min(1.0, t * 1.6)) for i in range(3))
                g.fill(c + (230,), pygame.Rect(0, y, w, 1))
            # A soft inner light along the top edge: the glass catches it.
            band = min(40, h // 3)
            for y in range(band):
                k = 0.10 * (1.0 - y / float(band)) ** 2
                g.fill((int(150 * k), int(200 * k), int(255 * k), 0),
                       pygame.Rect(0, y, w, 1),
                       special_flags=pygame.BLEND_RGBA_ADD)
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            mask.fill((255, 255, 255, 0))
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                             border_radius=RADIUS)
            g.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            return g

        return fx._cached(key, build)

    def _frost(self, surf, r):
        """Blur what is behind a panel (cheap: scale down, scale back up)."""
        if ON_WASM or self.quality() == 0:
            return
        clip = r.clip(surf.get_rect())
        if clip.width < 16 or clip.height < 16:
            return
        piece = surf.subsurface(clip).copy()
        small = pygame.transform.smoothscale(
            piece, (max(2, clip.width // 10), max(2, clip.height // 10)))
        surf.blit(pygame.transform.smoothscale(small, clip.size), clip.topleft)

    def panel(self, surf, w, h, accent=ACCENT):
        r = pygame.Rect(0, 0, w, h)
        r.center = (W // 2, H // 2)
        return self.panel_rect(surf, r, accent)

    def panel_rect(self, surf, r, accent=ACCENT):
        """A floating glass panel: soft shadow, frosted backdrop, hairlines.

        The accent is a short lit tick on the top edge, not a saturated
        border around the whole dialog.
        """
        r = pygame.Rect(r)
        surf.blit(fx.shadow(r.width + 70, r.height + 60, 120),
                  (r.x - 35, r.y - 14))
        self._frost(surf, r)
        surf.blit(self._glass(r.size), r.topleft)
        pygame.draw.rect(surf, PANEL_EDGE, r, width=1, border_radius=RADIUS)
        pygame.draw.line(surf, PANEL_INNER, (r.left + RADIUS, r.top + 1),
                         (r.right - RADIUS, r.top + 1))
        pygame.draw.rect(surf, accent, (r.left + 26, r.top - 1, 54, 3),
                         border_radius=2)
        # The tick's bloom stays on the panel edge: a wider halo bled onto
        # the first menu row and took a disabled label below 4.5:1.
        scen.light(surf, (r.left + 53, r.top - 2), 52, accent, 2.4, 0.28)
        self._panel_rect = r if self.ui_probe is not None else None
        return r

    def dim(self, surf, alpha=150, top=GAME_TOP, height=GAME_BOT - GAME_TOP):
        ov = fx._cached(("dim", height, alpha), lambda: _alpha_fill(
            (W, height), (4, 6, 12, alpha)))
        surf.blit(ov, (0, top))

    # ------------------------------------------------------------- chrome --
    def _diamond(self, surf, cx, cy, r, colour, glow=True):
        pts = [(cx, cy - r), (cx + r * 0.72, cy), (cx, cy + r),
               (cx - r * 0.72, cy)]
        if glow:
            scen.light(surf, (cx, cy), int(r * 7), colour, 2.2, 0.55)
        pygame.draw.polygon(surf, colour, pts)
        pygame.draw.polygon(surf, (255, 255, 255),
                            [(cx, cy - r), (cx + r * 0.72, cy), (cx, cy - r * 0.2)])

    def heading(self, surf, s, accent, top):
        """Tracked uppercase title flanked by a lit crystal mark."""
        s = str(s).upper()
        tw = self.font_head.size(s)[0]
        gap = 16
        px = (W - (tw + gap + 12)) // 2 + 12 + gap
        cy = top + self.font_head.get_height() // 2 + 1
        self._diamond(surf, px - gap - 2, cy, 6, accent)
        return self.text(surf, s, self.font_head, TEXT_HEAD, top, center=False,
                         x=px, style="heading")

    def section(self, surf, s, accent, x, top):
        """Left-aligned panel title with the same crystal mark."""
        s = str(s).upper()
        cy = top + self.font_head.get_height() // 2 + 1
        self._diamond(surf, x + 5, cy, 6, accent)
        return self.text(surf, s, self.font_head, TEXT_HEAD, top, center=False,
                         x=x + 22, style="heading")

    def hint(self, surf, s, y):
        return self.text(surf, s, self.font_small, TEXT_DIM, y, style="hint")

    def _row_glow(self, size):
        key = ("rowglow", tuple(size))

        def build():
            w, h = size
            g = pygame.Surface((w, h), pygame.SRCALPHA)
            for x in range(w):
                a = int(46 * (1.0 - x / float(w)) ** 1.8)
                if a:
                    g.fill(ACCENT + (a,), pygame.Rect(x, 0, 1, h))
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            mask.fill((255, 255, 255, 0))
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                             border_radius=9)
            g.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            return g

        return fx._cached(key, build)

    def draw_menu(self, surf, menu, top, width, row_h=None, markers=True,
                  x=None):
        """Draw a menu with one visibly filled focus row.

        Focus is a shape, not a colour change: the selected row gets a filled
        plate, a soft lit edge and a 3 px accent bar, so it survives 720p
        downscaling and does not depend on hue alone.  Every row is probed so
        ``tools/check_ui.py`` can prove rows stay inside their panel and never
        overlap.  ``x`` places the list explicitly for two-column screens.
        """
        row_h = row_h or ROW_H
        if x is None:
            x = (W - width) // 2
        fh = self.font_hud.get_height()
        sh = self.font_small.get_height()
        start, end = menu.window()
        for row, item in enumerate(menu.visible_items()):
            i = start + row
            y = top + row * row_h
            rect = pygame.Rect(x, y, width, row_h)
            sel = i == menu.index
            if sel:
                pygame.draw.rect(surf, FOCUS_BG, rect, border_radius=9)
                surf.blit(self._row_glow(rect.size), rect.topleft)
                pygame.draw.rect(surf, FOCUS_BAR,
                                 (rect.x + 1, rect.y + 8, 3, rect.h - 16),
                                 border_radius=2)
                scen.light(surf, (rect.x + 3, rect.centery), 60, FOCUS_BAR,
                           2.2, 0.5)
                fg, bg = FOCUS_FG, FOCUS_BG
                detail_col = (206, 222, 240)
            else:
                fg = TEXT_BODY if item.enabled else TEXT_OFF
                bg = None
                detail_col = TEXT_DIM
            self._probe("", rect, fg, "row", bg if sel else UI_BG)
            ty = y + (row_h - fh) // 2
            self.text(surf, display_case(item.label), self.font_hud, fg, ty,
                      center=False, x=x + 22, bg=bg)
            detail = item.value or item.note
            if detail:
                dty = y + (row_h - sh) // 2 + 1
                self.text(surf, display_case(detail), self.font_small,
                          detail_col, dty, center=False,
                          right=x + width - 20, bg=bg)
        if menu.clipped() and markers:
            # ASCII markers: the bundled bold font has no arrow glyphs, and a
            # missing glyph renders as a tofu box.
            if start > 0:
                self.text(surf, "More above", self.font_small, TEXT_DIM,
                          top - 20, center=False, x=x + 22, style="hint")
            if end < len(menu):
                self.text(surf, "More below", self.font_small, TEXT_DIM,
                          top + (end - start) * row_h + 2, center=False,
                          x=x + 22, style="hint")
        return top + (end - start) * row_h

    def cam_int(self):
        """Camera offset in cells, rounded to whole *pixels* rather than cells.

        Rounding to one pixel keeps the pan smooth and every object on the same
        grid, which is what stops tile shimmer.
        """
        sx, sy = motion.shake_offset(
            # Cosmetic stream, never the module-level one: drawing must not
            # consume gameplay randomness (see docs/ARCHITECTURE.md).
            self.cosmetic_rng, self.shake,
            self.settings.get("shake", 1), self.settings.get("motion", 1))
        cam_px = int(round(self.cam * CELL)) + sx
        return cam_px / float(CELL), sy

    # --------------------------------------------------------- backdrops --
    def draw_sky(self, surf, theme, gh_rows=VIEW_H, cam_px=0):
        """The world's painted sky (sun/moon, stars, glows, far ridge)."""
        self.scene(theme).draw_sky(surf, cam_px)

    def parallax(self, surf, theme, cam, oy, t):
        """Distant layers, light shafts and mist for ``theme`` at ``cam`` cells."""
        tb = motion.background_time(t, self.settings.get("motion", 1))
        self.scene(theme).draw_back(surf, int(cam * CELL), tb)

    def backdrop(self, surf, theme, cam_px, front=True, canopy=False):
        """A full painted world with no playfield: menu and card backgrounds."""
        tb = motion.background_time(self.t, self.settings.get("motion", 1))
        sc = self.scene(theme)
        sc.draw_back(surf, cam_px, tb)
        sc.draw_motes(surf, cam_px, tb)
        if front:
            sc.draw_front(surf, cam_px, tb, canopy=canopy)

    def atmosphere(self, surf, oy=0):
        """Final grade and vignette (kept under its old name for callers)."""
        self.post(surf, self.scene(getattr(self.lvl, "theme", "day")))

    def post(self, surf, sc):
        """Colour grade and vignette: the frame's finishing pass.

        A full-frame bloom cost 3 ms of a 10 ms frame (measured) for a look
        the per-source additive glows already give, so it was dropped.
        """
        q = self.quality()
        if sc is not None and sc.grade:
            surf.fill(sc.grade, special_flags=pygame.BLEND_RGB_ADD)
        if q and not ON_WASM and not (sc is not None and self._lit(sc)):
            surf.blit(_vignette(), (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    # ------------------------------------------------------------- tiles --
    def terrain(self, lvl):
        """The baked terrain for ``lvl`` (rebuilt only when the level changes)."""
        key = (id(lvl), lvl.theme, self.quality(), lvl.w)
        if self._bake is None or self._bake_key != key \
                or self._bake_lvl is not lvl:
            self._bake = terr.TerrainBake(lvl, lvl.theme, self.quality())
            self._bake_key = key
            self._bake_lvl = lvl
            self._prewarm(lvl)
        return self._bake

    def _prewarm(self, lvl):
        """Build a level's sprites while its intro card fades in.

        The first use of a pose, an enemy frame or a prop used to be built
        inside a gameplay frame (measured 7-10 ms spikes on a 3.8 ms median);
        now the level load pays for all of it once.
        """
        for name in art.POSES:
            art.hero_frame(name)
        names = set()
        for kind, _x, _y in lvl.enemies:
            p = profile(kind)
            names.update(p["sprites"])
            if p["wind"]:
                names.add(p["wind"])
        for name in names:
            art.flipped(art.enemy_frame(name))
        for build in (art.coin_orb, art.gem_crystal, art.star_flower,
                      art.beacon_crystal):
            build()
        for state in (0.0, 1.0):
            art.mushroom(state)
        for on in (False, True):
            art.shrine(on)
        for m in lvl.movers:
            art.mover_slab(int(3.0 * CELL), m[2])
        spike_c = rgb(THEMES[lvl.theme]["spike"])
        rim = rim_for(spike_c)
        tip = pal.OUTLINE_LIGHT if rim == pal.OUTLINE_DARK else pal.OUTLINE_DARK
        for seed in range(4):
            art.thorns(spike_c, rim, tip, seed)
        flag_x, flag_y = lvl.flag
        gy = flag_y
        while lvl.tile(flag_x, gy + 1) not in ("#", "B") and gy < lvl.h - 1:
            gy += 1
        art.beacon((gy + 1 - flag_y) * CELL + 24)

    def draw_tiles(self, surf, lvl, theme, cam, oy, t):
        """Baked terrain, then the two hazards that must stay exact.

        Spikes and lava keep their audited palette (ART_BIBLE §3): the thorn
        fill is the one hazard colour, the rim is whichever outline contrasts
        most, and lava keeps its hot surface on every frame.
        """
        cam_px = int(round(cam * CELL))
        self.terrain(lvl).draw(surf, cam_px, oy)
        spike_c = rgb(theme["spike"])
        spike_rim = rim_for(spike_c)
        spike_tip = pal.OUTLINE_LIGHT if spike_rim == pal.OUTLINE_DARK \
            else pal.OUTLINE_DARK
        x0 = max(0, int(cam) - 1)
        x1 = min(lvl.w, int(cam) + VIEW_W + 2)
        for gy in range(lvl.h):
            row = lvl.grid[gy]
            sy = oy + gy * CELL
            for gx in range(x0, x1):
                ch = row[gx]
                if ch == "^":
                    sx = int(round((gx - cam) * CELL))
                    img = art.thorns(spike_c, spike_rim, spike_tip, gx * 7 + gy)
                    surf.blit(img, (sx, sy + CELL - img.get_height()))
                elif ch == "~":
                    sx = int(round((gx - cam) * CELL))
                    self._lava_cell(surf, lvl, gx, gy, sx, sy, t)

    def _lava_cell(self, surf, lvl, gx, gy, sx, sy, t):
        deep = lvl.tile(gx, gy - 1) == "~"
        if deep:
            surf.fill(pal.LAVA_DEEP, (sx, sy, CELL, CELL))
            vein = int(3 + 2 * math.sin(t * 1.7 + gx * 1.3))
            pygame.draw.line(surf, pal.LAVA_BODY, (sx + 2, sy + 6 + vein),
                             (sx + CELL - 3, sy + 9 - vein), 2)
            return
        wave = math.sin(gx * 0.8 + t * 3.2) * 3 if self.motion_on() else 0.0
        top = int(sy + 4 + wave)
        pygame.draw.rect(surf, pal.LAVA_DEEP, (sx, top + 6, CELL, sy + CELL - top - 6))
        pygame.draw.rect(surf, pal.LAVA_BODY, (sx, top, CELL, 8))
        # Bright surface line: lava must not read as dark rock.
        pygame.draw.rect(surf, pal.LAVA_HOT, (sx, top, CELL, 3))
        pygame.draw.line(surf, (255, 236, 170), (sx, top), (sx + CELL - 1, top))
        for k in range(2):
            px = sx + 5 + k * 10
            py = top + 7 + math.sin(t * 5 + gx + k * 2) * 2
            pygame.draw.circle(surf, pal.LAVA_HOT, (int(px), int(py)), 2)

    # ------------------------------------------------------------- items --
    def draw_items(self, surf, cam, oy, t):
        self._draw_props(surf, cam, oy, t)
        self._draw_pickups(surf, cam, oy, t)

    def _draw_props(self, surf, cam, oy, t):
        """Springs, checkpoints, movers and the goal beacon (lit by the world)."""
        lvl = self.lvl
        for m in self.movers:
            mx = int(round((m.x - cam) * CELL))
            my = int(round(oy + m.y * CELL))
            if not -4 * CELL <= mx <= W + CELL:
                continue
            wpx = int(m.W * CELL)
            slab = art.mover_slab(wpx, m.axis)
            surf.blit(slab, (mx - 2, my - 2))
            if self.motion_on() and int(t * 2) % 2 == 0:
                col = (90, 220, 255) if m.axis == "h" else (190, 140, 255)
                cx = mx + wpx // 2
                if m.axis == "h":
                    for s in (-1, 1):
                        ax = cx + s * (wpx // 2 + 6)
                        pygame.draw.polygon(surf, col, [(ax, my), (ax + s * 6, my + 4),
                                                        (ax, my + 8)])
                else:
                    for s in (-1, 1):
                        ay = my + (-8 if s < 0 else 16)
                        pygame.draw.polygon(surf, col, [(cx - 4, ay), (cx + 4, ay),
                                                        (cx, ay + s * 5)])
        for (x, y) in lvl.springs:
            sx = int((x - cam) * CELL)
            if sx < -CELL * 2 or sx > W + CELL:
                continue
            comp = self.spring_anim.get((x, y), 0) > 0
            img = art.mushroom(1.0 if comp else 0.0)
            base = oy + (y + 1) * CELL
            surf.blit(img, (sx + CELL // 2 - img.get_width() // 2,
                            base - img.get_height() + 1))
        for (x, y) in lvl.checkpoints:
            sx = int((x - cam) * CELL)
            if sx < -CELL * 2 or sx > W + CELL:
                continue
            on = (x, y) in self.cp_on
            img = art.shrine(on)
            base = oy + (y + 1) * CELL
            surf.blit(img, (sx + CELL // 2 - img.get_width() // 2,
                            base - img.get_height() + 1))
        flag_x, flag_y = lvl.flag
        gy = flag_y
        while lvl.tile(flag_x, gy + 1) not in ("#", "B") and gy < lvl.h - 1:
            gy += 1
        sx = int((flag_x - cam) * CELL)
        if -3 * CELL <= sx <= W + 3 * CELL:
            top = oy + flag_y * CELL
            bot = oy + (gy + 1) * CELL
            img = art.beacon(bot - top + 24)
            surf.blit(img, (sx + CELL // 2 - img.get_width() // 2,
                            bot - img.get_height() + 1))

    def _beacon_top(self, cam, oy, t):
        flag_x, flag_y = self.lvl.flag
        bob = math.sin(t * 2.0) * 3 if self.motion_on() else 0
        return (int((flag_x - cam) * CELL) + CELL // 2,
                int(oy + flag_y * CELL - 22 + bob))

    def _draw_pickups(self, surf, cam, oy, t):
        """Coins, gems, star power and prism rings: the emissive collectibles."""
        motion_on = self.motion_on()
        glow_on = self.quality() == 1
        for (x, y) in self.powers:
            cxp = (x - cam + 0.5) * CELL
            if cxp < -CELL or cxp > W + CELL:
                continue
            cyp = oy + (y - 0.5) * CELL
            img = art.star_flower()
            if motion_on:
                img = pygame.transform.rotozoom(img, -t * 60.0 % 72.0, 1.0)
            fx.glow(surf, (cxp, cyp), int(56 * GLOW_SCALE) + 8, (255, 220, 140), 70, 2.0)
            surf.blit(img, (int(cxp - img.get_width() / 2),
                            int(cyp - img.get_height() / 2)))
        coin = art.coin_orb()
        cw, chh = coin.get_size()
        for (x, y) in self.coins:
            cxp = (x - cam + 0.5) * CELL
            if cxp < -CELL or cxp > W + CELL:
                continue
            bob = math.sin(t * 2.4 + x * 0.7) * 1.5 if motion_on else 0.0
            cyp = oy + (y + 0.5) * CELL + bob
            if glow_on:
                fx.glow(surf, (cxp, cyp), int(34 * GLOW_SCALE) + 6,
                        (255, 214, 120), 52, 2.2)
            surf.blit(coin, (int(cxp - cw / 2), int(cyp - chh / 2)))
        gem = art.gem_crystal()
        gw, gh = gem.get_size()
        for (x, y) in self.gems:
            cxp = (x - cam + 0.5) * CELL
            if cxp < -CELL or cxp > W + CELL:
                continue
            bob = math.sin(t * 1.9 + x) * 2.0 if motion_on else 0.0
            cyp = oy + (y + 0.5) * CELL + bob
            if glow_on:
                fx.glow(surf, (cxp, cyp), int(64 * GLOW_SCALE) + 8,
                        (90, 220, 255), 64, 2.0)
            surf.blit(gem, (int(cxp - gw / 2), int(cyp - gh / 2)))
        self.draw_resonance(surf, cam, oy, t)

    def draw_resonance(self, surf, cam, oy, t):
        """Hollow prism rings: the one traversal object that is not a diamond.

        Armed rings pulse and carry four rays; spent rings are dim and still.
        Reduced motion freezes the pulse at its midpoint instead of removing
        the object, so readability never depends on animation.
        """
        field = getattr(self, "resonance", None)
        nodes = field.nodes if field is not None else tuple(
            getattr(self.lvl, "resonance", ()))
        motion_on = self.settings.get("motion", 1)
        pulse = motion.prism_pulse(t, motion_on)
        for (x, y) in nodes:
            sx = (x + 0.5 - cam) * CELL
            sy = oy + (y + 0.5) * CELL
            if sx < -CELL or sx > W + CELL:
                continue
            armed = field is None or field.armed((x, y))
            col = pal.PRISM if armed else pal.PRISM_SPENT
            cx, cy = int(sx), int(sy)
            radius = int(7 + (2 if armed else 0) + pulse * 2)
            if armed:
                fx.glow(surf, (cx, cy), radius * 2 + 22, pal.PRISM_GLOW, 70, 1.6)
                pygame.draw.circle(surf, pal.PRISM_GLOW, (cx, cy), radius + 3, 2)
            pygame.draw.circle(surf, col, (cx, cy), radius, 3)
            if armed:
                pygame.draw.circle(surf, pal.PRISM_CORE, (cx, cy), 2)
                ray = radius + 3 + int(pulse * 3)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    pygame.draw.line(surf, pal.PRISM,
                                     (cx + dx * (radius + 2),
                                      cy + dy * (radius + 2)),
                                     (cx + dx * ray, cy + dy * ray), 2)
            if field is not None:
                flash = field.flash_amount(t)
                if flash > 0 and field.last_hit and field.last_hit[0] == (x, y):
                    pygame.draw.circle(surf, pal.PRISM_CORE, (cx, cy),
                                       radius + 5 + int(8 * (1 - flash)), 2)

    # ---------------------------------------------------------- entities --
    def _enemy_view(self, e):
        """``(name, tell, image, origin, flip, feet_px_x, feet_cells_y)``."""
        boss = e.kind in BOSS_KINDS
        name, tell = enemy_pose(e, self.t)
        if e.kind == "sentry":
            flip = getattr(e, "face", 1) < 0
        elif boss:
            flip = e.face < 0
        else:
            flip = getattr(e, "dir", 0) < 0
        cx = e.x + (1.2 if boss else 0.45)
        bottom = e.y + (e.H if boss else 1)
        img = art.enemy_frame(name)
        return name, tell, img, flip, cx, bottom

    def draw_enemies(self, surf, cam, oy, t):
        """Draw every enemy through the shared frame rule (lumenvalley.bestiary).

        The chosen frame and the telegraph come from one pure function, so the
        tell a player sees here is the tell the terminal build draws and the
        one the tests assert.
        """
        for e in self.enemies:
            if not e.alive:
                continue
            sx = e.x - cam
            boss = e.kind in BOSS_KINDS
            span = 9 if boss else 3
            if not -span <= sx <= VIEW_W + span:
                continue
            if boss and e.hurt > 0 and int(e.hurt * 12) % 2:
                continue
            name, tell, img, flip, cx, bottom = self._enemy_view(e)
            if flip:
                img = art.flipped(img)
            w, h = img.get_size()
            px = int(round((cx - cam) * CELL))
            feet = int(math.floor(bottom * CELL)) + int(oy)
            surf.blit(img, (px - w // 2, feet - h + 1))
            if tell > 0.0:
                # Warm means "about to hurt", the same rule the hazards follow.
                # The mark sits above the head and is clamped below the HUD so
                # a tall boss cannot hide its own warning.
                py = max(GAME_TOP + 26, feet - h - 8)
                bar = 12 + int(8 * tell)
                scen.light(surf, (px, py - bar // 2), 44, (255, 150, 60), 2.0, 0.8)
                pygame.draw.rect(surf, (18, 14, 26),
                                 (px - 3, py - bar - 4, 7, bar + 5),
                                 border_radius=3)
                pygame.draw.rect(surf, (255, 156, 64),
                                 (px - 2, py - bar - 3, 5, bar),
                                 border_radius=2)
                pygame.draw.circle(surf, (255, 156, 64), (px, py + 5), 3)

    def player_image(self, pl):
        """The player's sprite for this frame, posed and facing (scale/flip)."""
        frame, sx_, sy_ = motion.player_pose(
            on_ground=pl.on_ground, vx=pl.vx, vy=pl.vy, dash_t=pl.dash_t,
            land_t=pl.land_t, air_t=pl.air_t, distance=pl.run_dist,
            wall_dir=pl.wall_dir, wall_jump_t=pl.wall_jump_t,
            dead=not pl.alive, hurt_t=pl.hurt_t, respawn_t=pl.respawn_t,
            celebrate=pl.celebrate)
        key = ("pose", frame, round(sx_, 2), round(sy_, 2), pl.face < 0)
        img = _POSE_CACHE.get(key)
        if img is None:
            img = art.hero_frame(frame)[0]
            if sx_ != 1.0 or sy_ != 1.0:
                w, h = img.get_size()
                img = pygame.transform.smoothscale(
                    img, (max(2, int(round(w * sx_))), max(2, int(round(h * sy_)))))
            if pl.face < 0:
                img = pygame.transform.flip(img, True, False)
            if len(_POSE_CACHE) > 160:
                _POSE_CACHE.clear()
            _POSE_CACHE[key] = img
        self._pose = (frame, sx_, sy_)
        return img

    def draw_afterimages(self, surf, cam, pl, img, oy=0):
        """Dash trail: tinted echoes at 40 % alpha (ART_BIBLE §5).

        Budgeted from settings: cheap graphics keep one, reduced motion none.
        The dash is strictly horizontal, so offsets behind the player are
        faithful to where it actually was, not a guess.
        """
        offsets = motion.afterimages(self.quality(), self.settings.get("motion", 1))
        if not offsets:
            return
        trail = _tinted_echo(img, (120, 230, 255), motion.AFTERIMAGE_ALPHA)
        w, h = trail.get_size()
        base_x = (pl.x + PLAYER_W / 2 - cam) * CELL - w // 2
        top = int(pl.bottom * CELL) - h + int(oy)
        for k, back in enumerate(offsets):
            surf.blit(trail, (int(base_x - pl.face * back * CELL), top))

    def _player_visible(self):
        pl = self.player
        # Star power (a long invulnerability) glows instead of blinking.
        return not (0 < pl.invuln <= 1.5 and int(pl.invuln * 12) % 2)

    def draw_player(self, surf, cam, oy):
        pl = self.player
        if not self._player_visible():
            return
        img = self.player_image(pl)
        if pl.dash_t > 0:
            self.draw_afterimages(surf, cam, pl, img, oy)
        w, h = img.get_size()
        centre_x = int((pl.x + PLAYER_W / 2 - cam) * CELL)
        feet = int(pl.bottom * CELL) + int(oy)
        surf.blit(img, (centre_x - w // 2, feet - h))

    def _hero_anchor(self, which, cam, oy):
        """Screen position of a hero anchor (``neck``/``crystal``/``head``)."""
        pl = self.player
        frame, sx_, sy_ = getattr(self, "_pose", ("IDLE", 1.0, 1.0))
        meta = art.hero_frame(frame)[1]
        ax, ay = meta[which]
        cx = (pl.x + PLAYER_W / 2 - cam) * CELL + ax * sx_ * pl.face
        cy = pl.bottom * CELL + oy + ay * sy_
        return cx, cy

    def draw_scarf(self, surf, cam, oy):
        """The trailing scarf, simulated in world pixels behind the hero."""
        pl = self.player
        if not self._player_visible():
            return
        self.player_image(pl)
        nx, ny = self._hero_anchor("neck", cam, oy)
        cam_px = cam * CELL
        wind = 1.0 if self.motion_on() else 0.0
        self._scarf.update((nx + cam_px, ny - oy), pl.face, self.t, wind)
        self._scarf.draw(surf, cam_px, -oy)

    def draw_contact_shadow(self, surf, cam, oy):
        """Soft shadow under the player's feet, drawn as world decoration.

        Separate from ``draw_player`` on purpose: that method must stay a single
        sprite blit, which is what the placement gate measures.
        """
        pl = self.player
        if not pl.alive or not pl.on_ground:
            return
        if self.quality() == 0:
            return
        shade = fx.shadow(30, 10, 120)
        centre_x = int((pl.x + PLAYER_W / 2 - cam) * CELL)
        surf.blit(shade, (centre_x - shade.get_width() // 2,
                          int(pl.bottom * CELL) + int(oy) - 6))

    def draw_ghost(self, surf, cam, oy):
        if not self.ghost or self.state != self.PLAY:
            return
        pos = playhead(self.ghost, self.level_time)
        if pos is None:
            return
        gx, gy, face = pos
        img = _tinted_echo(art.hero_frame("IDLE")[0], (170, 230, 255), 105)
        if face < 0:
            img = art.flipped(img)
        w, h = img.get_size()
        cx = int((gx + PLAYER_W / 2 - cam) * CELL)
        feet = int((gy + PLAYER_H) * CELL) + int(oy)
        surf.blit(img, (cx - w // 2, feet - h))

    def draw_particles(self, surf, cam, oy):
        """Simulation particles as soft light: dust puffs and glowing sparks."""
        add = self.quality() == 1
        for pt in self.parts.items:
            x = int(round((pt[0] - cam) * CELL))
            y = int(round(oy + pt[1] * CELL))
            if x < -20 or x > W + 20:
                continue
            life_k = max(0.0, min(1.0, pt[4] * 3))
            col = rgb(pt[6])
            # Quantised size and alpha: a continuous life value made every
            # particle a cache miss (measured: +1.6 ms/frame before quantising).
            bucket = min(3, int(life_k * 3.999))
            dust = pal.saturation(col) < 0.12
            if dust:
                fx.glow(surf, (x, y), 10 + 4 * bucket, (220, 226, 236),
                        40 + 26 * bucket, 1.6)
                continue
            fx.glow(surf, (x, y), 8 + 4 * bucket, col, 80 + 50 * bucket, 2.0)
            if add:
                scen.light(surf, (x, y), 16 + 6 * bucket, col, 2.2, 0.55)
        for s in self.shots:
            x = int(round((s.x - cam) * CELL))
            y = int(round(oy + s.y * CELL))
            for k in range(1, 4):
                tx = x - int(math.copysign(1, s.vx)) * k * 6
                fx.glow(surf, (tx, y), 14 - k * 3, (255, 90, 190), 120 - k * 30, 2.0)
            fx.glow(surf, (x, y), 22, (255, 110, 200), 160, 1.8)
            pygame.draw.circle(surf, (255, 120, 210), (x, y), 5)
            pygame.draw.circle(surf, (255, 236, 250), (x, y), 2)

    # ---------------------------------------------------------- lighting --
    def _lit(self, sc):
        return (sc.ambient is not None and self.quality() == 1
                and not ON_WASM)

    def _ambient(self, sc):
        """Cached ambient base: the night sky stays lit, the ground is dark."""
        key = ("ambient", sc.theme)

        def build():
            base = pygame.Surface((W, H))
            amb = sc.ambient
            open_sky = sc.theme == "night"
            for y in range(H):
                if open_sky:
                    k = max(0.0, min(1.0, (y - 120) / 200.0))
                    k = k * k * (3 - 2 * k)
                else:
                    k = 1.0
                c = tuple(int(255 + (amb[i] - 255) * k) for i in range(3))
                base.fill(c, pygame.Rect(0, y, W, 1))
            # The vignette rides along in the light map: one multiply, not two.
            base.blit(_vignette(), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
            return base

        return fx._cached(key, build)

    def _light_map(self, sc, cam, oy):
        """Ambient darkness with light punched in by every source on screen."""
        if self._lightmap is None:
            self._lightmap = pygame.Surface((W, H))
        lm = self._lightmap
        lm.blit(self._ambient(sc), (0, 0))
        pl = self.player
        if pl.alive or self.state == self.DEAD:
            cx, cy = self._hero_anchor("crystal", cam, oy)
            scen.light(lm, (cx, cy), 460, (210, 236, 255), 1.5, 1.0)
            scen.light(lm, (cx, cy), 160, (255, 255, 255), 1.8, 0.6)
        lvl = self.lvl
        x0 = max(0, int(cam) - 2)
        x1 = min(lvl.w, int(cam) + VIEW_W + 3)
        for gx in range(x0 - x0 % 3, x1, 3):
            for gy in range(lvl.h):
                if lvl.grid[gy][gx] == "~" and lvl.tile(gx, gy - 1) != "~":
                    scen.light(lm, ((gx + 0.5 - cam) * CELL, oy + gy * CELL),
                               240, (255, 150, 90), 1.6, 0.9)
                    break
        for (x, y) in self.gems:
            sx = (x + 0.5 - cam) * CELL
            if -60 < sx < W + 60:
                scen.light(lm, (sx, oy + (y + 0.5) * CELL), 150, (120, 230, 255),
                           1.8, 0.8)
        for (x, y) in self.coins:
            sx = (x + 0.5 - cam) * CELL
            if -40 < sx < W + 40:
                scen.light(lm, (sx, oy + (y + 0.5) * CELL), 80, (255, 210, 130),
                           1.8, 0.6)
        for (x, y) in lvl.checkpoints:
            sx = (x + 0.5 - cam) * CELL
            if -100 < sx < W + 100:
                on = (x, y) in self.cp_on
                scen.light(lm, (sx, oy + (y - 1) * CELL), 260 if on else 120,
                           (140, 255, 220) if on else (120, 140, 170), 1.6,
                           0.9 if on else 0.4)
        bx, by = self._beacon_top(cam, oy, self.t)
        if -200 < bx < W + 200:
            scen.light(lm, (bx, by), 380, (255, 240, 210), 1.5, 1.0)
        for s in self.shots:
            scen.light(lm, ((s.x - cam) * CELL, oy + s.y * CELL), 110,
                       (255, 120, 200), 1.8, 0.8)
        return lm

    def draw_emissive(self, surf, cam, oy):
        """Additive light on top of everything lit: the glowing things glow."""
        q = self.quality()
        t = self.t
        pl = self.player
        if (pl.alive or self.state == self.DEAD) and self._player_visible():
            cx, cy = self._hero_anchor("crystal", cam, oy)
            pulse = 0.85 + 0.15 * math.sin(t * 3.0) if self.motion_on() else 1.0
            scen.light(surf, (cx, cy), int(48 * GLOW_SCALE) + 10, HERO["glow"],
                       2.0, 0.95 * pulse)
            if q:
                scen.light(surf, (cx, cy), int(170 * GLOW_SCALE), HERO["glow"],
                           2.6, 0.32 * pulse)
            if pl.invuln > 1.5:
                hue = (255, 220, 120) if int(t * 8) % 2 else (255, 250, 220)
                scen.light(surf, (cx, cy - 6), int(150 * GLOW_SCALE), hue, 1.8, 0.65)
        lvl = self.lvl
        if q:
            x0 = max(0, int(cam) - 1)
            x1 = min(lvl.w, int(cam) + VIEW_W + 2)
            strip = _lava_glow()
            for gx in range(x0, x1):
                for gy in range(lvl.h):
                    if lvl.grid[gy][gx] == "~" and lvl.tile(gx, gy - 1) != "~":
                        sx = int(round((gx - cam) * CELL))
                        surf.blit(strip, (sx - 6, oy + gy * CELL - strip.get_height() + 10),
                                  special_flags=pygame.BLEND_RGB_ADD)
                        break
            for (x, y) in self.gems:
                sx = (x + 0.5 - cam) * CELL
                if -60 < sx < W + 60:
                    scen.light(surf, (sx, oy + (y + 0.5) * CELL),
                               int(80 * GLOW_SCALE), (90, 220, 255), 2.2, 0.55)
            for (x, y) in self.coins:
                sx = (x + 0.5 - cam) * CELL
                if -40 < sx < W + 40:
                    scen.light(surf, (sx, oy + (y + 0.5) * CELL),
                               int(36 * GLOW_SCALE) + 6, (255, 200, 110), 2.4, 0.42)
            for (x, y) in lvl.checkpoints:
                if (x, y) not in self.cp_on:
                    continue
                sx = (x + 0.5 - cam) * CELL
                if -80 < sx < W + 80:
                    cy = oy + (y + 1) * CELL - 40
                    scen.light(surf, (sx, cy), int(90 * GLOW_SCALE), (120, 255, 210),
                               2.0, 0.7)
                    beam = _beam(18, 150, (90, 220, 190))
                    surf.blit(beam, (int(sx - 9), int(cy - 150)),
                              special_flags=pygame.BLEND_RGB_ADD)
            for e in self.enemies:
                if not e.alive or not -4 <= e.x - cam <= VIEW_W + 4:
                    continue
                name, tell, img, flip, cx, bottom = self._enemy_view(e)
                kind = name.split("_")[0]
                eyes = art.ENEMY_EYES.get(kind, ())
                col = (255, 120, 220)
                if kind == "CHARGER":
                    col = (255, 210, 90) if tell > 0 else (255, 160, 70)
                elif kind == "SENTRY":
                    col = (255, 170, 70) if tell > 0 else (150, 110, 220)
                elif kind == "BOSS":
                    col = (255, 110, 70)
                elif kind == "KING":
                    col = (220, 240, 255)
                elif kind == "FLYER":
                    col = (255, 220, 120)
                elif kind == "SPIKER":
                    col = (255, 220, 230)
                fpx = (cx - cam) * CELL
                fpy = int(math.floor(bottom * CELL)) + oy
                big = 1.0 + 0.8 * tell
                for (ex, ey) in eyes:
                    ex = -ex if flip else ex
                    scen.light(surf, (fpx + ex, fpy + ey), int(20 * big) + 6, col,
                               2.2, 0.75)
        bx, by = self._beacon_top(cam, oy, t)
        if -200 < bx < W + 200:
            crystal = art.beacon_crystal()
            if q:
                beam = _beam(44, max(10, by), (120, 200, 230))
                surf.blit(beam, (bx - 22, 0), special_flags=pygame.BLEND_RGB_ADD)
                scen.light(surf, (bx, by), int(200 * GLOW_SCALE), (180, 240, 255),
                           2.0, 0.65)
            surf.blit(crystal, (bx - crystal.get_width() // 2,
                                by - crystal.get_height() // 2))
            scen.light(surf, (bx, by), int(70 * GLOW_SCALE), (230, 255, 255), 2.0, 0.8)
        for s in self.shots:
            scen.light(surf, ((s.x - cam) * CELL, oy + s.y * CELL),
                       int(46 * GLOW_SCALE) + 8, (255, 90, 190), 2.0, 0.8)

    # --------------------------------------------------------------- vfx --
    def _observe(self):
        """Turn simulation changes into one-shot cosmetic effects.

        Read-only: a coin that left ``self.coins`` this frame becomes a ring of
        light where it was.  Nothing here writes back to the simulation.
        """
        key = (id(self.lvl), getattr(self, "li", None))
        alive = frozenset(i for i, e in enumerate(self.enemies) if e.alive)
        snap = (frozenset(self.coins), frozenset(self.gems),
                frozenset(self.powers), alive, frozenset(self.cp_on),
                self.player.alive)
        if self._obs is None or self._obs_key != key:
            self._obs, self._obs_key = snap, key
            self._vfx = []
            return
        coins, gems, powers, prev_alive, cps, was_alive = self._obs
        now = self.t
        for c in coins - snap[0]:
            self._vfx.append(("coin", c[0] + 0.5, c[1] + 0.5, now))
        for g in gems - snap[1]:
            self._vfx.append(("gem", g[0] + 0.5, g[1] + 0.5, now))
        for p in powers - snap[2]:
            self._vfx.append(("power", p[0] + 0.5, p[1] - 0.5, now))
        for i in prev_alive - alive:
            if i < len(self.enemies):
                e = self.enemies[i]
                boss = e.kind in BOSS_KINDS
                self._vfx.append(("kill", e.x + (1.2 if boss else 0.45),
                                  e.y + (1.0 if boss else 0.5), now))
        for c in snap[4] - cps:
            self._vfx.append(("check", c[0] + 0.5, c[1] - 1.0, now))
        if was_alive and not snap[5]:
            pl = self.player
            self._vfx.append(("death", pl.x + PLAYER_W / 2, pl.y + 1.0, now))
        self._obs = snap
        self._vfx = [v for v in self._vfx if now - v[3] < 1.0][-24:]

    def draw_vfx(self, surf, cam, oy):
        if not self._vfx:
            return
        motion_on = self.motion_on()
        for kind, x, y, t0 in self._vfx:
            age = self.t - t0
            sx = (x - cam) * CELL
            sy = oy + y * CELL
            # ART_BIBLE §5: a pickup's flourish is over in <= 250 ms, so it
            # never hides the player; events that are not pickups may linger.
            spec = {"coin": (0.25, 26, (255, 214, 120)),
                    "gem": (0.25, 52, (110, 236, 255)),
                    "power": (0.25, 60, (255, 230, 150)),
                    "kill": (0.45, 48, (220, 200, 255)),
                    "check": (0.9, 70, (130, 255, 210)),
                    "death": (0.6, 90, (200, 240, 255))}[kind]
            dur, reach, col = spec
            if age < 0 or age > dur:
                continue
            k = age / dur
            if kind == "death" and self.ui_probe is None:
                flash = int(90 * (1.0 - k) ** 2)
                if flash > 2:
                    surf.fill((flash // 2, flash // 2, flash // 2),
                              special_flags=pygame.BLEND_RGB_ADD)
            if not motion_on:
                scen.light(surf, (sx, sy), reach, col, 2.0, 0.6 * (1.0 - k))
                continue
            r = int(6 + reach * (1.0 - (1.0 - k) ** 2) * 0.5)
            a = 1.0 - k
            ring = _ring(r, max(1, int(3 * a) + 1), col, a)
            surf.blit(ring, (int(sx - ring.get_width() / 2),
                             int(sy - ring.get_height() / 2)),
                      special_flags=pygame.BLEND_RGB_ADD)
            scen.light(surf, (sx, sy), int(reach * 1.4), col, 2.2, 0.7 * a)
            if kind == "check":
                beam = _beam(26, int(220 * a) + 10, col)
                surf.blit(beam, (int(sx - 13), int(sy - beam.get_height() + 10)),
                          special_flags=pygame.BLEND_RGB_ADD)

    # --------------------------------------------------------------- hud --
    def _hud_scrim(self):
        def build():
            s = pygame.Surface((W, 104), pygame.SRCALPHA)
            for y in range(104):
                a = int(206 * (1.0 - y / 103.0) ** 1.4)
                s.fill((5, 7, 13, a), pygame.Rect(0, y, W, 1))
            return s
        return fx._cached(("hudscrim",), build)

    def _hud_metric(self, surf, label, value, right, y, color=TEXT_BODY):
        """A small tracked label with a bright right-aligned value.

        Returns the label's left edge so the next metric can be chained
        without a magic x offset.
        """
        vw = self.font_hud.size(value)[0]
        lw = self.font_label.size(label)[0]
        self.text(surf, value, self.font_hud, color, y, center=False,
                  right=right)
        self.text(surf, label, self.font_label, TEXT_DIM, y + 5, center=False,
                  right=right - vw - 10, style="hint")
        return right - vw - 10 - lw

    def _life_crystal(self, surf, cx, cy, full=True):
        col = UI_LIFE if full else (70, 60, 80)
        if full:
            scen.light(surf, (cx, cy), 34, col, 2.2, 0.55)
        pygame.draw.polygon(surf, (60, 20, 40), [(cx, cy - 9), (cx + 7, cy),
                                                 (cx, cy + 9), (cx - 7, cy)])
        pygame.draw.polygon(surf, col, [(cx, cy - 8), (cx + 6, cy), (cx, cy + 8),
                                        (cx - 6, cy)])
        pygame.draw.polygon(surf, (255, 214, 226), [(cx, cy - 8), (cx + 6, cy),
                                                    (cx, cy - 1)])

    def draw_hud(self, surf):
        """A floating HUD over the world: no bar, one soft scrim.

        Three clusters: life crystals, collectibles and the dash/prism state on
        the left; the world name and a progress line in the centre; score, time
        and best on the right.  Columns are chained from measured widths, so a
        longer value cannot slide under its neighbour.
        """
        surf.blit(self._hud_scrim(), (0, 0))
        pad = 26
        mid = 26
        x = pad + 6
        for i in range(min(self.lives, 5)):
            self._life_crystal(surf, x, mid)
            x += 20
        x += 16
        coin = _icon("coin")
        surf.blit(coin, (x, mid - coin.get_height() // 2))
        scen.light(surf, (x + 8, mid), 36, (255, 200, 90), 2.2, 0.55)
        x += coin.get_width() + 8
        cnt = "%02d" % self.coins_n
        self.text(surf, cnt, self.font_hud, UI_COIN, mid - 12, center=False, x=x)
        x += self.font_hud.size(cnt)[0] + 24
        gem = _icon("gem")
        surf.blit(gem, (x, mid - gem.get_height() // 2))
        scen.light(surf, (x + 7, mid), 40, (90, 220, 255), 2.2, 0.55)
        x += gem.get_width() + 8
        gem_txt = "%d" % self.gems_n
        self.text(surf, gem_txt, self.font_hud, UI_GEM, mid - 12, center=False, x=x)
        x += self.font_hud.size(gem_txt)[0] + 28

        dash_ready = self.player.can_dash and self.player.dash_cd <= 0
        ring_c = (x + 7, mid)
        if dash_ready:
            scen.light(surf, ring_c, 40, ACCENT, 2.0, 0.7)
            pygame.draw.circle(surf, ACCENT, ring_c, 7)
            pygame.draw.circle(surf, (236, 252, 255), ring_c, 3)
        else:
            pygame.draw.circle(surf, TEXT_OFF, ring_c, 7, 2)
        dash_label = "DASH READY" if dash_ready else "DASH USED"
        dash_col = ACCENT if dash_ready else TEXT_OFF
        self.text(surf, dash_label, self.font_label, dash_col, mid - 9,
                  center=False, x=x + 20, style="hint")
        after_dash = x + 20 + self.font_label.size(dash_label)[0] + 24
        if getattr(self, "resonance", None) is not None \
                and self.resonance.chain > 0:
            chain_label = "PRISM x%d" % self.resonance.chain
            self.text(surf, chain_label, self.font_label, pal.PRISM,
                      mid - 9, center=False, x=after_dash, style="hint")
            after_dash += self.font_label.size(chain_label)[0] + 24
        if self.muted:
            # Chained from the measured labels: a fixed offset put MUTE on
            # top of them whenever both were visible.
            self.text(surf, "MUTE", self.font_label, TEXT_DIM, mid - 9,
                      center=False, x=after_dash, style="hint")

        # Centre: the world name and how far through it you are.
        self.text(surf, self.lvl.name.upper(), self.font_title, TEXT_HEAD, 6,
                  style="hint")
        pct = max(0.0, min(1.0, (self.player.x - self.lvl.start[0])
                           / max(1.0, self.lvl.flag[0] - self.lvl.start[0])))
        tw = 280
        tx = (W - tw) // 2
        ty = 36
        pygame.draw.line(surf, (54, 64, 84), (tx, ty), (tx + tw, ty), 2)
        edge = tx + int(tw * pct)
        if pct > 0:
            pygame.draw.line(surf, ACCENT, (tx, ty), (edge, ty), 2)
        scen.light(surf, (edge, ty), 30, ACCENT, 2.0, 0.8)
        pygame.draw.circle(surf, (236, 252, 255), (edge, ty), 3)
        pygame.draw.polygon(surf, UI_GOLD, [(tx + tw + 8, ty - 5), (tx + tw + 12, ty),
                                            (tx + tw + 8, ty + 5), (tx + tw + 4, ty)])

        right = W - pad
        self._hud_metric(surf, "SCORE", "%06d" % self.score, right, 6)
        time_left = self._hud_metric(
            surf, "TIME", fmt_time(self.level_time), right, 34, (214, 220, 232))
        self._hud_metric(surf, "BEST", "%06d" % max(self.hiscore, self.score),
                         time_left - 26, 34, (192, 200, 216))

        # Boss: a slim, glowing health line under the HUD.
        boss = next((e for e in self.enemies
                     if e.kind in ("boss", "king") and e.alive), None)
        boss_on = boss is not None and abs(boss.x - self.player.x) < 42
        if boss_on:
            bw2 = 360
            brr = pygame.Rect((W - bw2) // 2, GAME_TOP + 30, bw2, 6)
            bname = getattr(boss, "display_name", "OBSIDIAN GUARDIAN").upper()
            bcol = (236, 200, 120) if getattr(boss, "king", False) \
                else (214, 140, 236)
            label_w = self.font_label.size(bname)[0]
            plate = pygame.Rect(0, 0, label_w + 30, 24)
            plate.center = (W // 2, GAME_TOP + 14)
            surf.blit(_alpha_fill(plate.size, (6, 8, 14, 196), 10), plate.topleft)
            self.text(surf, bname, self.font_label, bcol, plate.top + 3,
                      style="hint")
            surf.blit(_alpha_fill(brr.inflate(8, 8).size, (6, 8, 14, 196), 6),
                      brr.inflate(8, 8).topleft)
            pygame.draw.rect(surf, (36, 38, 54), brr, border_radius=3)
            fill = int(bw2 * boss.hp / boss.max_hp)
            if fill > 0:
                flash = (255, 160, 170) if boss.hurt > 0 else bcol
                pygame.draw.rect(surf, flash, (brr.x, brr.y, fill, brr.h),
                                 border_radius=3)
                scen.light(surf, (brr.x + fill, brr.centery), 50, flash, 2.0, 0.6)
        self._draw_toast(surf, GAME_TOP + (64 if boss_on else 16))

    def _draw_toast(self, surf, y):
        """Event toasts pop in on a soft plate instead of blinking."""
        if not self.toast_msg:
            self._toast_seen = None
            return
        msg, col, until = self.toast_msg
        if self._toast_seen is None or self._toast_seen[0] != (msg, until):
            self._toast_seen = ((msg, until), self.t)
        age = self.t - self._toast_seen[1]
        if until - self.t <= 0:
            return
        colour = palette_text(rgb(col))
        w = self.font_hud.size(msg)[0]
        # The pop is a settle from slightly large to resting size, so the
        # plate is never narrower than the words it carries.
        grow = min(1.0, age / 0.12) if self.motion_on() else 1.0
        plate = pygame.Rect(0, 0, int(w + 48 + 18 * (1.0 - grow)),
                            int(36 + 6 * (1.0 - grow)))
        plate.center = (W // 2, y + 13)
        surf.blit(_alpha_fill(plate.size, (6, 8, 14, 200), 12), plate.topleft)
        scen.light(surf, (W // 2, y + 13), w + 80, colour, 3.0, 0.25)
        self.text(surf, msg, self.font_hud, colour, y + 1)

    def rgb_theme(self, key):
        return rgb(THEMES[self.lvl.theme][key])

    # ------------------------------------------------------------- world --
    def draw_world(self, surf, include_player=True, hud=True):
        cam, shake_y = self.cam_int()
        oy = GAME_TOP + shake_y
        cam_px = int(round(cam * CELL))
        sc = self.scene(self.lvl.theme)
        tb = motion.background_time(self.t, self.settings.get("motion", 1))
        self._observe()
        sc.draw_back(surf, cam_px, tb)
        self.draw_tiles(surf, self.lvl, self.theme, cam, oy, self.t)
        self._draw_props(surf, cam, oy, self.t)
        self.draw_ghost(surf, cam, oy)
        self.draw_enemies(surf, cam, oy, self.t)
        hero = include_player and (self.player.alive or self.state == self.DEAD)
        if hero:
            self.draw_contact_shadow(surf, cam, oy)
            self.draw_scarf(surf, cam, oy)
            self.draw_player(surf, cam, oy)
        if self._lit(sc):
            # Dark worlds: everything drawn so far is multiplied by a light
            # map, so the hero's crystal, lava and gems carve pools of light
            # out of the gloom.  Emissive pickups and glows come after.
            surf.blit(self._light_map(sc, cam, oy), (0, 0),
                      special_flags=pygame.BLEND_RGB_MULT)
        self._draw_pickups(surf, cam, oy, self.t)
        self.draw_emissive(surf, cam, oy)
        self.draw_particles(surf, cam, oy)
        self.draw_vfx(surf, cam, oy)
        sc.draw_motes(surf, cam_px, tb)
        sc.draw_front(surf, cam_px, tb)
        self.post(surf, sc)
        if hud:
            self.draw_hud(surf)

    # ------------------------------------------------------------- panels --
    def _band(self, surf, top, height, alpha=200):
        """A cinematic horizontal band: dark in the middle, clear at the edges."""
        def build():
            s = pygame.Surface((W, height), pygame.SRCALPHA)
            for y in range(height):
                t = abs(y - height / 2.0) / (height / 2.0)
                a = int(alpha * (1.0 - t ** 2.2))
                s.fill((4, 6, 12, a), pygame.Rect(0, y, W, 1))
            return s
        surf.blit(fx._cached(("band", height, alpha), build), (0, top))

    def draw_intro(self, surf):
        """Level title card: a cinematic band, not a dialog."""
        self.dim(surf, 70)
        self._band(surf, 132, 220, 215)
        if self.li is not None:
            self.text(surf, "WORLD %d OF %d" % (self.li + 1, len(self.levels)),
                      self.font_label, TEXT_DIM, 168, style="hint")
        name = self.lvl.name.upper()
        img = _logo_text(self.font_head, name, (250, 252, 255), (150, 220, 255))
        surf.blit(img, ((W - img.get_width()) // 2, 196))
        nw = self.font_head.size(name)[0]
        for s in (-1, 1):
            x0 = W // 2 + s * (nw // 2 + 30)
            x1 = x0 + s * 140
            pygame.draw.line(surf, (80, 120, 160), (x0, 212), (x1, 212))
            self._diamond(surf, x0 - s * 4, 212, 4, ACCENT)
        best = self.best_times[self.li] if self.li is not None else None
        if best is not None:
            note = "Best time  %s%s" % (
                fmt_time(best), "   ·   Race the ghost" if self.ghost else "")
            self.text(surf, note, self.font_small, TEXT_DIM, 248, style="hint")
        dots = "." * max(1, int(self.st_t * 3))
        self.text(surf, "Get ready " + dots, self.font_hud, TEXT_BODY, 290)

    def draw_pause(self, surf):
        self.dim(surf, 120)
        r = self.panel(surf, 520, 430)
        self.heading(surf, "PAUSED", ACCENT, r.top + 24)
        self.draw_menu(surf, self.pause_menu, r.top + 84, 440)
        self.hint(surf, HINTS["pause"], r.bottom - 34)

    def draw_settings(self, surf):
        if self.settings_back == self.PAUSE:
            self.draw_world(surf)
        else:
            self.backdrop(surf, "day", int(self.t * 30))
        self.dim(surf, 110, 0, H)
        start, end = self.settings_menu.window()
        extra = 92 if self.settings_menu.clipped() else 72
        r = self.panel(surf, 600, 74 + (end - start) * 32 + extra)
        self.heading(surf, "SETTINGS", ACCENT, r.top + 18)
        self.draw_menu(surf, self.settings_menu, r.top + 74, 520, row_h=32)
        self.hint(surf, HINTS["settings"], r.bottom - 32)

    def _medal(self, surf, cx, cy, medal):
        col = {"GOLD": UI_GOLD, "SILVER": UI_SILVER, "BRONZE": UI_BRONZE}.get(medal)
        if col is None:
            return
        scen.light(surf, (cx, cy), 120, col, 2.0, 0.55)
        pygame.draw.circle(surf, _shade(col, 0.45), (cx, cy), 19)
        pygame.draw.circle(surf, col, (cx, cy), 16)
        pygame.draw.circle(surf, _shade(col, 0.7), (cx, cy), 11, 2)
        self._diamond(surf, cx, cy, 6, (255, 255, 255), glow=False)

    def draw_complete(self, surf):
        self.dim(surf, 140)
        s = self.stats or dict(time=0, time_bonus=0, coins=0, gems=0,
                               level_bonus=0)
        r = self.panel(surf, 1040, 440, UI_GOOD)
        self.heading(surf, "LEVEL CLEAR", UI_GOOD, r.top + 18)
        if self.li is not None:
            best = self.best_times[self.li]
            bt = fmt_time(best) if best is not None else "--:--"
            if getattr(self, "new_best_time", False):
                bt += "  · new best"
        else:
            bt = "--:--"
        medal = getattr(self, "medal", "") or "—"
        grade = getattr(self, "grade", "") or "—"
        medal_col = {"GOLD": UI_GOLD, "SILVER": UI_SILVER,
                     "BRONZE": UI_BRONZE}.get(medal, TEXT_BODY)
        grade_col = {"S": UI_GOLD, "A": UI_GOOD, "B": ACCENT,
                     "C": UI_WARN, "D": UI_BAD}.get(grade, TEXT_BODY)
        rows = [("Time", fmt_time(s["time"]), TEXT_BODY),
                ("Best", bt, TEXT_DIM),
                ("Coins", "%d / %d" % (self.coins_n,
                                       self.level_coins_total), UI_COIN),
                ("Gems", "%d / %d" % (self.gems_n,
                                      self.level_gems_total), UI_GEM),
                ("Deaths", str(getattr(self, "deaths_level", 0)), TEXT_BODY),
                ("Medal", display_case(medal), medal_col),
                ("Grade", grade, grade_col)]
        # Challenges are campaign metadata; a custom or editor run has none.
        campaign = (self.li is not None and not self.playing_custom
                    and not self.test_mode)
        if campaign:
            rows.append(("Challenges",
                         "%d / %d" % self._challenge_progress(self.li),
                         ACCENT))
        left = r.left + 44
        for i, (label, value, col) in enumerate(rows):
            yy = r.top + 70 + i * 30
            if i % 2 == 0:
                surf.blit(_alpha_fill((480, 28), (255, 255, 255, 7), 6),
                          (left - 12, yy - 2))
            self.text(surf, label, self.font_small, TEXT_DIM, yy + 2,
                      center=False, x=left, style="hint")
            self.text(surf, value, self.font_hud, col, yy,
                      center=False, right=left + 456)
        self._medal(surf, left + 300, r.top + 70 + 5 * 30 + 13,
                    getattr(self, "medal", ""))
        self.text(surf, "Total score  %06d" % self.score, self.font_hud,
                  ACCENT, r.top + 314, center=False, x=left)
        if self.save_note == "SAVE FAILED":
            note, note_col = "Save failed", UI_BAD
        elif self.record_note:
            note = self.record_note
            if note.startswith("ACH: "):
                note = "Achievement: " + note[5:]
            note, note_col = display_case(note), ACCENT
        elif self.new_record:
            note, note_col = "New best score!", UI_GOOD
        else:
            note, note_col = display_case(self.save_note), TEXT_DIM
        if note:
            self.text(surf, note[:64], self.font_small, note_col,
                      r.top + 356, center=False, x=left, style="hint")
        menu_x = r.right - 440
        pygame.draw.line(surf, (44, 58, 80), (menu_x - 30, r.top + 70),
                         (menu_x - 30, r.bottom - 70))
        upcoming = ""
        if self.li is not None and not self.playing_custom and not self.test_mode:
            nxt = self.li + 1
            if nxt < len(self.levels):
                upcoming = self.levels[nxt].name
        self.text(surf, ("NEXT: " + upcoming) if upcoming else "CONTINUE",
                  self.font_small, TEXT_DIM, r.top + 78,
                  center=False, x=menu_x, style="hint")
        self.draw_menu(surf, self.results_menu, r.top + 108, 400, x=menu_x)
        self.hint(surf, HINTS["results"], r.bottom - 32)

    def draw_credits(self, surf):
        self.backdrop(surf, "cave", int(self.t * 40))
        self.dim(surf, 80, 0, H)
        r = self.panel(surf, 860, 420, ACCENT)
        self.heading(surf, "CREDITS", ACCENT, r.top + 22)
        self.text(surf, "LUMENVALLEY", self.font_title, ACCENT,
                  r.top + 70, style="hint")
        lines = [
            ("Game design & code", "Siam (iams1am)"),
            ("Engine", "Python + pygame-ce"),
            ("Browser build", "Pygbag / WebAssembly"),
            ("Art", "Procedural painting, built in code"),
            ("Music & SFX", "Procedural synthesis"),
            ("Type", "FreeSans Bold (GNU Free Font)"),
            ("Thanks", "Every player and playtester"),
        ]
        yy = r.top + 112
        for role, who in lines:
            self.text(surf, role, self.font_small, TEXT_DIM, yy,
                      center=False, x=r.left + 70, style="hint")
            self.text(surf, who, self.font_small, TEXT_BODY, yy,
                      center=False, right=r.right - 70)
            yy += 32
        self.text(surf, "Thanks for playing", self.font_hud, ACCENT,
                  r.top + 356)
        self.hint(surf, HINTS["credits"], r.bottom - 32)

    def draw_records(self, surf):
        self.backdrop(surf, "night", int(self.t * 30))
        self.dim(surf, 60, 0, H)
        menu = self.records_menu
        start, end = menu.window()
        clipped = menu.clipped()
        list_top = 96 if clipped else 74
        lr = pygame.Rect(40, 10, 800, 460)
        self.panel_rect(surf, lr, ACCENT)
        self.section(surf, menu.title, ACCENT, lr.left + 24, lr.top + 14)
        if clipped:
            self.text(surf, "%d-%d of %d" % (start + 1, end, len(menu)),
                      self.font_small, TEXT_DIM, lr.top + 22,
                      center=False, right=lr.right - 24, style="hint")
        self.draw_menu(surf, menu, lr.top + list_top, 760, x=lr.left + 20)
        self.text(surf, menu.footer, self.font_small, TEXT_DIM,
                  lr.bottom - 32, center=False, x=lr.left + 24, style="hint")

        pr = pygame.Rect(880, 10, 680, 460)
        self.panel_rect(surf, pr, ACCENT)
        self.section(surf, "CAREER RECORD", ACCENT, pr.left + 40,
                     pr.top + 14)
        yy = pr.top + 64
        for i, line in enumerate(self._record_summary()):
            self.text(surf, line, self.font_small,
                      TEXT_BODY if i < 2 else TEXT_DIM, yy,
                      center=False, x=pr.left + 40, style="hint")
            yy += 30
        cur = menu.current()
        if cur is not None and cur.action.startswith("ach:"):
            earned = cur.value == "EARNED"
            self.section(surf, cur.label, ACCENT, pr.left + 40,
                         pr.top + 186)
            self.text(surf, display_case(cur.value), self.font_hud,
                      UI_GOOD if earned else UI_BAD,
                      pr.top + 238, center=False, x=pr.left + 40)
            self.text(surf, display_case(self._records_detail()),
                      self.font_hud, TEXT_BODY,
                      pr.top + 276, center=False, x=pr.left + 40)
        else:
            self.section(surf, "OPTIONAL MASTERY", ACCENT, pr.left + 40,
                         pr.top + 186)
            self.text(surf, "Challenges never gate a level.", self.font_hud,
                      TEXT_BODY, pr.top + 238, center=False, x=pr.left + 40)
        self.text(surf, "Ghosts replay your fastest clear.", self.font_small,
                  TEXT_DIM, pr.bottom - 90, center=False, x=pr.left + 40,
                  style="hint")
        self.text(surf, "Personal bests only - no global board.",
                  self.font_small, TEXT_DIM, pr.bottom - 62, center=False,
                  x=pr.left + 40, style="hint")

    def draw_confirm(self, surf):
        # The backdrop is context, not part of the modal: auditing it would
        # count the pause menu's rows as a second focus bar and a second list.
        probe = self.ui_probe
        self.ui_probe = None
        if self.confirm_back == self.PAUSE:
            self.draw_pause(surf)
        elif self.confirm_back == self.SETTINGS:
            self.draw_settings(surf)
        else:
            self.draw_title(surf)
        self.ui_probe = probe
        self.dim(surf, 120, 0, H)
        r = self.panel(surf, 920, 230, UI_WARN)
        self.heading(surf, "ARE YOU SURE?", UI_WARN, r.top + 16)
        self.text(surf, display_case(self.pending_prompt), self.font_hud,
                  TEXT_BODY, r.top + 60)
        self.draw_menu(surf, self.confirm_menu, r.top + 98, 360, row_h=38)
        self.hint(surf, HINTS["confirm"], r.bottom - 30)

    def draw_gameover(self, surf):
        self.backdrop(surf, "dusk", int(self.t * 20))
        self.dim(surf, 90, 0, H)
        r = self.panel(surf, 640, 340, UI_BAD)
        self.heading(surf, "GAME OVER", UI_BAD, r.top + 28)
        pygame.draw.line(surf, (54, 66, 88), (r.left + 80, r.top + 86),
                         (r.right - 80, r.top + 86))
        left, right = r.left + 110, r.right - 110
        for i, (label, value, col) in enumerate((
                ("Score", "%06d" % self.score, TEXT_BODY),
                ("Best", "%06d" % self.hiscore, UI_GOLD))):
            yy = r.top + 116 + i * 58
            self.text(surf, label, self.font_small, TEXT_DIM, yy + 10,
                      center=False, x=left, style="hint")
            self.text(surf, display_case(value), self.font_ui, col, yy,
                      center=False, right=right)
        self.hint(surf, HINTS["gameover"], r.bottom - 46)

    def draw_victory(self, surf):
        self.backdrop(surf, "dawn", int(self.t * 30))
        for pt in self.parts.items:
            x = pt[0] * CELL
            y = pt[1] * CELL
            scen.light(surf, (x, y), 26, rgb(pt[6]), 2.0, 0.9)
        self.parts.update(STEP)
        self.dim(surf, 60, 0, H)
        r = self.panel(surf, 820, 420, ACCENT)
        self.heading(surf, "LUMENVALLEY IS FREE", ACCENT,
                     r.top + 34)
        pygame.draw.line(surf, (54, 66, 88), (r.left + 80, r.top + 94),
                         (r.right - 80, r.top + 94))
        new_best = getattr(self, "new_best_run", False)
        rows = [("Final score", "%06d" % self.score, TEXT_BODY)]
        if new_best:
            rows.append(("Clear time  ·  new best",
                         fmt_time(self.run_time), ACCENT))
        elif self.best_total is not None:
            rows.append(("Clear time",
                         "%s  (best %s)" % (fmt_time(self.run_time),
                                            fmt_time(self.best_total)),
                         TEXT_BODY))
        else:
            rows.append(("Clear time", fmt_time(self.run_time), TEXT_BODY))
        rows.append(("Best score", "%06d" % self.hiscore, UI_GOLD))
        left, right = r.left + 110, r.right - 110
        for i, (label, value, col) in enumerate(rows):
            yy = r.top + 124 + i * 54
            self.text(surf, label, self.font_small, TEXT_DIM, yy + 9,
                      center=False, x=left, style="hint")
            self.text(surf, value, self.font_ui, col, yy,
                      center=False, right=right)
        self.hint(surf, HINTS["victory"], r.top + 340)

    def draw_help(self, surf):
        self.backdrop(surf, "cave", int(self.t * 40))
        self.dim(surf, 50, 0, H)
        r = self.panel(surf, 1000, 440, ACCENT)
        self.heading(surf, "HOW TO PLAY", ACCENT, r.top + 22)
        lines = [
            ("Reach the light beacon at the end of each level.", TEXT_BODY),
            ("Stomp enemies from above. Avoid thorns and lava.", TEXT_BODY),
            ("Chargers rush when they spot you.", TEXT_DIM),
            ("Spikers are armored - even stomping them hurts!", UI_BAD),
            ("Left/right or A/D move · Space/W jump · "
             "wall-jump on walls", TEXT_DIM),
            ("X/L air dash (once per jump) · Down/S drop through "
             "platforms", TEXT_DIM),
            ("Mushrooms launch you high · Shrines save your progress",
             TEXT_DIM),
            ("Prism rings: dash through to refill and chain 100/200/300",
             ACCENT),
            ("Esc pause · M sound · R restart level", TEXT_DIM),
            ("Scoring: coin 100 · gem 500 · stomp 200 · "
             "plus time bonus", UI_WARN),
            ("Medals: Gold <= 60% par · Silver <= 80% · "
             "Bronze <= 100%", UI_WARN),
            ("Challenges are optional; Records lists them and your ghosts",
             TEXT_DIM),
            ("You have 3 lives per run. Good luck!", UI_BAD),
        ]
        yy = r.top + 76
        for i, (txt, col) in enumerate(lines):
            self.text(surf, txt, self.font_small, col, yy, center=False,
                      x=r.left + 80, style="hint")
            yy += 22
            if i in (1, 3, 6, 9):
                yy += 6
        self.hint(surf, HINTS["help"], r.bottom - 32)

    def _selected_level(self):
        """Return ``(Level, campaign_index)`` for the focused level row."""
        cur = self.level_menu.current()
        if cur is None:
            return None, None
        if cur.action.startswith("level:"):
            i = int(cur.action.split(":", 1)[1])
            return self.levels[i], i
        if cur.action.startswith("trial:"):
            i = int(cur.action.split(":", 1)[1])
            return self.levels[i], i
        if cur.action.startswith("custom:"):
            j = int(cur.action.split(":", 1)[1])
            entry = self.customs[j]
            return Level(entry["name"], entry.get("theme", "day"),
                         entry["rows"]), None
        return None, None

    def _thumb_sky(self, theme):
        sky = self._skies.get(theme)
        if sky is None:
            sky = scen.build_sky(theme)
            self._skies[theme] = sky
        return sky

    def draw_level_thumb(self, surf, lvl, rect):
        """The level's real opening screen, painted, then scaled down.

        The first 80 columns are baked with the same terrain renderer the
        level uses, over its own sky, with its coins, gems and the start
        marked from the data the simulation reads.
        """
        key = (lvl.name, lvl.theme, hash(tuple(lvl.grid)), rect.size)
        img = self._thumbs.get(key)
        if img is None:
            big = pygame.Surface((W, H))
            big.blit(self._thumb_sky(lvl.theme), (0, 0))
            bake = terr.TerrainBake(lvl, lvl.theme, self.quality(), 0, VIEW_W)
            bake.draw(big, 0, GAME_TOP)
            coin = art.coin_orb()
            gem = art.gem_crystal()
            for x, y in lvl.coins:
                if x < VIEW_W:
                    big.blit(coin, (x * CELL + 1, GAME_TOP + y * CELL + 1))
            for x, y in lvl.gems:
                if x < VIEW_W:
                    big.blit(gem, (x * CELL - 1, GAME_TOP + y * CELL - 4))
            hero = art.hero_frame("IDLE")[0]
            sx, sy = lvl.start
            big.blit(hero, (int((sx + 0.42) * CELL) - hero.get_width() // 2,
                            GAME_TOP + (sy + 2) * CELL - hero.get_height()))
            top = GAME_TOP + 2 * CELL
            crop = big.subsurface((0, top, W, GAME_BOT - 20 - top))
            img = pygame.transform.smoothscale(crop, rect.size)
            if len(self._thumbs) > 24:
                self._thumbs.clear()
            self._thumbs[key] = img
        surf.blit(img, rect.topleft)
        pygame.draw.rect(surf, (90, 120, 150), rect, 1, border_radius=4)

    def draw_levelsel(self, surf):
        self.backdrop(surf, "night", int(self.t * 30))
        self.dim(surf, 40, 0, H)
        menu = self.level_menu
        start, end = menu.window()
        clipped = menu.clipped()
        list_top = 96 if clipped else 74
        lr = pygame.Rect(40, 10, 800, 460)
        self.panel_rect(surf, lr, ACCENT)
        self.section(surf, menu.title, ACCENT, lr.left + 24, lr.top + 14)
        if clipped:
            self.text(surf, "%d-%d OF %d" % (start + 1, end, len(menu)),
                      self.font_small, TEXT_DIM, lr.top + 22,
                      center=False, right=lr.right - 24, style="hint")
        self.draw_menu(surf, menu, lr.top + list_top, 760, x=lr.left + 20)
        self.text(surf, menu.footer, self.font_small, TEXT_DIM,
                  lr.bottom - 32, center=False, x=lr.left + 24,
                  style="hint")

        lvl, ci = self._selected_level()
        pr = pygame.Rect(880, 10, 680, 460)
        self.panel_rect(surf, pr, ACCENT)
        self.section(surf, "LEVEL PREVIEW", ACCENT, pr.left + 40, pr.top + 14)
        if lvl is None:
            self.text(surf, "NO RECORDS YET", self.font_ui, TEXT_DIM,
                      pr.top + 180, center=False, x=pr.left + 40)
            return
        thumb = pygame.Rect(pr.left + 40, pr.top + 58, pr.width - 80, 140)
        self.draw_level_thumb(surf, lvl, thumb)
        self.text(surf, display_case(lvl.name), self.font_hud, ACCENT,
                  pr.top + 210, center=False, x=pr.left + 40)
        if ci is not None:
            locked = ci > self.unlocked
            cleared = self.cleared[ci]
            best = self.best_times[ci]
            medal = medal_for(best, PAR_TIMES[ci]) if best is not None else ""
            status = ("LOCKED" if locked else
                      ("CLEARED" if cleared else
                       ("UNPLAYED" if best is None else "BEST SET")))
            par = PAR_TIMES[ci]
            done, total = self._challenge_progress(ci)
            lines = [
                ("BEST  %s" % (fmt_time(best) if best is not None
                               else "--:--"), (232, 236, 244)),
                ("MEDAL  %s" % (medal or "—"),
                 {"GOLD": UI_GOLD, "SILVER": UI_SILVER,
                  "BRONZE": UI_BRONZE}.get(medal, TEXT_BODY)),
                (pace_line(par, fmt_time), TEXT_DIM),
                ("COINS  %d / %d" % (self.best_coins[ci],
                                     len(lvl.coins)), UI_COIN),
                ("GEMS  %d / %d" % (self.best_gems[ci],
                                    len(lvl.gems)), UI_GEM),
            ]
            threats = threat_count(lvl)
            band = ("LOW" if threats <= 7 else
                    "MEDIUM" if threats <= 12 else "HIGH")
            lines.append(("THREATS  %d  (%s)" % (threats, band),
                          UI_WARN if band == "HIGH" else TEXT_BODY))
            lines.append(("CHALLENGES  %d / %d   STATUS  %s"
                          % (done, total, status),
                          UI_BAD if locked else
                          UI_GOOD if cleared else TEXT_BODY))
        else:
            lines = [("CUSTOM LEVEL", TEXT_DIM),
                     ("COINS  %d" % len(lvl.coins), UI_COIN),
                     ("GEMS  %d" % len(lvl.gems), UI_GEM),
                     ("THREATS  %d" % threat_count(lvl), TEXT_BODY),
                     ("PLAY TO SET A RECORD", TEXT_DIM)]
        for i, (txt, col) in enumerate(lines):
            if i < 7:
                self.text(surf, txt, self.font_small, col,
                          pr.top + 248 + i * 24, center=False,
                          x=pr.left + 40, style="hint")

    def _title_scrim(self):
        def build():
            s = pygame.Surface((W, H), pygame.SRCALPHA)
            for y in range(H):
                top = max(0.0, 1.0 - y / 190.0) ** 1.3
                bot = max(0.0, (y - 330) / 150.0) ** 1.6
                a = int(200 * top + 150 * bot)
                if a:
                    s.fill((4, 7, 12, min(230, a)), pygame.Rect(0, y, W, 1))
            return s
        return fx._cached(("titlescrim",), build)

    def _title_hero(self, surf, t):
        """The Wayfarer on a mossy outcrop, scarf in the wind."""
        k = 3.0
        ledge = _title_ledge()
        lx, ly = 40, H - ledge.get_height() + 6
        surf.blit(ledge, (lx, ly))
        breathe = math.sin(t * 1.6) * 1.2 if self.motion_on() else 0.0
        img, meta = art.hero_frame("IDLE", k)
        feet = (lx + 170, ly + 36 + breathe)
        ox, oy = art.HERO_OX * k, art.HERO_OY * k
        nx, ny = meta["neck"]
        anchor = (feet[0] + nx * k, feet[1] + ny * k)
        self._title_scarf.update(anchor, 1, t, 0.9 if self.motion_on() else 0.0,
                                 gravity=1300.0)
        self._title_scarf.draw(surf, 0, 0, scale=1.0)
        surf.blit(img, (int(feet[0] - ox), int(feet[1] - oy)))
        cx, cy = meta["crystal"]
        pulse = 0.85 + 0.15 * math.sin(t * 2.4) if self.motion_on() else 1.0
        scen.light(surf, (feet[0] + cx * k, feet[1] + cy * k), 90,
                   HERO["glow"], 2.0, 0.9 * pulse)
        scen.light(surf, (feet[0] + cx * k, feet[1] + cy * k), 360,
                   HERO["glow"], 2.6, 0.30 * pulse)

    def draw_title(self, surf):
        cam = int(self.t * 24)
        sc = self.scene("day")
        tb = motion.background_time(self.t, self.settings.get("motion", 1))
        sc.draw_back(surf, cam, tb)
        sc.draw_motes(surf, cam, tb)
        for pt in self.fx.items:
            scen.light(surf, (pt[0] * CELL, pt[1] * CELL), 22, rgb(pt[6]),
                       2.2, 0.7)
        self._title_hero(surf, self.t)
        sc.draw_front(surf, cam, tb, canopy=True)
        self.post(surf, sc)
        surf.blit(self._title_scrim(), (0, 0))
        logo = _logo()
        lx = (W - logo.get_width()) // 2
        surf.blit(logo, (lx, 22))
        if self.motion_on():
            _logo_sweep(surf, logo, (lx, 22), self.t)
        sub = "A journey of light through Lumenvalley"
        sw = self.font_sub.size(sub)[0]
        rule_y = 128
        for s in (-1, 1):
            x0 = W // 2 + s * (sw // 2 + 22)
            pygame.draw.line(surf, (96, 140, 176), (x0, rule_y + 10),
                             (x0 + s * 90, rule_y + 10))
        self.text(surf, sub, self.font_sub, (184, 210, 230), rule_y,
                  style="hint")
        panel = self.panel_rect(surf, pygame.Rect((W - 620) // 2, 172,
                                                  620, 296), ACCENT)
        start, end = self.title_menu.window()
        self.draw_menu(surf, self.title_menu, 186, 540, row_h=36,
                       markers=False)
        left = ("%d-%d of %d" % (start + 1, end, len(self.title_menu))
                if self.title_menu.clipped()
                else "Best %06d  ·  v%s" % (self.hiscore, VERSION))
        self.text(surf, left, self.font_small, TEXT_DIM, 434,
                  center=False, x=panel.left + 26, style="hint")
        self.text(surf, HINTS["title"], self.font_small, TEXT_DIM,
                  434, center=False, right=panel.right - 26, style="hint")

    # ---------------------------------------------------------------- all --
    def draw_editor(self, surf):
        from game import ED_TOOLS, ED_THEMES
        self._ed_init()
        theme = THEMES[ED_THEMES[self.ed_theme_i]]
        self.draw_sky(surf, ED_THEMES[self.ed_theme_i], GAME_H)
        # header
        surf.blit(_alpha_fill((W, 2 * CELL + 8), (6, 8, 14, 220)), (0, 0))
        tch, tname = ED_TOOLS[self.ed_tool]
        blink = int(self.t * 4) % 2 == 0
        self.text(surf, "EDITOR", self.font_hud, (90, 230, 255), 8,
                  center=False, x=14)
        self.text(surf, "[%s] %s" % (tch.strip() or "_", tname),
                  self.font_hud,
                  (255, 220, 90) if blink else (200, 200, 210), 8,
                  center=False, x=170)
        self.text(surf, "THEME " + ED_THEMES[self.ed_theme_i].upper(),
                  self.font_hud, rgb(178), 8, center=False, x=560)
        self.text(surf, "X %d" % self.ed_cx, self.font_small,
                  (170, 170, 185), 14, center=False, x=W - 520)
        self.text(surf, "SPACE PAINT · X ERASE · E/Q TOOL · N THEME",
                  self.font_small, (150, 150, 165), 8, center=False,
                  x=W - 470)
        self.text(surf, "ENTER TEST · V SAVE · ESC EXIT",
                  self.font_small, (150, 150, 165), CELL + 10)
        # grid (raw editor chars incl entity letters)
        ground_c = rgb(theme["ground"])
        grass_c = rgb(theme["grass"])
        for gy in range(GAME_H):
            sy = GAME_TOP + gy * CELL
            for sx_cell in range(VIEW_W):
                gx = self.ed_cam + sx_cell
                if gx >= len(self.ed_grid[0]):
                    break
                ch = self.ed_grid[gy][gx]
                px = sx_cell * CELL
                if ch == "#":
                    above = self.ed_grid[gy - 1][gx] if gy > 0 else " "
                    pygame.draw.rect(surf, ground_c, (px, sy, CELL, CELL))
                    if above not in ("#", "B"):
                        pygame.draw.rect(surf, grass_c,
                                         (px, sy, CELL, CELL // 3))
                elif ch == "B":
                    pygame.draw.rect(surf, rgb(theme["brick_b"]),
                                     (px, sy, CELL, CELL))
                    pygame.draw.rect(surf, rgb(theme["brick_f"]),
                                     (px + 2, sy + 2, CELL - 4,
                                      CELL // 2))
                elif ch == "-":
                    pygame.draw.rect(surf, rgb(theme["plat"]),
                                     (px, sy + 5, CELL, 6),
                                     border_radius=3)
                elif ch == "^":
                    pygame.draw.polygon(
                        surf, rgb(theme["spike"]),
                        [(px, sy + CELL), (px + CELL // 2, sy + 4),
                         (px + CELL, sy + CELL)])
                elif ch == "~":
                    pygame.draw.rect(surf, (140, 24, 28),
                                     (px, sy + 3, CELL, CELL - 3))
                elif ch != " ":
                    col = dict(c=(255, 215, 80), g=(90, 240, 255),
                               s=(255, 150, 60), e=(240, 100, 240),
                               f=(180, 130, 255), h=(255, 160, 40),
                               w=(255, 90, 90), t=(200, 200, 210),
                               Z=(240, 100, 240), b=(240, 100, 240),
                               r=(140, 245, 255),
                               P=(70, 230, 90),
                               F=(255, 90, 90)).get(ch, (235, 235, 245))
                    img = self.font_hud.render(ch, True, col)
                    surf.blit(img, (px + CELL // 2 - img.get_width() // 2,
                                    sy + CELL // 2 - img.get_height() // 2))
        # cursor
        if int(self.t * 4) % 2 == 0:
            cx = (self.ed_cx - self.ed_cam) * CELL
            cy = GAME_TOP + self.ed_cy * CELL
            for dx_, dy_ in ((-4, 0), (4, 0), (0, -4), (0, 4)):
                pygame.draw.line(surf, (255, 255, 255),
                                 (cx + dx_, cy + dy_),
                                 (cx + dx_ * 0.4, cy + dy_), 3)
        # palette strip
        bar = pygame.Rect(0, H - CELL - 10, W, CELL + 10)
        surf.blit(_alpha_fill(bar.size, (6, 8, 14, 220)), bar.topleft)
        px = 14
        for i, (cch, cname) in enumerate(ED_TOOLS):
            sel = i == self.ed_tool
            col = (255, 220, 90) if sel else (170, 175, 190)
            img = self.font_hud.render(cch.strip() or "_", True, col)
            surf.blit(img, (px, bar.y + 8))
            px += max(img.get_width(), CELL) + 8
        if self.toast_msg and self.toast_msg[2] > self.t:
            msg, col, _ = self.toast_msg
            self.text(surf, msg, self.font_ui, rgb(col), 2 * CELL + 16)

    _FADE_INTO = None

    def _transition(self):
        """Start a short fade from black when entering a new scene."""
        st = self.state
        if self.ui_probe is not None:
            self._last_state = st
            return
        last = self._last_state
        self._last_state = st
        if last is None or last == st:
            return
        if st in (self.INTRO, self.TITLE, self.LEVELSEL, self.GAMEOVER,
                  self.VICTORY, self.CREDITS, self.RECORDS, self.HELP):
            if not (st == self.TITLE and last in (self.SETTINGS, self.CONFIRM)):
                self._fade_t0 = self.t

    def draw_all(self, surf):
        surf.fill((6, 8, 14))
        self._panel_rect = None
        self._transition()
        st = self.state
        if st == self.TITLE:
            self.draw_title(surf)
        elif st == self.LEVELSEL:
            self.draw_levelsel(surf)
        elif st == self.EDITOR:
            self.draw_editor(surf)
        elif st == self.SETTINGS:
            self.draw_settings(surf)
        elif st == self.HELP:
            self.draw_help(surf)
        elif st == self.CREDITS:
            self.draw_credits(surf)
        elif st == self.RECORDS:
            self.draw_records(surf)
        elif st == self.CONFIRM:
            self.draw_confirm(surf)
        elif st == self.VICTORY:
            self.draw_victory(surf)
        elif st == self.GAMEOVER:
            self.draw_gameover(surf)
        else:
            # The results panel covers the HUD's whole width and repeats its
            # numbers, so the HUD steps aside instead of peeking out half-cut.
            self.draw_world(surf, hud=st != self.COMPLETE)
            if st == self.INTRO:
                self.draw_intro(surf)
            elif st == self.PAUSE:
                self.draw_pause(surf)
            elif st == self.COMPLETE:
                self.draw_complete(surf)
        if self._fade_t0 is not None and self.ui_probe is None:
            k = (self.t - self._fade_t0) / 0.40
            if k >= 1.0 or k < 0.0:
                self._fade_t0 = None
            else:
                a = int(255 * (1.0 - k) ** 1.6)
                if a > 2:
                    surf.blit(_alpha_fill((W, H), (4, 6, 12, a)), (0, 0))


# ------------------------------------------------------- cached helpers --
_POSE_CACHE = {}


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def palette_text(col):
    """A themed toast colour pushed to stay legible on the dark plate."""
    return pal.ensure_contrast(col, (6, 8, 14), 4.5)


def _alpha_fill(size, rgba, radius=0):
    key = ("afill", tuple(size), tuple(rgba), radius)

    def build():
        s = pygame.Surface(size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        if radius:
            pygame.draw.rect(s, rgba, (0, 0) + tuple(size), border_radius=radius)
        else:
            s.fill(rgba)
        return s

    if rgba[3] < 255 and len(fx._CACHE) > 4000:
        fx._CACHE.clear()
    return fx._cached(key, build)


def _vignette():
    def build():
        n = 48
        small = pygame.Surface((n, n // 3))
        cx, cy = (n - 1) / 2.0, (n // 3 - 1) / 2.0
        for y in range(n // 3):
            for x in range(n):
                dx = (x - cx) / cx
                dy = (y - cy) / cy
                d = min(1.0, (dx * dx * 0.7 + dy * dy * 0.6) ** 0.5)
                v = int(255 - 92 * d ** 2.6)
                small.set_at((x, y), (v, v, v))
        return pygame.transform.smoothscale(small, (W, H))
    return fx._cached(("vigmult",), build)


def _lava_glow():
    def build():
        s = pygame.Surface((CELL + 12, 46))
        s.fill((0, 0, 0))
        for y in range(46):
            k = (y / 45.0) ** 2.2
            for x in range(CELL + 12):
                e = 1.0 - abs(x - (CELL + 11) / 2.0) / ((CELL + 12) / 2.0)
                v = k * max(0.0, e) ** 0.7
                s.set_at((x, y), (int(150 * v), int(64 * v), int(22 * v)))
        return s
    return fx._cached(("lavaglow",), build)


def _beam(width, height, colour):
    height = max(4, int(height))
    key = ("beam", width, height // 8 * 8, tuple(colour))

    def build():
        h = max(4, height // 8 * 8)
        small = pygame.Surface((width, 16))
        small.fill((0, 0, 0))
        for y in range(16):
            k = (y / 15.0) ** 1.5
            for x in range(width):
                e = 1.0 - abs(x - (width - 1) / 2.0) / (width / 2.0)
                v = 0.55 * k * max(0.0, e) ** 1.8
                small.set_at((x, y), tuple(int(c * v) for c in colour))
        return pygame.transform.smoothscale(small, (width, h))

    return fx._cached(key, build)


def _ring(radius, width, colour, k):
    q = round(k * 8) / 8.0
    key = ("ring", radius, width, tuple(colour), q)

    def build():
        size = radius * 2 + 8
        s = pygame.Surface((size, size))
        s.fill((0, 0, 0))
        c = tuple(int(v * q) for v in colour)
        pygame.draw.circle(s, c, (size // 2, size // 2), radius, width)
        return s

    return fx._cached(key, build)


def _tinted_echo(img, colour, alpha):
    key = ("echo", id(img), tuple(colour), alpha)
    hit = fx._CACHE.get(key)
    if hit is not None and hit[0] is img:
        return hit[1]
    mask = img.copy()
    mask.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    out = pygame.Surface(img.get_size(), pygame.SRCALPHA)
    out.fill(tuple(colour) + (255,))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    out.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
    fx._CACHE[key] = (img, out)
    return out


def _icon(kind):
    def build():
        if kind == "coin":
            return pygame.transform.smoothscale(art.coin_orb(), (18, 18))
        return pygame.transform.smoothscale(art.gem_crystal(), (16, 20))
    return fx._cached(("icon", kind), build)


def _logo_text(font, text, top, bottom):
    """Tracked text with a vertical gradient fill and a soft outer glow."""
    key = ("logotext", id(font), text, tuple(top), tuple(bottom))

    def build():
        mask = font.render(text, True, (255, 255, 255))
        w, h = mask.get_size()
        grad = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(h):
            t = y / float(max(1, h - 1))
            c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            grad.fill(c + (255,), pygame.Rect(0, y, w, 1))
        grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        pad = 24
        out = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
        glow = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
        glow.blit(grad, (pad, pad))
        blur = getattr(pygame.transform, "box_blur", None)
        if blur is not None:
            glow = blur(glow, 8)
            glow.fill((120, 210, 255, 0), special_flags=pygame.BLEND_RGB_MULT)
            glow.fill((255, 255, 255, 150), special_flags=pygame.BLEND_RGBA_MULT)
            out.blit(glow, (0, 0))
        out.blit(grad, (pad, pad))
        return out

    return fx._cached(key, build)


_LOGO_FONT = None


def _logo():
    global _LOGO_FONT
    if _LOGO_FONT is None:
        _LOGO_FONT = Type(66, 10.0)
    return _logo_text(_LOGO_FONT, "LUMENVALLEY", (255, 255, 255),
                      (140, 214, 255))


def _logo_sweep(surf, logo, pos, t):
    """A slow band of light that crosses the wordmark every few seconds."""
    period = 6.5
    k = (t % period) / 1.4
    if k > 1.0:
        return
    w, h = logo.get_size()
    band = fx._cached(("sweep", h), lambda: _sweep_band(h))
    x = int(-band.get_width() + (w + band.get_width()) * k)
    mask = fx._cached(("logomask", id(logo)), lambda: _white_mask(logo))
    piece = pygame.Surface((w, h), pygame.SRCALPHA)
    piece.fill((0, 0, 0, 0))
    piece.blit(band, (x, 0))
    piece.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surf.blit(piece, pos)


def _white_mask(img):
    m = img.copy()
    m.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    return m


def _sweep_band(h):
    w = 120
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    for x in range(w):
        e = 1.0 - abs(x - w / 2.0) / (w / 2.0)
        s.fill((255, 255, 255, int(170 * e ** 2)), pygame.Rect(x, 0, 1, h))
    return s


def _title_ledge():
    def build():
        w, h = 420, 130
        m = pygame.Surface((w, h), pygame.SRCALPHA)
        m.fill((0, 0, 0, 0))
        pts = [(0, h), (0, 60), (40, 44), (110, 36), (190, 34), (250, 40),
               (300, 58), (340, 84), (380, 104), (420, 120), (w, h)]
        pygame.draw.polygon(m, (16, 24, 22), pts)
        pygame.draw.polygon(m, (34, 58, 40), [(30, 46), (110, 34), (190, 32),
                                              (250, 38), (240, 46), (110, 44),
                                              (36, 54)])
        pygame.draw.lines(m, (170, 214, 120), False,
                          [(40, 44), (110, 36), (190, 34), (250, 40)], 2)
        for i in range(60):
            x = 36 + i * 3.6
            hgt = 4 + 9 * scen.hash01(i, 5)
            lean = (scen.hash01(i, 6) - 0.5) * 6
            y = 36 + abs(x - 150) * 0.04
            pygame.draw.line(m, (90, 150, 80), (x, y), (x + lean, y - hgt), 1)
        return m
    return fx._cached(("ledge",), build)



def fmt_time(t):
    return "%02d:%02d" % (int(t) // 60, int(t) % 60)


# ----------------------------------------------------------------- main --
AUDIO = None


KEYMAP = {
    pygame.K_LEFT: "LEFT", pygame.K_a: "a",
    pygame.K_RIGHT: "RIGHT", pygame.K_d: "d",
    pygame.K_UP: "UP", pygame.K_w: "w",
    pygame.K_DOWN: "DOWN", pygame.K_s: "s",
    pygame.K_SPACE: "JUMP",
    pygame.K_RETURN: "ENTER", pygame.K_KP_ENTER: "ENTER",
    pygame.K_ESCAPE: "ESC",
    pygame.K_x: "x", pygame.K_l: "l", pygame.K_m: "m",
    pygame.K_r: "r", pygame.K_q: "q", pygame.K_v: "v",
}

PAD_BUTTONS = {0: "JUMP", 1: "x", 2: "ESC", 3: "ENTER",
               6: "ESC", 7: "ENTER"}


def poll_keyboard(pressed):
    """Translate ``pygame.key.get_pressed()`` into held game key names.

    ``pressed`` may be the SDL sequence or any mapping-like object, which
    keeps this function usable from the headless front-end test.
    """
    held = set()
    for key, name in KEYMAP.items():
        try:
            down = pressed[key]
        except (KeyError, IndexError):
            down = False
        if down:
            held.add(name)
    return held


class InputState:
    """Held keys plus one-shot edges for a variable-rate render loop.

    Edges are held pending until at least one fixed simulation step consumes
    them, so a key pressed during a frame that renders without stepping is
    not lost.
    """

    def __init__(self):
        self.prev = set()
        self.pending = set()

    def update(self, pressed, pad=None):
        held = poll_keyboard(pressed)
        if pad:
            held |= set(pad)
        edges = self.pending | (held - self.prev)
        self.prev = held
        self.pending = set(edges)
        return held, edges

    def consume(self):
        self.pending.clear()

MUSIC_STATE_THEME = {
    Game.TITLE: "day", Game.LEVELSEL: "night", Game.HELP: "cave",
    Game.GAMEOVER: "dusk", Game.VICTORY: "day", Game.EDITOR: "day",
}


# Two mixer channels are reserved for music (a crossfade needs both); sfx use
# the rest.  The rules about who gets a voice live in lumenvalley/audio_mix.py.
MUSIC_CHANNELS = mixmod.MUSIC_CHANNELS


class Audio:
    """pygame.mixer backend: looping theme music + layered SFX.

    Phase 10 rules (see ``lumenvalley/audio_mix.py``): samples are all prepared in
    ``init`` so no decode or synthesis happens inside a gameplay frame, the
    music channels are reserved so an sfx cannot cut the track, a theme change
    crossfades, and a priority-3 cue ducks the music while it plays.
    """

    def __init__(self, asset_dir=ASSET_DIR):
        self.ok = False
        self.asset_dir = asset_dir
        self.music = {}
        self.sfx = {}
        self.music_ch = []
        self.active = 0
        self.fade = None
        self.cur_theme = None
        self.enabled = True
        self.master = 0.7
        self.music_vol = 0.6
        self.sfx_vol = 0.8
        self.voices = mixmod.Voices()
        self._duck = 1.0
        self._primed = False
        self._last_t = time.monotonic()
        self.init()

    def set_enabled(self, on):
        self.enabled = bool(on)

    def set_volumes(self, master, music, sfx):
        """Persisted 0-10 steps become mixer gains (game master * bus)."""
        self.master = max(0, min(10, int(master))) / 10.0
        self.music_vol = max(0, min(10, int(music))) / 10.0
        self.sfx_vol = max(0, min(10, int(sfx))) / 10.0

    def init(self):
        """Start the mixer.  Safe to call again from a user gesture."""
        if not self.ok:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=sndmod.RATE, size=-16,
                                      channels=mixmod.TOTAL_CHANNELS,
                                      buffer=512)
                self.ok = True
            except pygame.error:
                self.ok = False
        if self.ok and not self._primed:
            self._configure()
        return self.ok

    def _configure(self):
        """Reserve the music channels and prepare every sample up front."""
        pygame.mixer.set_num_channels(max(mixmod.TOTAL_CHANNELS,
                                         pygame.mixer.get_num_channels()))
        pygame.mixer.set_reserved(mixmod.MUSIC_CHANNELS)
        self.music_ch = [pygame.mixer.Channel(i)
                         for i in range(mixmod.MUSIC_CHANNELS)]
        for name in sndmod.SPECS:
            self._sfx_sample(name)
        for theme in musicmod.TRACKS:
            self._music_sample(theme)
        self._primed = True

    def _sfx_duration(self, name):
        snd = self.sfx.get(name)
        try:
            return float(snd.get_length()) if snd is not None else 0.2
        except pygame.error:
            return 0.2

    def _file(self, *parts):
        path = os.path.join(self.asset_dir, *parts)
        return path if os.path.exists(path) else None

    def _sfx_sample(self, name):
        snd = self.sfx.get(name)
        if snd is not None:
            return snd
        path = self._file("sfx", name + ".ogg")
        try:
            if path:
                snd = pygame.mixer.Sound(path)
            else:
                import io
                wav = io.BytesIO()
                pcm = sndmod.SPECS[name]()
                n = len(pcm)
                wav.write(b"RIFF")
                wav.write(_u32(n + 36))
                wav.write(b"WAVEfmt ")
                wav.write(_u32(16) + _u16(1) + _u16(1) + _u32(sndmod.RATE)
                          + _u32(sndmod.RATE * 2) + _u16(2) + _u16(16))
                wav.write(b"data")
                wav.write(_u32(n))
                wav.write(pcm)
                wav.seek(0)
                snd = pygame.mixer.Sound(file=wav)
        except (OSError, pygame.error):
            return None
        self.sfx[name] = snd
        return snd

    def _music_sample(self, theme):
        snd = self.music.get(theme)
        if snd is not None:
            return snd
        path = self._file("music", theme + ".ogg")
        try:
            if path:
                snd = pygame.mixer.Sound(path)
            else:
                snd = pygame.mixer.Sound(buffer=musicmod.render(theme))
        except (OSError, pygame.error):
            return None
        self.music[theme] = snd
        return snd

    def play_sfx(self, name):
        if not self.ok or not self.enabled or name not in sndmod.SPECS:
            return
        if not self._primed:
            self.init()
        if not self.sfx:
            return
        now = time.monotonic()
        pri = sndmod.PRI.get(name, 1)
        dur = self._sfx_duration(name)
        snd = self._sfx_sample(name)
        if snd is None:
            return
        # Slot i of the policy maps to mixer channel MUSIC_CHANNELS + i, so the
        # reserved music channels can never be taken by a sound effect.
        idx = self.voices.start(name, pri, now, dur)
        if idx is None:
            return
        ch = pygame.mixer.Channel(mixmod.MUSIC_CHANNELS + idx)
        ch.set_volume(self.master * self.sfx_vol * mixmod.voice_gain(pri))
        ch.play(snd)

    # The headless sim calls ``audio.play(name)``; ``play_sfx`` is the mixer
    # method and the name the desktop shell installs globally.
    def play(self, name):
        return self.play_sfx(name)

    # -- music bus ---------------------------------------------------------
    def music_gain(self):
        """Bus gain for music right now: master * music slider * ducking."""
        if not self.enabled:
            return 0.0
        return self.master * self.music_vol * self._duck

    def gain_for(self, channel):
        """Intended gain of music channel 0/1 during the current state."""
        if not self.music_ch:
            return 0.0
        out, inc = 0.0, 1.0
        if self.fade is not None:
            t = self.fade["t"]
            out = 1.0 - mixmod.smoothstep(t / mixmod.FADE_OUT)
            inc = mixmod.smoothstep(t / mixmod.FADE_IN)
        level = self.music_gain()
        return level * (inc if channel == self.active else out)

    def _tick(self, dt):
        if self.fade is None:
            return
        self.fade["t"] += max(0.0, float(dt))
        if self.fade["t"] >= self.fade["dur"]:
            self.fade = None
            if self.music_ch:
                self.music_ch[1 - self.active].stop()

    def _switch_theme(self, theme):
        snd = self._music_sample(theme)
        if snd is None:
            return
        if not self.music_ch:
            self.init()
            if not self.music_ch:
                return
        self.active = 1 - self.active
        ch = self.music_ch[self.active]
        ch.stop()
        ch.set_volume(0.0)
        ch.play(snd, loops=-1)
        self.fade = {"t": 0.0,
                     "dur": max(mixmod.FADE_IN, mixmod.FADE_OUT)}
        self.cur_theme = theme

    def update(self, game):
        if not self.ok:
            return
        self.enabled = not game.muted
        now = time.monotonic()
        self._tick(now - self._last_t)
        self._last_t = now
        self._duck = self.voices.duck(now)
        theme = MUSIC_STATE_THEME.get(game.state) \
            or getattr(game.lvl, "theme", "day")
        if theme != self.cur_theme and theme in musicmod.TRACKS:
            self._switch_theme(theme)
        self._apply_volumes()

    def _apply_volumes(self):
        for i, ch in enumerate(self.music_ch):
            ch.set_volume(max(0.0, min(1.0, self.gain_for(i))))
        sfx_level = self.master * self.sfx_vol
        for i, slot in enumerate(self.voices.slots):
            if slot is None:
                continue
            ch = pygame.mixer.Channel(mixmod.MUSIC_CHANNELS + i)
            if ch.get_busy():
                ch.set_volume(sfx_level * mixmod.voice_gain(slot[1]))


def _u32(v):
    return struct.pack("<I", v)


def _u16(v):
    return struct.pack("<H", v)


def read_pad(pad_keys):
    """Merge connected gamepads into the held-key set."""
    for i in range(pygame.joystick.get_count()):
        try:
            js = pygame.joystick.Joystick(i)
            if not js.get_init():
                js.init()
            hx = hy = 0
            axx = axy = 0
            if js.get_num_hats() > 0:
                hx, hy = js.get_hat(0)
            if js.get_num_axes() > 0:
                axx = js.get_axis(0)
            if js.get_num_axes() > 1:
                axy = js.get_axis(1)
            if hx < -0.5 or axx < -0.4:
                pad_keys.update(("LEFT", "a"))
            if hx > 0.5 or axx > 0.4:
                pad_keys.update(("RIGHT", "d"))
            if hy < -0.5 or axy < -0.4:
                pad_keys.update(("UP", "w", "JUMP"))
            if hy > 0.5 or axy > 0.4:
                pad_keys.update(("DOWN", "s"))
            for btn, name in PAD_BUTTONS.items():
                if btn < js.get_num_buttons() and js.get_button(btn):
                    pad_keys.add(name)
        except pygame.error:
            continue


def main():
    global AUDIO
    pygame.mixer.pre_init(frequency=22050, size=-16, channels=8, buffer=512)
    pygame.init()
    pygame.joystick.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Lumenvalley")
    # The window wears the same painted crystal as the browser tab.
    try:
        icon = pygame.image.load(os.path.join(HERE, "web", "favicon.png"))
    except (OSError, pygame.error):
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.polygon(icon, (90, 230, 255),
                            [(16, 1), (29, 12), (16, 31), (3, 12)])
    pygame.display.set_icon(icon)
    clock = pygame.time.Clock()
    fullscreen = False

    audio = Audio()
    AUDIO = audio
    sndmod.play = audio.play_sfx       # route SFX through the mixer

    game = PygameGame()
    input_state = InputState()
    last = time.perf_counter()
    sim_clock = FixedStepClock(STEP)
    running = True
    while running:
        now = time.perf_counter()
        frame_ms = (now - last) * 1000.0
        steps = sim_clock.advance(now - last)
        last = now
        tapped = set()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                name = KEYMAP.get(ev.key)
                if name:
                    tapped.add(name)
                if ev.key == pygame.K_F11:
                    fullscreen = not fullscreen
                    game.screen_mode = ("fullscreen" if fullscreen
                                        else "window")
                    game._refresh_menus()
                    flags = pygame.FULLSCREEN | pygame.SCALED \
                        if fullscreen else 0
                    screen = pygame.display.set_mode((W, H), flags)
                elif ev.key in (pygame.K_F3, pygame.K_F2, pygame.K_BACKQUOTE):
                    game.toggle_diagnostics()
        # The settings screen only records intent; the window is the view's job.
        if getattr(game, "fullscreen_requests", 0):
            game.fullscreen_requests = 0
            fullscreen = game.screen_mode == "fullscreen"
            flags = pygame.FULLSCREEN | pygame.SCALED if fullscreen else 0
            screen = pygame.display.set_mode((W, H), flags)
        pad = set()
        read_pad(pad)
        keys, edges = input_state.update(pygame.key.get_pressed(),
                                         pad | tapped)
        ran = False
        update_start = time.perf_counter()
        for _ in range(steps):
            if not running:
                break
            if not game.step(STEP, keys, edges):
                running = False
                break
            input_state.consume()
            edges = set()
            ran = True
        update_ms = (time.perf_counter() - update_start) * 1000.0
        audio.update(game)
        render_start = time.perf_counter()
        game.draw_all(screen)
        game.draw_diagnostics(screen)
        render_ms = (time.perf_counter() - render_start) * 1000.0
        pygame.display.flip()
        game.record_frame(frame_ms, update_ms, render_ms)
        if not ran:
            time.sleep(sim_clock.sleep_time())
        clock.tick(120)
    game._save_score()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
