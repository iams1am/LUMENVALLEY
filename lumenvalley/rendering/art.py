"""Painted character, enemy and prop sprites for the pygame/browser renderer.

Every sprite is drawn procedurally at 4x and smooth-scaled down, so edges are
antialiased and shapes can be curved and layered (the old 8x8 pixel grids could
not carry a silhouette at this resolution).  Each frame is built once and
cached.  Sizes follow the *hitboxes* in ``entities.py``: the hero's body is
0.85 x 2 cells, so the sprite is about 30 x 46 px instead of the old 80 x 80
block that drew far outside its own collision box.

The hero is an original design, the Wayfarer: a small hooded traveller with a
teal cloak, a coral scarf and a crystal pendant that lights the way.  Poses are
generated from a tiny rig (lean, two legs, two arms, cloak flare, hood tip), so
all fourteen gameplay states share one silhouette.

Every builder is deterministic and draws nothing at runtime except blits.
"""

import math

import pygame

from .looks import HERO

S = 4                     # supersampling factor
_CACHE = {}

# Collectible outline: near-black (like palette.OUTLINE_DARK), so a pickup
# keeps a silhouette in front of a sunset as well as in a cave.  Measured in
# tests/test_scenery.py: against the worst mid-tone backdrop a cyan gem's fill
# and this outline still give >= 4:1 together.
OUTLINE = (6, 4, 8)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Canvas:
    """A supersampled drawing surface with an origin in final pixels."""

    def __init__(self, w, h, ox, oy, scale=1.0):
        self.k = float(scale)
        self.w, self.h = int(round(w * self.k)), int(round(h * self.k))
        self.ox, self.oy = float(ox), float(oy)
        self.surf = pygame.Surface((self.w * S, self.h * S), pygame.SRCALPHA)
        self.surf.fill((0, 0, 0, 0))

    def p(self, x, y):
        k = self.k * S
        return (int(round((self.ox + x) * k)), int(round((self.oy + y) * k)))

    def poly(self, col, pts):
        if len(pts) >= 3:
            pygame.draw.polygon(self.surf, col, [self.p(*q) for q in pts])

    def circle(self, col, c, r):
        pygame.draw.circle(self.surf, col, self.p(*c), max(1, int(r * S * self.k)))

    def ellipse(self, col, c, rx, ry):
        x, y = self.p(c[0] - rx, c[1] - ry)
        k = S * self.k
        pygame.draw.ellipse(self.surf, col, (x, y, max(1, int(rx * 2 * k)),
                                             max(1, int(ry * 2 * k))))

    def line(self, col, a, b, w, caps=True):
        pa, pb = self.p(*a), self.p(*b)
        ww = max(1, int(w * S * self.k))
        pygame.draw.line(self.surf, col, pa, pb, ww)
        if caps and ww > 2:
            pygame.draw.circle(self.surf, col, pa, ww // 2)
            pygame.draw.circle(self.surf, col, pb, ww // 2)

    def lines(self, col, pts, w):
        if len(pts) >= 2:
            pygame.draw.lines(self.surf, col, False, [self.p(*q) for q in pts],
                              max(1, int(w * S * self.k)))

    def arc(self, col, c, rx, ry, a0, a1, w):
        x, y = self.p(c[0] - rx, c[1] - ry)
        k = S * self.k
        pygame.draw.arc(self.surf, col, (x, y, int(rx * 2 * k), int(ry * 2 * k)),
                        a0, a1, max(1, int(w * k)))

    def finish(self):
        return pygame.transform.smoothscale(self.surf, (self.w, self.h))


def _rot(p, ang, pivot):
    x, y = p[0] - pivot[0], p[1] - pivot[1]
    c, s = math.cos(ang), math.sin(ang)
    return (pivot[0] + x * c - y * s, pivot[1] + x * s + y * c)


# =================================================================== hero ==
THIGH = 7.6
SHIN = 7.4
HERO_W, HERO_H = 40, 54
HERO_OX, HERO_OY = 20, 53        # feet centre inside the canvas

# lean: radians (+ = forward).  legs: ((thigh, shin), (thigh, shin)) for the
# back then the front leg, angles from straight down (+ = forward).  arms:
# (back, front) from straight down (+ = forward/up).  flare: cloak hem spread
# (back, front) px.  hem: hem lift (- = billows up).  tip: hood tip angle.
POSES = {
    "IDLE": dict(lean=0.03, legs=((-0.10, -0.02), (0.12, 0.04)),
                 arms=(-0.15, 0.20), flare=(1.0, 0.5), hem=0.0, tip=0.25,
                 eyes="open"),
    "ANTICIPATE": dict(lean=0.32, legs=((0.55, -0.55), (0.85, -0.45)),
                       arms=(-0.6, 0.3), flare=(3.0, 1.5), hem=1.0, tip=0.05,
                       eyes="open"),
    "RUN_A": dict(lean=0.24, legs=((-0.55, -1.05), (0.62, 0.18)),
                  arms=(0.75, -0.65), flare=(4.5, 0.0), hem=-1.0, tip=-0.05,
                  eyes="open", bob=0.0),
    "RUN_D": dict(lean=0.22, legs=((0.35, -0.85), (0.02, 0.04)),
                  arms=(0.2, -0.1), flare=(4.0, 0.5), hem=-0.5, tip=0.0,
                  eyes="open", bob=-1.0),
    "RUN_B": dict(lean=0.24, legs=((0.62, 0.18), (-0.55, -1.05)),
                  arms=(-0.65, 0.75), flare=(4.5, 0.0), hem=-1.0, tip=-0.05,
                  eyes="open", bob=0.0),
    "RUN_C": dict(lean=0.22, legs=((0.02, 0.04), (0.35, -0.85)),
                  arms=(-0.1, 0.2), flare=(4.0, 0.5), hem=-0.5, tip=0.0,
                  eyes="open", bob=-1.0),
    "JUMP": dict(lean=0.08, legs=((-0.25, 0.05), (1.05, -0.35)),
                 arms=(-0.5, 2.3), flare=(3.0, 2.0), hem=1.5, tip=-0.45,
                 eyes="open", air=True),
    "APEX": dict(lean=0.0, legs=((-0.30, -0.55), (0.55, -0.25)),
                 arms=(-1.5, 1.6), flare=(5.5, 4.0), hem=-2.5, tip=0.35,
                 eyes="wide", air=True),
    "FALL": dict(lean=-0.05, legs=((-0.18, 0.06), (0.22, 0.10)),
                 arms=(-2.5, 2.4), flare=(6.0, 5.0), hem=-4.5, tip=0.95,
                 eyes="wide", air=True),
    "LAND": dict(lean=0.30, legs=((0.40, -0.75), (1.0, -0.55)),
                 arms=(-0.9, 0.7), flare=(6.0, 4.0), hem=1.0, tip=0.15,
                 eyes="open"),
    "DASH": dict(lean=0.85, legs=((-1.25, -1.45), (0.55, -0.35)),
                 arms=(-1.3, -1.0), flare=(10.0, 0.0), hem=-6.0, tip=-0.15,
                 eyes="focus", air=True),
    "WALL": dict(lean=-0.10, legs=((0.10, 0.25), (1.10, 0.55)),
                 arms=(-0.4, 1.75), flare=(2.0, 1.0), hem=-1.5, tip=0.75,
                 eyes="open", air=True),
    "WALL_JUMP": dict(lean=0.42, legs=((-0.75, -0.45), (0.85, -0.55)),
                      arms=(-0.9, 1.9), flare=(7.0, 1.0), hem=-3.0,
                      tip=-0.25, eyes="open", air=True),
    "HURT": dict(lean=-0.50, legs=((-0.35, -0.15), (0.45, 0.55)),
                 arms=(-2.6, 2.7), flare=(4.0, 4.0), hem=-2.0, tip=0.7,
                 eyes="hurt", air=True),
    "DEAD": dict(lean=1.05, legs=((1.45, -1.55), (1.25, -1.45)),
                 arms=(0.4, 0.6), flare=(5.0, 3.0), hem=2.0, tip=-0.2,
                 eyes="closed"),
    "RESPAWN": dict(lean=0.0, legs=((-0.08, 0.0), (0.08, 0.0)),
                    arms=(-0.7, 0.7), flare=(2.0, 2.0), hem=0.0, tip=0.5,
                    eyes="closed"),
    "VICTORY": dict(lean=-0.06, legs=((-0.22, -0.05), (0.25, 0.08)),
                    arms=(-0.9, 3.0), flare=(2.5, 2.0), hem=0.0, tip=0.45,
                    eyes="happy"),
}


def _leg_points(hip, thigh, shin):
    knee = (hip[0] + math.sin(thigh) * THIGH, hip[1] + math.cos(thigh) * THIGH)
    ankle = (knee[0] + math.sin(shin) * SHIN, knee[1] + math.cos(shin) * SHIN)
    return knee, ankle


def hero_frame(name, scale=1.0):
    """``(surface, meta)`` for one hero pose, facing right.

    ``meta`` holds anchor points in sprite pixels relative to the feet
    centre: ``neck`` (scarf root), ``crystal`` (light source), ``head``.
    ``scale`` draws a larger, still-crisp hero (the title screen).
    """
    key = ("hero", name, round(scale, 2))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    P = POSES.get(name, POSES["IDLE"])
    C = HERO
    cv = Canvas(HERO_W, HERO_H, HERO_OX, HERO_OY, scale)
    lean = P["lean"]
    (bt, bs), (ft, fs) = P["legs"]
    # Grounded poses: lower the hip until the lowest foot touches the floor.
    reach = max(math.cos(bt) * THIGH + math.cos(bs) * SHIN,
                math.cos(ft) * THIGH + math.cos(fs) * SHIN)
    if P.get("air"):
        hip_y = -15.0
    else:
        hip_y = -max(4.0, reach) - 0.8
    hip_y += P.get("bob", 0.0)
    if name == "DEAD":
        hip_y = -5.0
    hip = (-0.5, hip_y)

    def up(pt):
        """Upper-body point, rotated by the lean around the hip."""
        return _rot((pt[0], pt[1] + hip_y + 15.0), lean, hip)

    # -- legs (back leg first, darker) ----------------------------------
    for i, (th, sh) in enumerate(P["legs"]):
        col = _lerp(C["legs"], (0, 0, 0), 0.35) if i == 0 else C["legs"]
        boot = _lerp(C["boot"], (0, 0, 0), 0.3) if i == 0 else C["boot"]
        hp = (hip[0] + (-1.2 if i == 0 else 1.2), hip[1])
        knee, ankle = _leg_points(hp, th, sh)
        cv.line(col, hp, knee, 3.4)
        cv.line(col, knee, ankle, 3.0)
        foot = (ankle[0] + math.cos(sh) * 2.0, ankle[1] - math.sin(sh) * 0.6)
        cv.ellipse(boot, ((ankle[0] + foot[0]) / 2, ankle[1] + 0.4), 2.7, 1.8)

    # -- back arm (behind the cloak) ------------------------------------
    sh_b = up((-3.0, -26.0))
    ab = P["arms"][0]
    hand_b = (sh_b[0] + math.sin(ab + lean) * 7.0, sh_b[1] + math.cos(ab + lean) * 7.0)
    cv.line(_lerp(C["cloak_dark"], (0, 0, 0), 0.3), sh_b, hand_b, 2.6)
    cv.circle(_lerp(C["hand"], (0, 0, 0), 0.3), hand_b, 1.6)

    # -- cloak ----------------------------------------------------------
    fb, ff = P["flare"]
    hem = P["hem"]
    back = [up((-4.6, -28.5)), up((-6.4 - fb * 0.4, -21.0)),
            up((-7.4 - fb, -12.5 + hem)), up((-3.5 - fb * 0.5, -10.8 + hem * 0.6))]
    front = [up((4.4, -28.5)), up((5.6 + ff * 0.4, -21.0)),
             up((6.6 + ff, -12.0 + hem * 0.5)), up((2.0 + ff * 0.4, -10.6 + hem * 0.3))]
    mid_hem = up((0.0, -10.2 + hem * 0.4))
    cloak = [back[0], back[1], back[2], back[3], mid_hem, front[3], front[2],
             front[1], front[0]]
    cv.poly(C["cloak_dark"], cloak)
    # Front panel lit by the crystal, so the body reads as a volume.
    panel = [up((0.2, -28.0)), front[0], front[1], front[2], front[3], mid_hem]
    cv.poly(C["cloak"], panel)
    cv.lines(C["cloak_lit"], [front[0], front[1], front[2]], 1.0)
    # Hem trim.
    cv.lines(_lerp(C["cloak_dark"], (0, 0, 0), 0.35),
             [back[2], back[3], mid_hem, front[3], front[2]], 0.9)

    # -- scarf knot -------------------------------------------------------
    n0, n1 = up((-4.8, -29.2)), up((4.6, -29.2))
    cv.line(C["scarf_dark"], (n0[0], n0[1] + 1.0), (n1[0], n1[1] + 1.0), 4.4)
    cv.line(C["scarf"], n0, n1, 3.4)
    cv.line(C["scarf_lit"], (n0[0] + 1.0, n0[1] - 0.8), (n1[0] - 1.0, n1[1] - 0.8), 0.9)
    knot = up((3.4, -27.0))
    cv.circle(C["scarf_dark"], (knot[0], knot[1] + 0.4), 2.3)
    cv.circle(C["scarf"], knot, 1.9)
    cv.circle(C["scarf_lit"], (knot[0] + 0.5, knot[1] - 0.6), 0.8)

    # -- head and hood ---------------------------------------------------
    head_lean = lean * 0.55
    hc = _rot(up((0.6, -35.2)), head_lean - lean, up((0.0, -29.0)))
    tip = P["tip"]
    hood_r = 7.9
    base_a = _rot((hc[0] - 5.2, hc[1] - 5.0), head_lean, hc)
    base_b = _rot((hc[0] - 7.6, hc[1] + 1.0), head_lean, hc)
    tip_len = 8.5
    tp = (hc[0] - 6.0 - math.cos(tip) * tip_len,
          hc[1] - 3.0 - math.sin(tip) * tip_len)
    cv.poly(C["cloak_dark"], [base_a, tp, base_b])
    cv.circle(C["cloak"], hc, hood_r)
    cv.arc(C["cloak_lit"], hc, hood_r - 0.4, hood_r - 0.4, 0.2, 1.7, 1.0)
    # Opening and face, turned toward the facing direction.
    op = _rot((hc[0] + 2.2, hc[1] + 0.9), head_lean, hc)
    cv.ellipse(C["hood_in"], op, 5.7, 5.5)
    fc = _rot((hc[0] + 2.9, hc[1] + 1.6), head_lean, hc)
    cv.ellipse(C["face"], fc, 4.4, 4.0)
    cv.ellipse(C["face_shade"], (fc[0] - 2.4, fc[1] + 1.0), 1.6, 2.4)
    # A fringe of hair under the hood: a face, not a mask.
    fr = _rot((hc[0] + 2.6, hc[1] - 2.3), head_lean, hc)
    cv.ellipse(C["hair"], fr, 4.8, 1.9)
    cv.poly(C["hair"], [(fr[0] + 0.5, fr[1]), (fr[0] + 3.4, fr[1] + 0.4),
                        (fr[0] + 1.6, fr[1] + 2.6)])
    eyes = P["eyes"]
    e1 = (fc[0] + 0.1, fc[1] + 0.2)
    e2 = (fc[0] + 2.9, fc[1] + 0.3)
    for k, e in enumerate((e1, e2)):
        r = 0.85 if k == 0 else 1.0
        if eyes in ("open", "wide", "focus"):
            ry = {"open": 1.25, "wide": 1.5, "focus": 0.8}[eyes]
            cv.ellipse(C["eye"], e, r, ry * (0.92 if k == 0 else 1.0))
            cv.circle(C["glint"], (e[0] + 0.35, e[1] - ry * 0.4), 0.45)
        elif eyes == "closed":
            cv.arc(C["eye"], (e[0], e[1] - 0.6), r + 0.3, 1.2, math.pi * 1.1,
                   math.pi * 1.9, 0.7)
        elif eyes == "happy":
            cv.arc(C["eye"], (e[0], e[1] + 0.6), r + 0.3, 1.4, 0.2,
                   math.pi - 0.2, 0.75)
        else:  # hurt: squeezed shut
            cv.line(C["eye"], (e[0] - r, e[1] - 1.0), (e[0] + r, e[1]), 0.7, False)
            cv.line(C["eye"], (e[0] - r, e[1] + 1.0), (e[0] + r, e[1]), 0.7, False)
    # Cheek warmth.
    cv.circle((240, 150, 136), (fc[0] + 2.2, fc[1] + 2.0), 0.9)

    # -- front arm and pendant ------------------------------------------
    sh_f = up((2.6, -26.2))
    af = P["arms"][1]
    hand_f = (sh_f[0] + math.sin(af + lean) * 7.0, sh_f[1] + math.cos(af + lean) * 7.0)
    cv.line(C["cloak"], sh_f, hand_f, 2.8)
    cv.circle(C["hand"], hand_f, 1.7)
    cr = up((2.4, -22.8))
    pts = [(cr[0], cr[1] - 3.2), (cr[0] + 2.2, cr[1]), (cr[0], cr[1] + 3.0),
           (cr[0] - 2.2, cr[1])]
    cv.poly(_lerp(C["crystal"], (20, 60, 90), 0.45), pts)
    cv.poly(C["crystal"], [pts[0], pts[1], pts[2], (cr[0], cr[1])])
    cv.circle(C["crystal_core"], (cr[0] + 0.3, cr[1] - 0.6), 0.9)

    surf = cv.finish()
    meta = {"neck": up((-4.2, -28.4)), "crystal": cr, "head": hc}
    _CACHE[key] = (surf, meta)
    return surf, meta


# ============================================================== enemies ==
ENEMY_COL = {
    "gloom": (40, 24, 56), "gloom_lit": (120, 70, 160),
    "gloom_rim": (214, 120, 230), "eye": (255, 120, 220),
    "brute": (54, 32, 28), "brute_mark": (236, 132, 52),
    "horn": (236, 216, 176), "horn_hot": (255, 226, 96),
    "brute_eye": (255, 176, 70),
    "armor": (72, 66, 92), "armor_lit": (140, 132, 170),
    "armor_dark": (34, 30, 46), "thorn": (226, 36, 64),
    "thorn_lit": (255, 140, 150),
    "moth": (44, 32, 66), "wing": (140, 108, 214), "wing_lit": (214, 196, 255),
    "wing_dark": (70, 50, 120), "moth_eye": (255, 228, 120),
    "stone": (66, 70, 88), "stone_lit": (120, 126, 150),
    "stone_dark": (32, 34, 46), "lens_idle": (120, 96, 170),
    "lens_hot": (255, 178, 70),
    "obsidian": (28, 22, 42), "obs_lit": (120, 96, 180),
    "obs_wing": (92, 64, 150), "obs_wing_dark": (46, 30, 80),
    "brow": (255, 96, 70),
    "robe": (32, 24, 58), "robe_lit": (90, 70, 150), "gold": (236, 196, 96),
    "gold_dark": (150, 108, 40), "gem": (110, 236, 255), "king_eye": (240, 250, 255),
}


def _walker(frame):
    E = ENEMY_COL
    cv = Canvas(32, 26, 16, 25)
    step = 1 if frame.endswith("B") else 0
    # Legs: four short claws that alternate.
    for i, x in enumerate((-7, -3, 3, 7)):
        off = (1.6 if (i + step) % 2 else -1.4)
        cv.line(E["gloom"], (x * 0.8, -4.5), (x + off, -0.6), 1.8)
    cv.ellipse(_lerp(E["gloom"], (0, 0, 0), 0.4), (0, -4.6), 10.5, 2.6)
    cv.ellipse(E["gloom"], (0, -9.2), 11.2, 8.0)
    cv.ellipse(_lerp(E["gloom"], E["gloom_lit"], 0.45), (-1.2, -11.5), 8.2, 5.2)
    cv.arc(E["gloom_rim"], (0, -9.2), 11.0, 7.8, 0.35, math.pi - 0.35, 1.0)
    # Two little spines on the back.
    for x in (-4.0, 0.0):
        cv.poly(E["gloom"], [(x - 1.6, -15.5), (x - 2.2, -20.0), (x + 1.4, -15.8)])
    # Eyes facing forward (+x), glowing.
    for (x, r) in ((4.6, 1.7), (8.2, 1.9)):
        cv.circle(_lerp(E["eye"], (0, 0, 0), 0.5), (x, -9.8), r + 0.9)
        cv.circle(E["eye"], (x, -9.8), r)
        cv.circle((255, 240, 252), (x + 0.4, -10.4), r * 0.42)
    return cv.finish()


def _charger(frame):
    E = ENEMY_COL
    wind = frame.endswith("WIND")
    step = 1 if frame.endswith("B") else 0
    cv = Canvas(38, 30, 18, 29)
    lean = -0.18 if wind else 0.0
    piv = (0.0, -4.0)

    def r(p):
        return _rot(p, lean, piv)

    for i, x in enumerate((-8, -4, 4, 8)):
        off = (1.6 if (i + step) % 2 else -1.4)
        if wind:
            off = -1.0
        cv.line(_lerp(E["brute"], (0, 0, 0), 0.3), r((x * 0.8, -5)),
                (x + off, -0.4), 2.4)
    body = [r(p) for p in ((-13, -8), (-11, -16), (-3, -19.5), (6, -18),
                           (12, -13), (12.5, -7), (6, -4.5), (-9, -4.5))]
    cv.poly(E["brute"], body)
    # Warm stripes: the charger's identity colour.
    for k in range(3):
        x = -8 + k * 5
        cv.poly(E["brute_mark"], [r((x, -18.2 + k * 0.5)), r((x + 2.4, -18.6 + k * 0.4)),
                                  r((x + 1.2, -12.5)), r((x - 1.0, -13))])
    cv.lines(_lerp(E["brute_mark"], (255, 255, 255), 0.25),
             [r((-11, -16)), r((-3, -19.5)), r((6, -18))], 1.0)
    # Head and snout.
    cv.ellipse(_lerp(E["brute"], (0, 0, 0), 0.15), r((10.5, -10.5)), 5.6, 5.0)
    cv.ellipse((92, 58, 48), r((14.5, -8.6)), 2.6, 2.0)
    eye = r((11.4, -12.4))
    eye_c = E["horn_hot"] if wind else E["brute_eye"]
    cv.circle(_lerp(eye_c, (0, 0, 0), 0.4), eye, 1.9)
    cv.circle(eye_c, eye, 1.3)
    # Horns: the silhouette difference from the Walker.
    horn = E["horn_hot"] if wind else E["horn"]
    for (bx, by, dx) in ((7.5, -14.5, 1.0), (10.5, -14.0, 1.6)):
        b = r((bx, by))
        m = r((bx + 2.5 * dx, by - 5.5))
        t = r((bx + 6.0 * dx, by - 6.8))
        cv.poly(horn, [b, (b[0] + 2.4, b[1] + 0.6), m, t])
    if wind:
        for k in range(4):
            sx = -10 + k * 6
            cv.poly(E["horn_hot"], [(sx, -0.5), (sx + 1.2, -3.8), (sx + 2.4, -0.5)])
    return cv.finish()


def _spiker(frame):
    E = ENEMY_COL
    step = 1 if frame.endswith("B") else 0
    cv = Canvas(32, 30, 16, 29)
    for i, x in enumerate((-7, -2.5, 2.5, 7)):
        off = (1.4 if (i + step) % 2 else -1.2)
        cv.line(E["armor_dark"], (x * 0.8, -4.0), (x + off, -0.5), 2.2)
    # Dorsal thorns first, so the armour overlaps their roots.
    for k in range(6):
        a = math.pi * (0.10 + 0.80 * k / 5.0)
        bx = math.cos(a) * -9.0
        by = -9.5 - math.sin(a) * 6.5
        tx = math.cos(a) * -16.5
        ty = -9.5 - math.sin(a) * 17.0
        nx, ny = -math.sin(a) * 3.0, -math.cos(a) * 3.0
        cv.poly(_lerp(E["thorn"], (0, 0, 0), 0.55),
                [(bx + nx, by + ny), (tx, ty), (bx - nx, by - ny)])
        cv.poly(E["thorn"], [(bx + nx * 0.6, by + ny * 0.6), (tx, ty), (bx, by)])
        cv.circle(E["thorn_lit"], (tx * 0.92 + bx * 0.08, ty * 0.92 + by * 0.08), 0.6)
    cv.ellipse(E["armor_dark"], (0, -8.4), 11.4, 7.6)
    cv.ellipse(E["armor"], (0, -9.4), 10.6, 6.8)
    for k in range(3):
        cv.arc(E["armor_lit"], (0, -9.4 + k * 0.4), 10.4 - k * 2.8, 6.6 - k * 1.6,
               0.3, math.pi - 0.3, 0.9)
    for x in (4.4, 7.6):
        cv.circle((255, 236, 240), (x, -8.6), 1.3)
        cv.circle((40, 10, 20), (x + 0.3, -8.5), 0.6)
    return cv.finish()


def _flyer(frame):
    E = ENEMY_COL
    up = frame.endswith("UP")
    cv = Canvas(46, 30, 23, 27)
    cy = -10.0
    ang = -0.55 if up else 0.45            # wing angle below horizontal

    def wpt(s, r, a):
        return (s * (1.5 + math.cos(ang + a) * r), cy - 1.0 + math.sin(ang + a) * r)

    for s in (-1, 1):
        root = (s * 1.5, cy - 1.0)
        fore = [root, wpt(s, 9.0, -0.35), wpt(s, 18.0, -0.05),
                wpt(s, 16.5, 0.30), wpt(s, 8.0, 0.55)]
        hind = [(s * 1.5, cy + 1.0), wpt(s, 11.0, 0.75), wpt(s, 9.0, 1.15),
                (s * 2.5, cy + 5.0)]
        cv.poly(E["wing_dark"], hind)
        cv.poly(_lerp(E["wing"], E["wing_dark"], 0.35),
                [hind[0], wpt(s, 9.5, 0.8), wpt(s, 7.5, 1.1), hind[3]])
        cv.poly(E["wing_dark"], fore)
        cv.poly(E["wing"], [root, wpt(s, 8.5, -0.25), wpt(s, 16.0, -0.02),
                            wpt(s, 14.5, 0.26), wpt(s, 7.5, 0.48)])
        cv.circle(E["wing_lit"], wpt(s, 11.0, 0.05), 1.9)
        cv.circle(E["wing_lit"], wpt(s, 6.0, 0.35), 1.1)
        cv.lines(E["wing_dark"], [root, wpt(s, 14.0, 0.05)], 0.6)
    cv.ellipse(E["moth"], (0, cy + 1.0), 3.6, 6.4)
    cv.ellipse(_lerp(E["moth"], E["wing"], 0.4), (0, cy - 2.0), 3.0, 3.0)
    for s in (-1, 1):
        cv.lines(E["moth"], [(s * 1.0, cy - 4.5), (s * 3.5, cy - 9.0),
                             (s * 6.0, cy - 10.0)], 0.6)
        cv.circle(E["moth_eye"], (s * 1.4, cy - 2.4), 1.0)
    return cv.finish()


def _sentry(frame):
    E = ENEMY_COL
    hot = frame.endswith("B")
    cv = Canvas(28, 36, 14, 35)
    cv.poly(E["stone_dark"], [(-9, 0), (9, 0), (7.5, -4), (-7.5, -4)])
    cv.poly(E["stone"], [(-6.5, -4), (6.5, -4), (5.5, -20), (-5.5, -20)])
    cv.lines(E["stone_lit"], [(-6.5, -4), (-5.5, -20), (5.5, -20)], 0.9)
    for y in (-8.5, -13.0):
        cv.line(E["stone_dark"], (-4.0, y), (4.0, y), 0.7, False)
    lift = -3.0 if hot else 0.0
    cv.poly(E["stone"], [(-7, -20), (7, -20), (6 + 1.5 * (lift < 0), -28 + lift),
                         (-5, -28 + lift)])
    cv.lines(E["stone_lit"], [(-7, -20), (-5, -28 + lift), (6, -28 + lift)], 0.8)
    lens = (4.0, -24.0 + lift * 0.5)
    lc = E["lens_hot"] if hot else E["lens_idle"]
    cv.circle(E["stone_dark"], lens, 3.6)
    cv.circle(_lerp(lc, (0, 0, 0), 0.35), lens, 2.8)
    cv.circle(lc, lens, 2.0)
    cv.circle((255, 250, 235), (lens[0] + 0.6, lens[1] - 0.7), 0.8)
    return cv.finish()


def _boss(frame):
    E = ENEMY_COL
    wind = frame.endswith("WIND")
    up = frame.endswith("A") or wind
    cv = Canvas(84, 64, 42, 63)
    # Wings behind the body.
    lift = 14.0 if wind else (8.0 if up else -4.0)
    for s in (-1, 1):
        root = (s * 6.0, -30.0)
        tip = (s * 40.0, -42.0 - lift)
        mid = (s * 30.0, -20.0 - lift * 0.4)
        low = (s * 14.0, -14.0)
        bones = [(s * 22.0, -40.0 - lift * 0.8), tip]
        cv.poly(E["obs_wing_dark"], [root, bones[0], tip, mid, low])
        cv.poly(E["obs_wing"], [root, (s * 20.0, -36.0 - lift * 0.7),
                                (s * 33.0, -36.0 - lift * 0.9), mid, low])
        cv.lines(E["obs_wing_dark"], [root, bones[0], tip], 1.6)
        cv.lines(E["obs_wing_dark"], [bones[0], mid], 1.0)
    # Legs and body.
    for s in (-1, 1):
        cv.line(E["obsidian"], (s * 7.0, -12.0), (s * 10.0, -1.0), 5.0)
        cv.poly(E["obsidian"], [(s * 7.0, -2.0), (s * 15.0, 0.0), (s * 6.0, 0.0)])
    torso = [(-15, -14), (-17, -30), (-9, -40), (9, -40), (17, -30), (15, -14),
             (8, -9), (-8, -9)]
    cv.poly(E["obsidian"], torso)
    cv.poly(_lerp(E["obsidian"], E["obs_lit"], 0.35),
            [(-9, -40), (9, -40), (12, -32), (0, -26), (-12, -32)])
    cv.lines(E["obs_lit"], [(-17, -30), (-9, -40), (9, -40), (17, -30)], 1.2)
    cv.lines(E["obs_lit"], [(0, -26), (0, -12)], 0.8)
    # Helm with a burning brow slit.
    cv.poly(_lerp(E["obsidian"], (0, 0, 0), 0.2),
            [(-8, -40), (-6, -50), (0, -54), (6, -50), (8, -40)])
    brow = E["brow"] if not wind else (255, 196, 120)
    cv.poly(_lerp(brow, (0, 0, 0), 0.5), [(-6, -46), (6, -46), (5, -43), (-5, -43)])
    cv.poly(brow, [(-5, -45.5), (5, -45.5), (4, -43.8), (-4, -43.8)])
    cv.lines(E["obs_lit"], [(-6, -50), (0, -54), (6, -50)], 1.0)
    return cv.finish()


def _king(frame):
    E = ENEMY_COL
    wind = frame.endswith("WIND")
    step = frame.endswith("B")
    cv = Canvas(70, 76, 35, 75)
    # Legs.
    for s in (-1, 1):
        spread = 2.0 if (step and s > 0) else 0.0
        cv.line(_lerp(E["robe"], (0, 0, 0), 0.3), (s * 6.0, -18.0),
                (s * (8.0 + spread), -2.0), 5.4)
        cv.poly(E["gold_dark"], [(s * (5.0 + spread), -3.0),
                                 (s * (13.0 + spread), -1.0),
                                 (s * (12.0 + spread), 0.0), (s * (4.0 + spread), 0.0)])
    robe = [(-14, -18), (-18, -44), (-10, -52), (10, -52), (18, -44), (14, -18),
            (6, -14), (-6, -14)]
    cv.poly(E["robe"], robe)
    cv.poly(E["robe_lit"], [(-3, -52), (3, -52), (4, -16), (-4, -16)])
    cv.lines(E["gold"], [(-14, -18), (-6, -14), (6, -14), (14, -18)], 1.4)
    cv.line(E["gold"], (0, -50), (0, -16), 1.0, False)
    # Gem pauldrons.
    for s in (-1, 1):
        base = (s * 14.0, -48.0)
        for k in range(3):
            a = -0.6 + 0.6 * k
            tip = (base[0] + s * math.sin(a + 0.4) * 8.0, base[1] - math.cos(a) * 9.0)
            cv.poly(_lerp(E["gem"], (10, 40, 80), 0.45),
                    [(base[0] - 2.5, base[1] + 2), tip, (base[0] + 2.5, base[1] + 2)])
            cv.poly(E["gem"], [(base[0], base[1] + 2), tip, (base[0] + 2.0 * s, base[1])])
    # Head.
    cv.ellipse((20, 16, 36), (0, -57.0), 7.5, 7.0)
    for s in (-1, 1):
        cv.ellipse(E["king_eye"], (s * 3.0, -57.5), 1.6, 1.1)
    # Crown.
    hot = (255, 250, 200) if wind else E["gold"]
    band = [(-9, -62), (9, -62), (9, -66), (-9, -66)]
    cv.poly(E["gold_dark"], band)
    for k in range(5):
        x = -8 + k * 4
        hgt = 8.0 if k % 2 == 0 else 5.5
        cv.poly(hot, [(x - 2.0, -66), (x, -66 - hgt), (x + 2.0, -66)])
        cv.circle(E["gem"] if k == 2 else (255, 120, 150), (x, -66 - hgt), 1.2)
    cv.lines(_lerp(hot, (255, 255, 255), 0.4), [(-9, -66), (9, -66)], 0.8)
    return cv.finish()


_ENEMY_BUILDERS = {
    "WALKER": _walker, "CHARGER": _charger, "SPIKER": _spiker,
    "FLYER": _flyer, "SENTRY": _sentry, "BOSS": _boss, "KING": _king,
}

# Where each enemy's glowing eyes sit, relative to its feet centre (facing
# right), for the renderer's emissive pass.
ENEMY_EYES = {
    "WALKER": ((4.6, -9.8), (8.2, -9.8)),
    "CHARGER": ((11.4, -12.4),),
    "SPIKER": ((4.4, -8.6), (7.6, -8.6)),
    "FLYER": ((-1.4, -12.4), (1.4, -12.4)),
    "SENTRY": ((4.0, -24.0),),
    "BOSS": ((0.0, -44.6),),
    "KING": ((-3.0, -57.5), (3.0, -57.5)),
}


def enemy_frame(name):
    """Cached enemy sprite for a ``lumenvalley.bestiary`` frame name."""
    key = ("enemy", name)
    hit = _CACHE.get(key)
    if hit is None:
        kind = name.split("_")[0]
        builder = _ENEMY_BUILDERS.get(kind, _walker)
        hit = builder(name)
        _CACHE[key] = hit
    return hit


def flipped(surf):
    key = ("flip", id(surf))
    hit = _CACHE.get(key)
    if hit is None or hit[0] is not surf:
        hit = (surf, pygame.transform.flip(surf, True, False))
        _CACHE[key] = hit
    return hit[1]


# ================================================================= props ==
def coin_orb():
    """A golden light mote (the coin)."""
    key = ("coin",)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(20, 20, 10, 10)
    # A dark outline keeps the mote's silhouette on a sunlit backdrop; the
    # glow carries it everywhere else (ART_BIBLE §3, collectible outline).
    cv.circle(OUTLINE, (0, 0), 7.9)
    cv.circle((150, 96, 20), (0, 0), 6.6)
    cv.circle((236, 176, 40), (0, 0), 6.0)
    cv.circle((255, 214, 70), (-0.4, -0.4), 5.0)
    cv.circle((255, 236, 150), (-1.2, -1.4), 3.0)
    cv.circle((255, 252, 230), (-1.8, -2.0), 1.4)
    cv.arc((255, 248, 210), (0, 0), 5.4, 5.4, 3.6, 4.6, 0.8)
    out = cv.finish()
    _CACHE[key] = out
    return out


def gem_crystal():
    """The cyan crystal (the gem): three facets, a lit edge and a sparkle."""
    key = ("gem",)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(24, 30, 12, 15)
    top, right, bot, left = (0, -11.5), (7.5, -2.5), (0, 12.0), (-7.5, -2.5)
    cv.poly(OUTLINE, [(top[0], top[1] - 2.2), (right[0] + 2.0, right[1]),
                      (bot[0], bot[1] + 2.2), (left[0] - 2.0, left[1])])
    cv.poly((10, 70, 100), [(top[0], top[1] - 1), (right[0] + 1, right[1]),
                            (bot[0], bot[1] + 1), (left[0] - 1, left[1])])
    cv.poly((40, 180, 220), [top, right, bot, left])
    cv.poly((0, 236, 255), [top, (0, -1.0), bot, left])
    cv.poly((150, 250, 255), [top, right, (0, -1.0)])
    cv.poly((20, 140, 190), [(0, -1.0), right, bot])
    cv.lines((235, 255, 255), [left, top, right], 0.9)
    cv.circle((255, 255, 255), (-2.4, -5.0), 1.2)
    out = cv.finish()
    _CACHE[key] = out
    return out


def star_flower():
    """Star power: a radiant white-gold star."""
    key = ("star",)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(34, 34, 17, 17)
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        r = 12.0 if k % 2 == 0 else 5.0
        pts.append((math.cos(a) * r, math.sin(a) * r))
    ring = [(x * 1.10, y * 1.10) for x, y in pts]
    cv.poly(OUTLINE, ring)
    cv.lines(OUTLINE, ring + ring[:1], 2.4)
    cv.poly((200, 140, 40), [(x * 1.04, y * 1.04) for x, y in pts])
    cv.poly((255, 222, 110), pts)
    cv.poly((255, 248, 210), [(x * 0.55, y * 0.55) for x, y in pts])
    cv.circle((255, 255, 255), (0, 0), 2.2)
    out = cv.finish()
    _CACHE[key] = out
    return out


def mushroom(squash=0.0):
    """The spring: a springy, glowing bounce cap on a pale stem."""
    q = round(max(0.0, min(1.0, squash)), 1)
    key = ("shroom", q)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(26, 22, 13, 21)
    stem_h = 9.0 - 5.0 * q
    cv.poly((214, 202, 176), [(-3.0, 0), (3.0, 0), (2.2, -stem_h), (-2.2, -stem_h)])
    cv.line((246, 238, 214), (-1.2, -1.0), (-1.0, -stem_h + 1), 0.8)
    cap_y = -stem_h - 2.5
    rx, ry = 10.5 + 2.0 * q, 6.0 - 2.0 * q
    cv.ellipse((150, 64, 30), (0, cap_y + 1.2), rx, ry * 0.7)
    cv.ellipse((236, 128, 52), (0, cap_y), rx, ry)
    cv.ellipse((255, 176, 88), (-2.0, cap_y - ry * 0.35), rx * 0.62, ry * 0.45)
    for (x, y, r) in ((-5.0, -0.8, 1.5), (1.0, -2.2, 1.9), (5.5, -0.4, 1.3)):
        cv.circle((255, 236, 160), (x, cap_y + y * (1.0 - 0.4 * q)), r)
    out = cv.finish()
    _CACHE[key] = out
    return out


def shrine(active):
    """Checkpoint: a carved stone lantern with a crystal that lights up."""
    key = ("shrine", bool(active))
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(24, 48, 12, 47)
    stone, lit, dark = (84, 88, 104), (140, 146, 166), (40, 42, 54)
    cv.poly(dark, [(-8, 0), (8, 0), (7, -4), (-7, -4)])
    cv.poly(stone, [(-4, -4), (4, -4), (3.4, -26), (-3.4, -26)])
    cv.lines(lit, [(-4, -4), (-3.4, -26)], 0.8)
    cv.poly(stone, [(-7, -26), (7, -26), (5, -30), (-5, -30)])
    for y in (-10.0, -17.0):
        cv.line(dark, (-2.6, y), (2.6, y), 0.6, False)
    col = (110, 255, 210) if active else (96, 110, 130)
    core = (230, 255, 250) if active else (150, 160, 176)
    cv.poly(_lerp(col, (0, 0, 0), 0.45), [(0, -44), (5, -36), (0, -30), (-5, -36)])
    cv.poly(col, [(0, -44), (5, -36), (0, -31), (-1.2, -36)])
    cv.circle(core, (-0.5, -38.0), 1.4)
    out = cv.finish()
    _CACHE[key] = out
    return out


def beacon(height):
    """The goal: a gold-trimmed obelisk crowned by a floating crystal."""
    height = int(max(40, height))
    key = ("beacon", height)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(36, height + 4, 18, height + 3)
    stone, lit, dark = (60, 58, 80), (130, 128, 168), (30, 28, 44)
    gold, gold_d = (236, 196, 96), (150, 108, 40)
    top = -height + 26
    cv.poly(dark, [(-10, 0), (10, 0), (8, -6), (-8, -6)])
    cv.poly(stone, [(-6, -6), (6, -6), (4.0, top + 4), (-4.0, top + 4)])
    cv.poly(lit, [(-6, -6), (-2.0, -6), (-1.2, top + 4), (-4.0, top + 4)])
    cv.poly(gold, [(-5, top + 4), (5, top + 4), (3, top), (-3, top)])
    cv.lines(gold_d, [(-6, -6), (6, -6)], 1.0)
    for k in range(3):
        y = -12 - k * (height - 40) / 3.0
        cv.line(gold, (-4.0, y), (4.0, y), 0.8, False)
    out = cv.finish()
    _CACHE[key] = out
    return out


def beacon_crystal():
    key = ("beacon_crystal",)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(28, 36, 14, 18)
    cv.poly((40, 120, 170), [(0, -16), (10, -2), (0, 17), (-10, -2)])
    cv.poly((150, 246, 255), [(0, -16), (0, 0), (0, 17), (-10, -2)])
    cv.poly((230, 255, 255), [(0, -16), (10, -2), (0, 0)])
    cv.poly((80, 200, 236), [(0, 0), (10, -2), (0, 17)])
    cv.lines((255, 255, 255), [(-10, -2), (0, -16), (10, -2)], 0.9)
    out = cv.finish()
    _CACHE[key] = out
    return out


def mover_slab(width_px, axis):
    key = ("mover", int(width_px), axis)
    if key in _CACHE:
        return _CACHE[key]
    w = int(width_px) + 4
    cv = Canvas(w, 20, 2, 2)
    rune = (90, 220, 255) if axis == "h" else (190, 140, 255)
    stone, lit, dark = (78, 80, 100), (150, 154, 180), (34, 34, 48)
    cv.poly(dark, [(-1, 1), (w - 3, 1), (w - 7, 12), (3, 12)])
    cv.poly(stone, [(-1, 0), (w - 3, 0), (w - 4, 7), (0, 7)])
    cv.line(lit, (0.5, 0.4), (w - 4.5, 0.4), 1.0, False)
    for k in range(int((w - 8) // 10)):
        x = 6 + k * 10
        cv.line(rune, (x, 3.5), (x + 4, 3.5), 1.1, False)
    out = cv.finish()
    _CACHE[key] = out
    return out


def thorns(fill, rim, tip, seed=0):
    """A pair of crystal thorns for one spike cell (hazard palette only)."""
    key = ("thorns", tuple(fill), tuple(rim), tuple(tip), seed % 4)
    if key in _CACHE:
        return _CACHE[key]
    cv = Canvas(20, 20, 0, 20)
    hs = (17.0, 15.0, 16.5, 14.0)
    for k in range(2):
        bx = 5.0 + k * 10.0
        h = hs[(seed + k) % 4] if seed % 2 else hs[(seed + k + 1) % 4]
        lean = (0.8 if k else -0.8)
        base_l, base_r = (bx - 4.6, 0.0), (bx + 4.6, 0.0)
        apex = (bx + lean, -h)
        cv.poly(rim, [(base_l[0] - 0.9, 0.0), (apex[0], apex[1] - 1.2),
                      (base_r[0] + 0.9, 0.0)])
        cv.poly(fill, [base_l, apex, base_r])
        cv.poly(_lerp(fill, (255, 255, 255), 0.25),
                [(bx - 1.0, -1.0), apex, (bx + 2.8, -1.0)])
        cv.line(tip, (apex[0] - 0.2, apex[1] + 1.0), (apex[0] - 0.5, apex[1] + 4.5), 1.1)
    out = cv.finish()
    _CACHE[key] = out
    return out


def forget():
    _CACHE.clear()
