"""Level definitions.

Levels are built on a character grid with a tiny builder API, then parsed
into a Level object: static tiles stay in the grid, dynamic things (coins,
enemies, springs, checkpoints...) are extracted into lists.

Grid legend:
  #  solid ground        B  brick (solid)      -  one-way platform
  c  coin                g  gem (500 pts)      s  spring
  e  walker enemy        f  flyer enemy        Z  boss guardian
  h  charger enemy       w  spiker (unstompable)
  t  sentry turret       b  crystal king (final boss)
  M  moving platform (→)   K  moving platform (↑)
  S  star power (invincibility)
  r  prism resonance node
  ^  spikes (deadly)     ~  lava (deadly)
  *  checkpoint          P  player start       F  goal flag (top of pole)
"""

LEVEL_H = 20

SOLID = {"#", "B"}
ONE_WAY = "-"

# Dynamic glyphs are extracted from the collision grid.  "^" is deliberately
# kept in the grid because gameplay tests for it directly, but it is also
# recorded in Level.spikes for tooling.  Anything else is a content error.
_ENTITY_GLYPHS = frozenset("cgs*ehwftZSbMKPFr")


class Builder:
    def __init__(self, w):
        self.w = w
        self.g = [[" "] * w for _ in range(LEVEL_H)]

    def put(self, x, y, ch):
        if 0 <= x < self.w and 0 <= y < LEVEL_H:
            self.g[y][x] = ch

    def ground(self, x0, x1, top=18):
        """Solid ground from row `top` to the bottom, columns x0..x1."""
        for x in range(x0, x1 + 1):
            for y in range(top, LEVEL_H):
                self.put(x, y, "#")

    def island(self, x0, x1, y, thick=2, ch="#"):
        for x in range(x0, x1 + 1):
            for yy in range(y, y + thick):
                self.put(x, yy, ch)

    def platform(self, x0, x1, y):
        for x in range(x0, x1 + 1):
            self.put(x, y, "-")

    def wall(self, x, y0, y1, ch="B"):
        for y in range(y0, y1 + 1):
            self.put(x, y, ch)

    def lava(self, x0, x1):
        for x in range(x0, x1 + 1):
            self.put(x, 18, "~")
            self.put(x, 19, "~")

    def spikes(self, x0, x1, y=17):
        for x in range(x0, x1 + 1):
            self.put(x, y, "^")

    def coins(self, xs, y):
        for x in xs:
            self.put(x, y, "c")

    def coin_arc(self, x0, y0):
        """Four coins in a jump arc starting at (x0, y0)."""
        for dx, dy in ((0, 0), (1, -1), (2, -1), (3, 0)):
            self.put(x0 + dx, y0 + dy, "c")

    def rows(self):
        return ["".join(r) for r in self.g]


class Level:
    def __init__(self, name, theme, rows):
        self.name = name
        self.theme = theme          # dict of colors, see game.py THEMES
        self.h = len(rows)
        self.w = max(len(r) for r in rows)
        self.grid = [r.ljust(self.w) for r in rows]

        self.coins = set()          # {(x, y)}
        self.gems = set()
        self.springs = set()
        self.spikes = set()
        self.movers = []            # [(x, y, axis)]
        self.powerups = []          # [(x, y)]
        self.checkpoints = []       # [(x, y)]
        self.resonance = set()      # prism node glyphs, {(x, y)}
        self.enemies = []           # [(kind, x, y)]
        self.start = (2, 16)
        self.flag = (self.w - 4, 8)
        #: The gem drawn highest in the grid (smallest row), used by the
        #: optional "high gem" challenge.  It is content metadata only and
        #: changes no collision geometry; a level with no gems leaves it None.
        self.high_gem = None

        clean = [list(r) for r in self.grid]
        for y, row in enumerate(self.grid):
            for x, ch in enumerate(row):
                if ch == " " or ch in "#B-~":
                    continue
                if ch == "^":
                    self.spikes.add((x, y))
                    continue
                if ch not in _ENTITY_GLYPHS:
                    raise ValueError(
                        "unknown level glyph %r at (%d, %d)" % (ch, x, y))
                clean[y][x] = " "
                if ch == "c":
                    self.coins.add((x, y))
                elif ch == "g":
                    self.gems.add((x, y))
                elif ch == "s":
                    self.springs.add((x, y))
                elif ch == "*":
                    self.checkpoints.append((x, y))
                elif ch == "e":
                    self.enemies.append(("walker", x, y))
                elif ch == "h":
                    self.enemies.append(("charger", x, y))
                elif ch == "w":
                    self.enemies.append(("spiker", x, y))
                elif ch == "f":
                    self.enemies.append(("flyer", x, y))
                elif ch == "t":
                    self.enemies.append(("sentry", x, y))
                elif ch == "Z":
                    self.enemies.append(("boss", x, y))
                elif ch == "b":
                    self.enemies.append(("king", x, y))
                elif ch == "S":
                    self.powerups.append((x, y))
                elif ch == "r":
                    self.resonance.add((x, y))
                elif ch == "M":
                    self.movers.append((x, y, "h"))
                elif ch == "K":
                    self.movers.append((x, y, "v"))
                elif ch == "P":
                    self.start = (x, y)
                elif ch == "F":
                    self.flag = (x, y)
        self.grid = ["".join(r) for r in clean]
        # Row 0 is the top of the grid, so the smallest row is the highest gem.
        # Ties break left-to-right to keep the marker deterministic.
        self.high_gem = (min(self.gems, key=lambda p: (p[1], p[0]))
                         if self.gems else None)

    def tile(self, x, y):
        if x < 0 or x >= self.w:
            return "#"              # level edges are walls
        if y < 0:
            return " "
        if y >= self.h:
            return " "
        return self.grid[y][x]

    def solid(self, x, y):
        return self.tile(x, y) in SOLID


