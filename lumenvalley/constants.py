"""Authoritative gameplay constants.

Nothing in this module imports a renderer, a terminal, a platform audio module
or a filesystem path.  Both the simulation and the front-ends read from here so
that layout, timing and palette values cannot drift apart.
"""

# Fixed simulation step.  Rendering may run at any rate; gameplay advances in
# whole STEP increments.
STEP = 1.0 / 60.0

VERSION = "1.0"

# Terminal-grid viewport.  The pygame front-end also uses these row/column
# counts for its layout, which is why they live in the shared constants.
VIEW_W = 80
VIEW_H = 24

GAME_OY = 3
GAME_H = 20

# UNCALIBRATED: these targets came from automated/bot runs and have not been
# validated with human playtesting.  Do not tune them from bot times alone;
# see docs/SCORING_CALIBRATION.md (R19).
PAR_TIMES = (60, 90, 120, 150, 100, 120)

# Per-level teaching cues: (player x in cells, message, colour).  Each fires
# once per level load, before the mechanic it explains, so World 1 can teach
# before it tests (Phase 8).  tools/check_levels.py requires every cue to be
# ordered left to right, ahead of its mechanic and clear of the opening cells.
TEACH_CUES = {
    0: ((10.0, "DASH THROUGH THE PRISM RING", 51),
        (60.0, "HOLD JUMP ON THE SPRING TO LAUNCH", 51),
        (84.0, "SPIKES HURT - JUMP OVER THEM", 196)),
    1: ((26.0, "LAVA IS DEADLY - USE THE PLATFORMS", 196),
        (62.0, "SENTRIES SHOOT ALONG THE FLOOR", 196),
        (132.0, "FLYERS PATROL ABOVE - TIME YOUR RUN", 245)),
    2: ((60.0, "DASH AT THE APEX TO CHAIN THREE RINGS", 51),
        (104.0, "CHARGERS RUSH WHEN THEY SPOT YOU", 245)),
}

THEMES = {
    "day": dict(grass=70, blade=77, ground=94, gdot=90,
                brick_f=166, brick_b=52, plat=136, spike=217, accent=28),
    "cave": dict(grass=245, blade=240, ground=236, gdot=243,
                 brick_f=60, brick_b=234, plat=101, spike=217, accent=37),
    "dusk": dict(grass=136, blade=100, ground=58, gdot=94,
                 brick_f=131, brick_b=52, plat=137, spike=217, accent=129),
    "night": dict(grass=140, blade=97, ground=233, gdot=239,
                  brick_f=97, brick_b=53, plat=95, spike=217, accent=141),
    "dawn": dict(grass=179, blade=187, ground=101, gdot=95,
                 brick_f=138, brick_b=59, plat=179, spike=217, accent=209),
}

# Symbolic key names shared by every input adapter.  Keeping them here means
# the simulation never imports the terminal module (which loads msvcrt/termios).
K_LEFT, K_RIGHT, K_UP, K_DOWN = "LEFT", "RIGHT", "UP", "DOWN"
K_JUMP, K_ENTER, K_ESC = "JUMP", "ENTER", "ESC"

# ------------------------------------------------------------- editor --
ED_W = 140
MAX_CUSTOMS = 12
ED_TOOLS = (
    ("#", "GROUND"), ("B", "BRICK"), ("-", "PLATFORM"),
    ("c", "COIN"), ("g", "GEM"), ("s", "SPRING"),
    ("^", "SPIKES"), ("~", "LAVA"),
    ("e", "WALKER"), ("f", "FLYER"), ("h", "CHARGER"),
    ("w", "SPIKER"), ("t", "SENTRY"), ("Z", "BOSS"),
    ("b", "CRYSTAL KING"),
    ("M", "MOVER →"), ("K", "MOVER ↑"), ("S", "STAR POWER"),
    ("r", "PRISM NODE"),
    ("*", "CHECKPOINT"), ("P", "START"), ("F", "FLAG"),
    (" ", "ERASER"),
)
ED_THEMES = ("day", "cave", "dusk", "night", "dawn")
