"""Pixel-art sprites drawn at double vertical resolution.

Every sprite is a list of equal-length strings; each char is one "pixel"
mapped through PAL to a 256-color index. Two pixel rows are packed into one
terminal cell as a '▀' half block (fg = upper pixel, bg = lower pixel), so
sprites are effectively twice as tall as the cell grid. '.' and ' ' are
transparent and blend with whatever was rendered underneath.
"""

import math

from render import FG_DEFAULT

PAL = {
    "R": 196, "r": 88,                       # hero cap red / dark red
    "S": 223,                                # skin
    "K": 16,                                 # dark (eyes, pupils)
    "W": 231,                                # white
    "T": 38, "t": 24,                        # shirt / shade
    "P": 60,                                 # pants
    "b": 94,                                 # boots
    "C": 201, "c": 127,                      # walker body / shade
    "k": 16,                                 # walker feet
    "V": 141, "v": 97,                       # flyer wing / shade
    "Y": 226,                                # flyer eyes
    "O": 220, "o": 172, "y": 227,            # coin rim / shine (gold, see ART_BIBLE)
    "A": 51, "M": 39, "D": 24,               # gem light/mid/dark
    "X": 245, "x": 240,                      # metal
    "F": 46,                                 # active flag green
    "L": 202,                                # spring plate red-orange
    "g": 239,                                # ghost silhouette gray
}

CLEAR = {".", " "}


def ghostify(rows):
    """Collapse a sprite to a dim single-color silhouette (for ghosts)."""
    return ["".join("g" if c not in CLEAR else c for c in row)
            for row in rows]


def _rows(*rs):
    return list(rs)


PLAYER_IDLE = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PP..PP.",
    "bbb..bbb",
)
PLAYER_RUN_A = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    "..PPP...",
    "..bbb...",
)
PLAYER_RUN_B = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PP..PP.",
    "bb...bbb",
)
PLAYER_RUN_C = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    "...PPP..",
    "...bbb..",
)
PLAYER_RUN_D = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    "..PPPP..",
    "..bbbb..",
)
PLAYER_DASH = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PPP....",
    "bb......",
)
PLAYER_JUMP = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PP..PP.",
    ".bb..bb.",
)
PLAYER_FALL = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    "..PPPP..",
    ".bb..bb.",
)
PLAYER_ANTICIPATE = _rows(
    "........",
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    "STTTTTTS",
    ".tttttt.",
    "PPPPPPPP",
    "bb....bb",
)
PLAYER_LAND = _rows(
    "........",
    "........",
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    "STTTTTTS",
    ".PPPPPP.",
    "bb....bb",
)
PLAYER_APEX = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    "PPttttPP",
    ".PP..PP.",
    ".bb..bb.",
)
PLAYER_WALL = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSSS",
    "STTTTTTS",
    ".tttttt.",
    "..PPPP..",
    ".bbb....",
)
PLAYER_WALL_JUMP = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PPPPP..",
    "bb....bb",
)
PLAYER_HURT = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SKSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    "PPttttPP",
    ".PP..PP.",
    "bb....bb",
)
PLAYER_DEAD = _rows(
    "........",
    "........",
    "........",
    "........",
    "..RRRR..",
    "bbRRRRRR",
    ".PPSSKS.",
    "..bbbb..",
)
PLAYER_RESPAWN = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    ".tttttt.",
    ".PPPPPP.",
    ".bb..bb.",
)
PLAYER_VICTORY = _rows(
    "..RRRR..",
    ".RRRRRR.",
    ".SSSKSS.",
    ".SSSSSS.",
    "STTTTTTS",
    "PPttttPP",
    ".PP..PP.",
    ".bb..bb.",
)

WALKER_A = _rows(
    "..CCCC..",
    ".CCCCCC.",
    ".CKCCKC.",
    "CCCCCCCC",
    ".cCCCCc.",
    ".kk..kk.",
)
WALKER_B = _rows(
    "..CCCC..",
    ".CCCCCC.",
    ".CKCCKC.",
    "CCCCCCCC",
    ".cCCCCc.",
    "kk....kk",
)

# Horned, not just a gold Walker: the silhouette has to carry the difference,
# because colour is the first thing a theme or a colour filter takes away.
CHARGER_A = _rows(
    "O......O",
    "OOOOOOOO",
    "OOKOOKOO",
    "OOOOOOOO",
    ".oOOOOo.",
    ".kk..kk.",
)
CHARGER_B = _rows(
    "O......O",
    "OOOOOOOO",
    "OOKOOKOO",
    "OOOOOOOO",
    ".oOOOOo.",
    "kk....kk",
)

SPIKER_A = _rows(
    ".R.R.R.R",
    "RRRRRRRR",
    "xxxxxxxx",
    "xxWxxWxx",
    "xxxxxxxx",
    ".xx..xx.",
)
SPIKER_B = _rows(
    ".R.R.R.R",
    "RRRRRRRR",
    "xxxxxxxx",
    "xxWxxWxx",
    "xxxxxxxx",
    "xx....xx",
)

# Leaning back with sparks at its feet: the frame that says "it has seen you".
CHARGER_WIND = _rows(
    "yO....Oy",
    "yOOOOOOy",
    "OOKOOKOO",
    "OOyyyyOO",
    ".oOOOOo.",
    ".kk..kk.",
)

SENTRY_A = _rows(
    "..xxxx..",
    ".xxxRxx.",
    ".xxxxxx.",
    "xxxxxxxx",
    "xx....xx",
    "xxxxxxxx",
)
# The lit frame raises the barrel: the promise of a shot is a shape, not just
# a warmer eye colour.
SENTRY_B = _rows(
    "...xx...",
    "..xxxx..",
    ".xxxYxY.",
    "xxxxxxxx",
    "xx....xx",
    "xxxxxxxx",
)