# --------------------------------------------------------------- level 1 --
def _level1():
    b = Builder(190)
    b.ground(0, 44)
    b.put(3, 16, "P")
    b.coins(range(10, 15), 15)
    b.ground(30, 38, 16)                    # small hill
    b.put(34, 15, "e")
    b.coins(range(32, 37), 13)
    b.put(16, 15, "r")                      # tutorial prism, flat safe ground

    b.ground(50, 68)                        # pit 45..49
    b.coin_arc(46, 14)
    b.platform(55, 60, 15)
    b.coins(range(56, 60), 13)
    b.put(66, 17, "s")                      # spring up to gem ledge
    b.platform(62, 67, 8)
    b.put(64, 7, "g")

    b.ground(75, 110)                       # pit 69..74
    b.coin_arc(70, 14)
    b.put(80, 17, "*")
    b.spikes(88, 90)
    b.coins(range(88, 91), 14)
    b.put(96, 17, "e")
    b.put(104, 17, "e")

    b.ground(111, 130)
    b.wall(114, 17, 17)                     # brick stair
    b.wall(115, 16, 17)
    b.wall(116, 15, 17)
    b.coins(range(114, 117), 13)

    b.platform(132, 135, 15)                # pit 131..136 with bridge
    b.ground(137, 160)
    b.coins(range(140, 149, 2), 16)
    b.put(142, 17, "e")
    b.put(150, 17, "e")
    b.put(155, 16, "g")

    b.platform(163, 167, 16)                # pit 161..168 stepping bridge
    b.ground(169, 189)
    b.ground(176, 189, 16)                  # hills up to the flag
    b.ground(182, 189, 14)
    b.put(186, 8, "F")
    return Level("VERDANT RUN", "day", b.rows())


# --------------------------------------------------------------- level 2 --
def _level2():
    b = Builder(210)
    b.island(0, 209, 0, 1, "B")             # cave ceiling
    b.ground(0, 30)
    b.put(3, 16, "P")
    b.coins(range(8, 13), 16)

    b.lava(31, 44)                          # lava hop
    b.platform(32, 35, 16)
    b.platform(37, 40, 15)
    b.platform(42, 44, 16)
    b.coins((34, 39, 43), 12)
    b.put(38, 12, "S")
    b.put(33, 14, "r")                      # lava crossing: two-link chain
    b.put(38, 14, "r")

    b.ground(45, 80)
    b.spikes(52, 53)
    b.put(60, 17, "e")
    b.wall(66, 15, 17)
    b.wall(72, 15, 17)
    b.put(68, 17, "t")
    b.coins((69, 72), 12)
    b.put(78, 17, "*")

    b.lava(81, 92)                          # big lava lake, spring flight
    b.put(80, 17, "s")
    b.platform(85, 88, 12)
    b.coins(range(83, 90, 2), 7)

    b.ground(93, 130)
    b.put(95, 16, "g")
    b.put(100, 17, "e")
    b.put(108, 17, "e")
    b.spikes(115, 117)
    b.platform(114, 118, 15)
    b.coins(range(114, 119, 2), 13)

    b.ground(131, 170)
    b.put(134, 17, "*")
    b.put(140, 10, "f")
    b.put(156, 9, "f")
    b.platform(143, 146, 15)                # climb over the brick wall
    b.wall(148, 15, 17)
    b.put(148, 10, "g")
    b.coins(range(152, 159, 2), 16)
    b.put(162, 17, "e")

    b.lava(171, 178)
    b.platform(172, 174, 15)
    b.platform(176, 178, 14)
    b.coins((173, 177), 12)

    b.ground(179, 209)
    b.put(184, 17, "e")
    b.put(190, 17, "e")
    b.ground(196, 209, 16)
    b.ground(202, 209, 14)
    b.put(206, 8, "F")
    return Level("CRYSTAL CAVERNS", "cave", b.rows())


