"""Per-world colour scripts for the painted (pygame/browser) renderer.

Pure data, no pygame: the readability tests import this module to measure the
colours the painted renderer actually uses, and ``lumenvalley/rendering`` builds
every layer from it.  The terminal build keeps its xterm palette in
``lumenvalley/constants.py``; nothing here changes gameplay or the hazard roles in
``lumenvalley/palette.py``.

Direction (ART_BIBLE §1, revised 2026-11-16): a *luminous painterly* world.
Scenes are backlit, so silhouettes read darker than the hazier distance behind
them and every walkable edge carries a rim of light.  Each depth layer is one
clear value step darker than the one behind it (sky > far > mid > near), which
is what makes the stack read as air instead of cut-outs.  Emissive accents
(the hero's crystal, gold motes, cyan gems, lava) are the brightest values on
screen.  The playfield band stays mid-dark on purpose: gems need 4:1 and coins
2:1 against whatever is behind them (``tests/test_scenery.py`` renders the
backdrops and measures it).

Colours are plain RGB triples.  Stops are ``(t, rgb)`` with ``t`` in 0..1 from
the top of the canvas to the bottom.
"""

# A layer is drawn back to front.  ``factor`` is its parallax speed (0 = fixed
# to the sky, 1 = moves with the playfield), ``kind`` picks the silhouette
# builder in ``lumenvalley.rendering.scenery``, ``top``/``bottom`` are the colour
# ramp inside the silhouette, ``rim`` the light caught by its upper edge, and
# ``blur`` the depth-of-field softness (far layers are softer).

VERDANT = {
    "title": "Verdant",
    "sky": ((0.00, (14, 36, 52)), (0.28, (30, 80, 94)),
            (0.50, (58, 116, 116)), (0.68, (70, 122, 108)),
            (0.86, (52, 96, 84)), (1.00, (26, 56, 50))),
    "light": {"kind": "sun", "x": 0.76, "y": 0.09, "r": 28,
              "core": (255, 251, 232), "edge": (255, 214, 140),
              "glow": (255, 222, 150), "glow_r": 250, "glow_a": 0.62},
    "stars": 0,
    "layers": (
        {"kind": "hills", "factor": 0.05, "base": 0.62, "amp": 0.24,
         "top": (48, 92, 108), "bottom": (42, 82, 96),
         "rim": (160, 210, 180), "seed": 11, "blur": 3},
        {"kind": "forest", "factor": 0.12, "base": 0.72, "amp": 0.24,
         "top": (34, 74, 86), "bottom": (30, 64, 74),
         "rim": (140, 196, 160), "seed": 23, "blur": 2, "style": "pine"},
        {"kind": "trunks", "factor": 0.26, "base": 1.00, "amp": 1.0,
         "top": (30, 60, 54), "bottom": (22, 46, 42),
         "rim": (196, 222, 140), "seed": 37, "blur": 1, "side": 1},
        {"kind": "forest", "factor": 0.45, "base": 0.84, "amp": 0.17,
         "top": (18, 46, 34), "bottom": (12, 34, 28),
         "rim": (122, 172, 90), "seed": 41, "blur": 0, "style": "broad"},
    ),
    "fog": {"colour": (184, 214, 170), "y": 0.58, "h": 0.36, "alpha": 58,
            "factor": 0.38, "drift": 7.0},
    "rays": {"colour": (255, 220, 140), "strength": 16, "count": 7,
             "spread": 0.55, "source": (0.80, -0.10)},
    "canopy": {"colour": (8, 24, 24), "factor": 0.62, "depth": 54},
    "foreground": {"colour": (5, 15, 16), "factor": 1.32, "height": 46,
                   "style": "ferns"},
    "motes": {"kind": "spores", "colours": ((255, 236, 170), (196, 255, 150)),
              "count": 46},
    "ambient": None,
    "grade": (4, 10, 10),
    "terrain": {
        "body": (54, 46, 40), "deep": (20, 18, 20), "speck": (78, 66, 54),
        "moss": (66, 116, 56), "moss_lit": (150, 196, 92),
        "rim": (226, 240, 168), "side": (88, 98, 70),
        "blades": ((54, 104, 50), (82, 140, 64), (122, 176, 82)),
        "accent": ((236, 226, 150), (190, 220, 255)),
        "accent_kind": "flowers",
        "brick": (86, 96, 88), "brick_lit": (132, 146, 128),
        "brick_dark": (40, 48, 46), "mortar": (28, 34, 32),
        "plank": (112, 78, 50), "plank_lit": (170, 128, 80),
        "plank_dark": (58, 38, 26), "rope": (150, 126, 86),
        "roots": (40, 34, 28), "glow": None,
    },
}