FLYER_UP = _rows(
    "V......V",
    ".VV..VV.",
    "..VVVV..",
    ".VYVVYV.",
    "..vvvv..",
    "........",
)
FLYER_DOWN = _rows(
    "........",
    "..VVVV..",
    ".VYVVYV.",
    "..VVVV..",
    ".VV..VV.",
    "V......V",
)

BOSS_A = _rows(
    "VV........VV",
    "VVV......VVV",
    ".VVV.OO.VVV.",
    "..xxxxxxxx..",
    ".xxxRxxRxxx.",
    ".xxxxxxxxxx.",
    "..xxxxxxxx..",
    "..xx....xx..",
)
BOSS_B = _rows(
    "............",
    ".....OO.....",
    "..xxxxxxxx..",
    ".xxxRxxRxxx.",
    ".xxxxxxxxxx.",
    "VVxxxxxxxxVV",
    "V.xx....xx.V",
    "..xx....xx..",
)

BOSS_WIND = _rows(
    "VVV......VVV",
    "VVVV.OO.VVVV",
    ".VVVxxxxVVV.",
    "..xxxWxxWxxx",
    ".xxxxxxxxxx.",
    ".xxxxxxxxxx.",
    "..xx....xx..",
    "..xx....xx..",
)

# The Crystal King is a different silhouette, not a recoloured Guardian: a
# taller crowned figure with gem pauldrons and no wings (Phase 9).
KING_A = _rows(
    "..O..O..O...",
    ".OyOOyOOyOO.",
    ".xxxxxxxxxx.",
    ".xWWxxxxWWx.",
    ".xxxxxxxxxx.",
    "MMxxxxxxxxMM",
    ".xxxxxxxxxx.",
    "..xx....xx..",
    "..xx....xx..",
    ".ddd....ddd.",
)
KING_B = _rows(
    "..O..O..O...",
    ".OyOOyOOyOO.",
    ".xxxxxxxxxx.",
    ".xWWxxxxWWx.",
    ".xxxxxxxxxx.",
    "MMxxxxxxxxMM",
    ".xxxxxxxxxx.",
    "..xx....xx..",
    ".xxx....xxx.",
    ".ddd....ddd.",
)
KING_WIND = _rows(
    "..y.y.y.y...",
    ".OyOOyOOyOO.",
    "MMxxxxxxxxMM",
    ".xWWxxxxWWx.",
    ".xxxxxxxxxx.",
    ".xxxxxxxxxx.",
    "MMxxxxxxxxMM",
    "..xx....xx..",
    "..xx....xx..",
    ".ddd....ddd.",
)

COIN_A = _rows(
    ".OO.",
    "OyyO",
    "OyWO",
    "OyyO",
    "OyyO",
    ".OO.",
)
COIN_B = _rows(
    "..O.",
    ".OyO",
    ".OyO",
    ".OyO",
    ".OyO",
    "..O.",
)
COIN_C = _rows(
    "..W.",
    "..y.",
    "..y.",
    "..y.",
    "..y.",
    "..O.",
)
COIN_FRAMES = (COIN_A, COIN_B, COIN_C, COIN_B)

GEM = _rows(
    "..AA..",
    ".AWMD.",
    "AMMMDD",
    ".AMMD.",
    "..MD..",
    "...D..",
)

SPRING_UP = _rows(
    "LLLL",
    "XxxX",
    "XxxX",
    "xxxx",
)
SPRING_DOWN = _rows(
    "LLLL",
    "xxxx",
    "....",
    "....",
)


def scale(rows, fx, fy):
    """Nearest-neighbor resize; row count stays even for half-blocks."""
    h = len(rows)
    w = len(rows[0])
    nh = max(2, int(round(h * fy)))
    if nh % 2:
        nh += 1
    ow = max(1, int(round(w * fx)))
    out = []
    for j in range(nh):
        src = rows[min(h - 1, int(j / fy))]
        out.append("".join(src[min(w - 1, int(i / fx))]
                           for i in range(ow)))
    return out


def blit(scr, rows, x, y, flip=False):
    """Composite a sprite onto the screen with (x, y) = top-left cell."""
    if flip:
        rows = [r[::-1] for r in rows]
    h_cells = len(rows) // 2
    for i in range(h_cells):
        cy = y + i
        if not 0 <= cy < scr.h:
            continue
        top = rows[2 * i]
        bot = rows[2 * i + 1]
        row = scr.cells[cy]
        for j in range(max(len(top), len(bot))):
            cx = x + j
            if not 0 <= cx < scr.w:
                continue
            tc = top[j] if j < len(top) else "."
            bc = bot[j] if j < len(bot) else "."
            t_on = tc not in CLEAR
            b_on = bc not in CLEAR
            if not t_on and not b_on:
                continue
            _, _, old_bg = row[cx]
            keep = old_bg if old_bg is not None else FG_DEFAULT
            nfg = PAL[tc] if t_on else keep
            nbg = PAL[bc] if b_on else keep
            row[cx] = ("▀" if nfg != nbg else " ", nfg, nbg)


def blit_bottom(scr, rows, cx_cells, bottom_cells, flip=False):
    """Blit anchored so the sprite's feet sit on `bottom_cells` (float)."""
    w_cells = len(rows[0]) / 2
    h_cells = len(rows) // 2
    x = int(round(cx_cells - w_cells / 2))
    y = int(math.floor(bottom_cells)) - h_cells
    blit(scr, rows, x, y, flip)