# --------------------------------------------------------------- level 3 --
def _level3():
    b = Builder(230)
    b.ground(0, 25)
    b.put(3, 16, "P")
    b.coins(range(8, 13), 16)

    b.ground(33, 95)                        # pit 26..32, flyer guard
    b.put(29, 12, "f")
    b.coin_arc(27, 13)
    b.spikes(40, 44)
    b.platform(39, 45, 15)
    b.coins(range(40, 45, 2), 13)
    b.put(50, 17, "e")
    b.put(56, 17, "e")

                        # stepping stone to the void
    b.island(66, 70, 15)                    # floating islands over the void
    b.island(75, 82, 13)
    b.island(84, 88, 15)
    b.island(91, 94, 10)
    b.coins((68, 76, 86), 11)
    b.put(87, 14, "s")
    b.put(92, 9, "t")
    b.put(92, 8, "g")
    b.put(63, 17, "*")                      # retry point before the void
    b.put(73, 9, "r")                       # mastery three-ring chain
    b.put(77, 9, "r")
    b.put(81, 9, "r")

    b.ground(96, 130)
    b.put(100, 17, "*")
    b.put(105, 17, "e")
    b.put(112, 17, "h")
    b.put(115, 8, "f")
    b.put(122, 8, "f")
    b.spikes(120, 122)
    b.platform(119, 123, 15)

    b.lava(131, 140)                        # lava flight
    b.put(129, 17, "s")
    b.platform(135, 137, 12)
    b.coins(range(133, 140, 2), 8)
    b.put(135, 9, "M")

    b.ground(141, 175)
    b.wall(150, 12, 17)                     # fortress walls
    b.wall(160, 12, 17)
    b.platform(146, 149, 15)
    b.put(148, 12, "t")
    b.put(155, 15, "g")
    b.coins(range(152, 159, 2), 16)
    b.platform(156, 159, 15)
    b.put(170, 17, "*")

    b.platform(177, 182, 16)                # pit 176..183
    b.put(181, 9, "f")
    b.ground(184, 229)
    b.put(188, 17, "e")
    b.put(193, 17, "e")
    for i, x in enumerate(range(196, 205, 2)):   # brick stairs
        b.wall(x, 17 - i, 17)
        b.wall(x + 1, 17 - i, 17)
    b.coins(range(196, 205, 2), 10)
    b.ground(206, 209, 13)
    b.ground(210, 229, 13)
    b.put(220, 3, "F")
    return Level("SKYFALL CITADEL", "dusk", b.rows())


# --------------------------------------------------------------- level 4 --
def _level4():
    b = Builder(240)
    b.ground(0, 24)
    b.put(3, 16, "P")
    b.coins(range(8, 13), 16)

    b.coin_arc(26, 13)                      # pit 25..29
    b.ground(30, 58)
    b.spikes(38, 40)
    b.platform(37, 41, 15)
    b.coins(range(38, 41), 13)
    b.put(46, 17, "e")
    
    b.put(58, 17, "s")                      # lava 59..72, spring flight
    b.lava(59, 72)
    b.platform(63, 66, 11)
    b.coins(range(62, 68, 2), 7)

    b.ground(73, 95)
    b.put(76, 16, "g")
    b.put(80, 17, "*")
    b.spikes(90, 91)
    b.put(86, 17, "e")

    b.island(98, 102, 14)                   # void crossing 96..120
    b.island(106, 110, 11)
    b.island(114, 118, 14)
    b.coins((100, 108, 116), 13)
    b.put(107, 10, "g")
    b.put(101, 13, "w")
    b.put(105, 15, "S")
    b.put(104, 12, "M")
    b.put(112, 11, "K")

    b.ground(121, 152)
    b.put(124, 17, "*")
    b.wall(130, 12, 17)
    b.platform(126, 129, 15)
    b.coins(range(126, 130), 12)
    b.put(134, 17, "e")
    b.put(142, 9, "f")
    b.spikes(144, 146)
    b.platform(143, 147, 15)
    b.put(150, 17, "e")

    b.lava(153, 164)                        # stepping stones
    b.platform(154, 157, 16)
    b.platform(160, 163, 15)
    b.coins((156, 162), 13)

    b.ground(165, 196)
    b.put(170, 17, "e")
    b.put(176, 17, "h")
    b.put(182, 17, "e")
    for i, x in enumerate(range(186, 195, 2)):
        b.wall(x, 17 - i, 17)
        b.wall(x + 1, 17 - i, 17)
    b.coins(range(186, 195, 2), 9)

    b.ground(197, 239, 16)                  # the spire
    b.ground(206, 239, 14)
    b.ground(214, 239, 12)
    b.put(222, 6, "f")
    b.put(226, 6, "g")
    b.put(230, 6, "F")
    return Level("OBSIDIAN SPIRE", "night", b.rows())