CAVERN = {
    "title": "Caverns",
    "sky": ((0.00, (3, 8, 18)), (0.30, (8, 22, 40)),
            (0.55, (12, 34, 56)), (0.80, (8, 22, 38)),
            (1.00, (3, 8, 16))),
    "light": None,
    "stars": 0,
    "glows": (((0.20, 0.42), 360, (150, 86, 170), 0.46),
              ((0.62, 0.36), 460, (46, 156, 210), 0.50),
              ((0.93, 0.50), 280, (80, 110, 220), 0.40)),
    "layers": (
        {"kind": "cavewall", "factor": 0.06, "base": 0.5, "amp": 0.3,
         "top": (14, 34, 58), "bottom": (10, 26, 46),
         "rim": (70, 140, 180), "seed": 13, "blur": 3},
        {"kind": "stalactites", "factor": 0.18, "base": 0.0, "amp": 0.5,
         "top": (6, 15, 30), "bottom": (5, 12, 24),
         "rim": (90, 170, 210), "seed": 29, "blur": 2,
         "crystals": ((90, 230, 255), (200, 130, 255))},
        {"kind": "columns", "factor": 0.40, "base": 0.0, "amp": 1.0,
         "top": (4, 11, 22), "bottom": (3, 8, 16),
         "rim": (70, 160, 200), "seed": 47, "blur": 0,
         "crystals": ((110, 240, 255), (150, 120, 255))},
    ),
    "fog": {"colour": (40, 100, 140), "y": 0.62, "h": 0.36, "alpha": 70,
            "factor": 0.35, "drift": 4.0},
    "rays": {"colour": (120, 210, 255), "strength": 16, "count": 4,
             "spread": 0.25, "source": (0.62, -0.20)},
    "canopy": {"colour": (3, 8, 16), "factor": 0.62, "depth": 44,
               "style": "stone"},
    "foreground": {"colour": (3, 8, 14), "factor": 1.32, "height": 40,
                   "style": "shards"},
    "motes": {"kind": "motes", "colours": ((120, 230, 255), (190, 150, 255)),
              "count": 52},
    "ambient": (104, 120, 160),
    "grade": (2, 8, 16),
    "terrain": {
        "body": (40, 48, 66), "deep": (12, 14, 24), "speck": (62, 74, 98),
        "moss": (46, 96, 120), "moss_lit": (110, 200, 220),
        "rim": (190, 246, 255), "side": (64, 84, 110),
        "blades": ((40, 92, 116), (60, 140, 168), (110, 210, 230)),
        "accent": ((120, 240, 255), (190, 150, 255)),
        "accent_kind": "crystals",
        "brick": (62, 72, 100), "brick_lit": (104, 120, 156),
        "brick_dark": (30, 34, 52), "mortar": (16, 20, 32),
        "plank": (78, 86, 104), "plank_lit": (130, 146, 170),
        "plank_dark": (36, 40, 54), "rope": (96, 120, 150),
        "roots": (24, 30, 44), "glow": (60, 200, 240),
    },
}

SKYFALL = {
    "title": "Skyfall",
    "sky": ((0.00, (34, 18, 60)), (0.14, (96, 44, 98)),
            (0.26, (150, 70, 104)), (0.40, (120, 56, 96)),
            (0.60, (82, 36, 80)), (0.82, (52, 24, 62)),
            (1.00, (26, 12, 36))),
    "light": {"kind": "sun", "x": 0.30, "y": 0.15, "r": 38,
              "core": (255, 246, 214), "edge": (255, 168, 96),
              "glow": (255, 160, 110), "glow_r": 240, "glow_a": 0.56},
    "stars": 28,
    "layers": (
        {"kind": "islands", "factor": 0.05, "base": 0.42, "amp": 0.20,
         "top": (92, 42, 92), "bottom": (80, 36, 82),
         "rim": (255, 190, 150), "seed": 17, "blur": 2},
        {"kind": "clouds", "factor": 0.10, "base": 0.68, "amp": 0.10,
         "top": (158, 84, 108), "bottom": (76, 36, 74),
         "rim": (255, 200, 170), "seed": 31, "blur": 3},
        {"kind": "spires", "factor": 0.24, "base": 0.80, "amp": 0.55,
         "top": (60, 28, 74), "bottom": (48, 22, 60),
         "rim": (255, 170, 130), "seed": 53, "blur": 1},
        {"kind": "ruins", "factor": 0.46, "base": 0.86, "amp": 0.30,
         "top": (32, 16, 44), "bottom": (24, 12, 34),
         "rim": (240, 150, 120), "seed": 61, "blur": 0},
    ),
    "fog": {"colour": (190, 110, 140), "y": 0.60, "h": 0.36, "alpha": 56,
            "factor": 0.36, "drift": 9.0},
    "rays": {"colour": (255, 170, 120), "strength": 15, "count": 8,
             "spread": 0.70, "source": (0.30, 0.15)},
    "canopy": None,
    "foreground": {"colour": (12, 6, 16), "factor": 1.32, "height": 42,
                   "style": "grass"},
    "motes": {"kind": "petals", "colours": ((255, 200, 150), (255, 230, 190)),
              "count": 40},
    "ambient": None,
    "grade": (8, 2, 10),
    "terrain": {
        "body": (70, 46, 60), "deep": (26, 14, 28), "speck": (96, 66, 80),
        "moss": (120, 94, 74), "moss_lit": (230, 176, 120),
        "rim": (255, 218, 170), "side": (110, 76, 88),
        "blades": ((116, 86, 70), (170, 126, 86), (220, 170, 110)),
        "accent": ((255, 236, 200), (190, 200, 255)),
        "accent_kind": "flowers",
        "brick": (124, 92, 104), "brick_lit": (184, 140, 140),
        "brick_dark": (62, 40, 56), "mortar": (40, 24, 36),
        "plank": (120, 82, 66), "plank_lit": (190, 140, 104),
        "plank_dark": (64, 38, 34), "rope": (180, 140, 110),
        "roots": (48, 30, 40), "glow": None,
    },
}

OBSIDIAN = {
    "title": "Obsidian",
    "sky": ((0.00, (4, 4, 14)), (0.30, (12, 10, 32)),
            (0.58, (28, 22, 60)), (0.80, (44, 32, 80)),
            (1.00, (20, 14, 40))),
    "light": {"kind": "moon", "x": 0.74, "y": 0.22, "r": 46,
              "core": (246, 248, 255), "edge": (140, 150, 186),
              "glow": (150, 170, 255), "glow_r": 300, "glow_a": 0.75},
    "stars": 150,
    "aurora": ((90, 200, 220), (150, 110, 240)),
    "layers": (
        {"kind": "mountains", "factor": 0.04, "base": 0.62, "amp": 0.26,
         "top": (34, 30, 66), "bottom": (30, 26, 56),
         "rim": (170, 170, 240), "seed": 19, "blur": 2},
        {"kind": "obsidian", "factor": 0.18, "base": 0.78, "amp": 0.42,
         "top": (20, 16, 42), "bottom": (16, 12, 32),
         "rim": (170, 150, 255), "seed": 43, "blur": 1,
         "crystals": ((170, 120, 255), (110, 200, 255))},
        {"kind": "crags", "factor": 0.42, "base": 0.86, "amp": 0.26,
         "top": (10, 8, 22), "bottom": (8, 6, 16),
         "rim": (130, 120, 220), "seed": 59, "blur": 0,
         "crystals": ((190, 130, 255), (120, 220, 255))},
    ),
    "fog": {"colour": (70, 60, 130), "y": 0.64, "h": 0.36, "alpha": 64,
            "factor": 0.36, "drift": 5.0},
    "rays": {"colour": (150, 170, 255), "strength": 22, "count": 5,
             "spread": 0.45, "source": (0.74, 0.22)},
    "canopy": None,
    "foreground": {"colour": (4, 3, 10), "factor": 1.32, "height": 40,
                   "style": "shards"},
    "motes": {"kind": "fireflies", "colours": ((150, 255, 200), (180, 150, 255)),
              "count": 40},
    "ambient": (140, 136, 184),
    "grade": (6, 4, 14),
    "terrain": {
        "body": (34, 28, 50), "deep": (8, 6, 16), "speck": (56, 48, 80),
        "moss": (70, 56, 112), "moss_lit": (160, 140, 230),
        "rim": (220, 210, 255), "side": (64, 56, 96),
        "blades": ((62, 50, 104), (96, 80, 150), (150, 130, 220)),
        "accent": ((190, 140, 255), (130, 230, 255)),
        "accent_kind": "crystals",
        "brick": (58, 50, 84), "brick_lit": (110, 100, 156),
        "brick_dark": (24, 20, 40), "mortar": (12, 10, 22),
        "plank": (70, 60, 96), "plank_lit": (120, 108, 160),
        "plank_dark": (32, 26, 48), "rope": (110, 100, 150),
        "roots": (22, 18, 34), "glow": (150, 110, 255),
    },
}