# --------------------------------------------------------------- level 5 --
def _level5():
    b = Builder(130)
    b.ground(0, 23)
    b.put(3, 16, "P")
    b.coins(range(8, 13), 16)

    b.coin_arc(25, 13)                      # pit 24..27
    b.ground(28, 129)
    b.put(30, 17, "e")
    b.put(34, 12, "f")
    b.put(37, 17, "t")
    b.put(56, 17, "h")
    b.put(20, 17, "*")

    b.platform(54, 58, 16)                  # the arena
    b.platform(66, 70, 15)
    b.platform(78, 82, 16)
    b.spikes(92, 93)
    b.coins((56, 68, 80), 14)
    b.wall(59, 17, 18)
    b.wall(67, 17, 18)
    b.put(68, 13, "S")
    b.put(72, 10, "Z")
    b.put(75, 12, "K")

    b.put(96, 17, "*")
    b.put(100, 17, "e")
    for i, x in enumerate(range(104, 112, 2)):
        b.wall(x, 17 - i, 17)
        b.wall(x + 1, 17 - i, 17)
    b.ground(114, 129, 16)
    b.put(118, 13, "g")
    b.coins((115, 119), 15)
    b.put(123, 8, "F")
    return Level("NIGHT THRONE", "night", b.rows())


# --------------------------------------------------------------- level 6 --
def _level6():
    b = Builder(230)
    b.ground(0, 25)
    b.put(3, 16, "P")
    b.coins(range(8, 13), 16)

    # mover ride over the wide pit (stones below as the safe route)
    b.put(32, 12, "M")
    b.platform(28, 31, 16)
    b.platform(36, 39, 16)
    b.coin_arc(30, 12)
    b.coins((33, 37), 11)

    # vertical lift up the clockwork cliff (stairs as fallback)
    b.ground(41, 90)
    b.put(45, 11, "K")
    for i, x in enumerate(range(47, 51)):
        b.wall(x, 17 - i // 2, 17)
    b.wall(52, 15, 17)
    b.platform(49, 51, 11)
    b.put(53, 12, "g")
    b.coins((46, 50), 9)

    # sentry corridor
    b.put(58, 17, "t")
    b.wall(63, 17, 18)
    b.wall(67, 17, 18)
    b.put(68, 13, "S")
    b.put(65, 17, "h")
    b.put(70, 17, "t")
    b.put(75, 17, "e")
    b.spikes(80, 82)
    b.platform(79, 83, 15)
    b.coins(range(80, 83), 14)
    b.put(87, 17, "*")

    # mover chain across the void (low stones as fallback)
    b.put(92, 17, "*")
    b.ground(91, 130)
    b.island(95, 99, 15)
    b.island(109, 113, 14)
    b.island(123, 127, 14)
    b.platform(101, 106, 17)
    b.platform(119, 124, 17)
    b.put(111, 11, "g")
    b.coins((97, 106, 111, 121, 125), 12)

    b.ground(131, 178)
    b.put(133, 17, "t")
    b.put(140, 17, "h")
    b.spikes(155, 158)
    b.platform(154, 159, 15)
    b.coins((155, 157), 14)
    b.put(165, 17, "*")
    b.wall(170, 15, 17)

    b.lava(179, 190)
    b.put(177, 17, "s")
    b.platform(182, 185, 12)
    b.coins((183, 187), 9)

    b.ground(191, 229)
    b.put(196, 17, "e")
    b.put(201, 17, "h")
    for i, x in enumerate(range(206, 214, 2)):
        b.wall(x, 17 - i, 17)
        b.wall(x + 1, 17 - i, 17)
    b.put(203, 17, "*")
    b.ground(215, 229, 13)
    b.put(218, 9, "b")
    b.put(220, 7, "g")
    b.put(225, 7, "F")
    return Level("CLOCKWORK VAULT", "dawn", b.rows())


def load_levels():
    return [_level1(), _level2(), _level3(), _level4(), _level5(),
            _level6()]