VAULT = {
    "title": "Clockwork",
    "sky": ((0.00, (34, 30, 60)), (0.12, (96, 70, 88)),
            (0.24, (160, 110, 94)), (0.36, (126, 86, 74)),
            (0.54, (92, 62, 54)), (0.78, (60, 40, 38)),
            (1.00, (32, 22, 24))),
    "light": {"kind": "sun", "x": 0.20, "y": 0.13, "r": 32,
              "core": (255, 250, 226), "edge": (255, 196, 120),
              "glow": (255, 200, 130), "glow_r": 250, "glow_a": 0.62},
    "stars": 18,
    "layers": (
        {"kind": "mountains", "factor": 0.04, "base": 0.60, "amp": 0.14,
         "top": (120, 82, 76), "bottom": (106, 72, 68),
         "rim": (255, 214, 160), "seed": 7, "blur": 3},
        {"kind": "gears", "factor": 0.12, "base": 0.55, "amp": 0.45,
         "top": (92, 62, 58), "bottom": (84, 56, 52),
         "rim": (255, 200, 140), "seed": 71, "blur": 2, "spin": 0.06},
        {"kind": "vault", "factor": 0.26, "base": 0.80, "amp": 0.50,
         "top": (58, 40, 38), "bottom": (46, 30, 30),
         "rim": (255, 190, 120), "seed": 83, "blur": 1},
        {"kind": "gears", "factor": 0.46, "base": 1.04, "amp": 0.30,
         "top": (34, 24, 22), "bottom": (28, 19, 18),
         "rim": (230, 160, 100), "seed": 97, "blur": 0, "spin": -0.12},
    ),
    "fog": {"colour": (200, 150, 110), "y": 0.58, "h": 0.36, "alpha": 52,
            "factor": 0.36, "drift": 6.0},
    "rays": {"colour": (255, 200, 130), "strength": 14, "count": 7,
             "spread": 0.50, "source": (0.20, 0.13)},
    "canopy": None,
    "foreground": {"colour": (14, 9, 8), "factor": 1.32, "height": 40,
                   "style": "cogs"},
    "motes": {"kind": "dust", "colours": ((255, 220, 160), (255, 240, 210)),
              "count": 48},
    "ambient": None,
    "grade": (8, 5, 2),
    "terrain": {
        "body": (66, 52, 44), "deep": (22, 16, 14), "speck": (92, 74, 58),
        "moss": (110, 96, 60), "moss_lit": (214, 182, 110),
        "rim": (255, 230, 170), "side": (110, 88, 66),
        "blades": ((96, 92, 56), (140, 126, 70), (196, 170, 96)),
        "accent": ((255, 220, 140), (200, 230, 255)),
        "accent_kind": "rivets",
        "brick": (120, 92, 62), "brick_lit": (190, 150, 96),
        "brick_dark": (60, 42, 30), "mortar": (34, 24, 18),
        "plank": (128, 92, 56), "plank_lit": (200, 156, 96),
        "plank_dark": (66, 44, 28), "rope": (170, 140, 96),
        "roots": (44, 32, 26), "glow": None,
    },
}

LOOKS = {
    "day": VERDANT,
    "cave": CAVERN,
    "dusk": SKYFALL,
    "night": OBSIDIAN,
    "dawn": VAULT,
}


def look(theme):
    """The colour script for a theme name (unknown names fall back to day)."""
    return LOOKS.get(theme, VERDANT)


def sky_at(theme, t):
    """Sky colour at vertical position ``t`` (0 top .. 1 bottom)."""
    stops = look(theme)["sky"]
    t = max(0.0, min(1.0, float(t)))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
    return tuple(stops[-1][1])


# The hero's own palette (the Wayfarer).  Shared by the sprite builder, the
# scarf ribbon and the HUD so the character reads as one design everywhere.
HERO = {
    "cloak": (34, 92, 112), "cloak_dark": (18, 52, 70),
    "cloak_lit": (78, 156, 170), "hood_in": (10, 18, 28),
    "face": (242, 204, 170), "face_shade": (210, 160, 132),
    "hair": (70, 44, 40),
    "eye": (30, 26, 40), "glint": (255, 255, 255),
    "scarf": (240, 118, 76), "scarf_dark": (176, 66, 54),
    "scarf_lit": (255, 182, 120),
    "crystal": (150, 250, 255), "crystal_core": (240, 255, 255),
    "glow": (90, 210, 255),
    "boot": (40, 30, 34), "legs": (30, 40, 56), "hand": (26, 44, 60),
}
