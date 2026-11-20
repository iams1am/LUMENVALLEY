"""Player, enemies and particles. All coordinates are float grid cells;
x grows right, y grows down. Terminal cells are ~2x taller than wide, so
horizontal speeds are roughly double the vertical ones."""

import math
import random

from lumenvalley.bestiary import PROFILES, telegraph

GRAVITY = 55.0
MAX_FALL = 30.0
RUN_SPEED = 16.0
RUN_ACCEL = 90.0
FRICTION = 60.0
JUMP_V = 22.0
SPRING_V = 32.0
STOMP_BOUNCE = 14.0
DASH_SPEED = 26.0
DASH_TIME = 0.16
COYOTE = 0.10
JUMP_BUFFER = 0.14
# The landing timer is a gameplay-state field captured by the Phase 2 golden
# trace, so its duration is frozen at the pre-refactor 0.14 s.  The *visible*
# squash uses only the first three frames of that window (lumenvalley.motion).
LAND_TIMER = 0.14

PLAYER_W = 0.85
PLAYER_H = 2.0


class Player:
    def __init__(self, x, y):
        self.reset(x, y)

    def reset(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.face = 1
        self.on_ground = False
        self.coyote = 0.0
        self.jump_buf = 0.0
        self.drop_timer = 0.0       # ignore one-way platforms briefly
        self.invuln = 0.0
        self.land_t = 0.0           # squash timer for landing juice
        self.air_t = 0.0            # seconds since take-off (pose selection)
        self.run_dist = 0.0         # cells travelled: drives the run cycle
        self.wall_dir = 0           # -1/1 while airborne against a wall
        self.wall_jump_t = 0.0      # wall-jump push-off pose window
        self.hurt_t = 0.0           # lethal-hit flinch window
        self.respawn_t = 0.0        # materialise pose window
        self.celebrate = False      # victory pose on the results screen
        self.just_jumped = False
        self.just_dashed = False
        self.dash_t = 0.0
        self.dash_cd = 0.0
        self.can_dash = True
        self.no_wall_t = 0.0
        self.alive = True

    @property
    def bottom(self):
        return self.y + PLAYER_H

    def rect(self):
        return (self.x, self.y, PLAYER_W, PLAYER_H)

    def resonate(self):
        """Prism hit: refuel and extend the active dash, without a new input.

        The extension is a full dash window rather than a fixed tail so a row
        of rings can chain at a readable rhythm.  ``can_dash`` is restored at
        the same time; landing is still the only way to reset the ring chain.
        """
        self.can_dash = True
        self.dash_cd = 0.0
        self.dash_t = max(self.dash_t, DASH_TIME)
        self.vy = 0.0

    def update(self, dt, level, left, right, jump_pressed, down_pressed,
               particles, dash_pressed=False, rng=None):
        rng = rng or random
        # -- horizontal ----------------------------------------------------
        want = (1 if right else 0) - (1 if left else 0)
        if want:
            self.face = want
            self.vx += want * RUN_ACCEL * dt
            self.vx = max(-RUN_SPEED, min(RUN_SPEED, self.vx))
        else:
            if self.vx > 0:
                self.vx = max(0.0, self.vx - FRICTION * dt)
            else:
                self.vx = min(0.0, self.vx + FRICTION * dt)

        # -- jumping: buffer + coyote time ---------------------------------
        self.coyote = max(0.0, self.coyote - dt)
        self.jump_buf = max(0.0, self.jump_buf - dt)
        self.drop_timer = max(0.0, self.drop_timer - dt)
        self.invuln = max(0.0, self.invuln - dt)
        self.land_t = max(0.0, self.land_t - dt)
        self.wall_jump_t = max(0.0, self.wall_jump_t - dt)
        self.respawn_t = max(0.0, self.respawn_t - dt)
        self.hurt_t = max(0.0, self.hurt_t - dt)
        self.dash_cd = max(0.0, self.dash_cd - dt)
        self.no_wall_t = max(0.0, self.no_wall_t - dt)
        if jump_pressed:
            self.jump_buf = JUMP_BUFFER
        if down_pressed and self.on_ground:
            self.drop_timer = 0.25
        if self.jump_buf > 0 and (self.on_ground or self.coyote > 0):
            self.vy = -JUMP_V
            self.on_ground = False
            self.coyote = 0.0
            self.jump_buf = 0.0
            self.no_wall_t = 0.18
            self.just_jumped = True
        elif (self.jump_buf > 0 and not self.on_ground
                and self.no_wall_t <= 0):
            xl = int(self.x - 0.15)
            xr = int(self.x + PLAYER_W + 0.15)
            wall_l = (level.solid(xl, int(self.y + 0.4))
                      or level.solid(xl, int(self.bottom - 0.6)))
            wall_r = (level.solid(xr, int(self.y + 0.4))
                      or level.solid(xr, int(self.bottom - 0.6)))
            if wall_l or wall_r:
                d = 1 if wall_l else -1
                self.vy = -JUMP_V * 0.92
                self.vx = d * 9.0
                self.face = d
                self.dash_t = 0.0
                self.jump_buf = 0.0
                self.can_dash = True
                self.just_jumped = True
                self.wall_jump_t = 0.18
                self.wall_dir = -d

        # -- gravity / air dash --------------------------------------------
        if (dash_pressed and self.can_dash and not self.on_ground
                and self.dash_t <= 0 and self.dash_cd <= 0):
            self.dash_t = DASH_TIME
            self.dash_cd = 0.35
            self.can_dash = False
            self.vy = 0.0
            self.just_dashed = True
        if self.dash_t > 0:
            self.dash_t -= dt
            self.vx = self.face * DASH_SPEED
            self.vy = 0.0
            particles.spawn(self.x + PLAYER_W / 2, self.bottom - 0.7,
                            -self.face * 3, rng.uniform(-1, 1),
                            0.22, "·", 246)
        else:
            self.vy = min(MAX_FALL, self.vy + GRAVITY * dt)

        # -- move & collide, axis separated --------------------------------
        before_x = self.x
        self._move_x(self.vx * dt, level)
        was_falling = self.vy > 8
        self._move_y(self.vy * dt, level)
        # Distance actually covered, not requested: a run cycle stepped by
        # intent keeps pumping while the player is pinned against a wall.
        self.run_dist += abs(self.x - before_x)
        if self.on_ground:
            self.air_t = 0.0
        else:
            self.air_t += dt
        # Wall contact is a pose input, not a physics input: probe the cells
        # beside the body so the sprite can lean while the player slides.
        self.wall_dir = 0
        if not self.on_ground:
            cy = int(self.y + PLAYER_H * 0.55)
            if level.solid(int(self.x - 0.05), cy):
                self.wall_dir = -1
            elif level.solid(int(self.x + PLAYER_W + 0.05), cy):
                self.wall_dir = 1
        if self.on_ground and was_falling:
            # Three frames of visible squash (ART_BIBLE §5).  The timer itself
            # stays 0.14 s because the Phase 2 lock records it; lumenvalley.motion
            # reads only the first three frames of the window.
            self.land_t = LAND_TIMER
            particles.dust(self.x, self.bottom)

    def _move_x(self, dx, level):
        self.x += dx
        x0, x1 = self.x, self.x + PLAYER_W
        for cy in range(int(self.y), int(self.y + PLAYER_H - 0.01) + 1):
            if dx > 0 and level.solid(int(x1), cy):
                self.x = int(x1) - PLAYER_W - 0.001
                self.vx = 0
                break
            if dx < 0 and level.solid(int(x0), cy):
                self.x = int(x0) + 1.001
                self.vx = 0
                break

    def _move_y(self, dy, level):
        prev_bottom = self.bottom
        self.y += dy
        if self.on_ground:
            self.coyote = COYOTE
        self.on_ground = False
        x_cells = {int(self.x + 0.05), int(self.x + PLAYER_W - 0.05)}
        if dy > 0:                                      # falling
            foot = self.bottom
            for cx in x_cells:
                t = level.tile(cx, int(foot))
                landed = t in "#B"
                if (t == "-" and self.drop_timer <= 0
                        and prev_bottom <= int(foot) + 0.01):
                    landed = True
                if landed:
                    self.y = int(foot) - PLAYER_H
                    self.vy = 0
                    self.on_ground = True
                    self.can_dash = True
                    return
        elif dy < 0:                                    # rising: bump head
            for cx in x_cells:
                if level.solid(cx, int(self.y)):
                    self.y = int(self.y) + 1.001
                    self.vy = 0
                    return


class Walker:
    """Patrols the ground near its post, flips at walls and ledges."""
    kind = "walker"
    SPEED = 4.5
    LEASH = 6

    def __init__(self, x, y):
        self.sx, self.sy = x, y
        self.reset()

    def reset(self):
        self.x = float(self.sx)
        self.y = float(self.sy)
        self.dir = -1
        self.raging = False
        self.alive = True

    def update(self, dt, level, px=None, py=None):
        nx = self.x + self.dir * self.SPEED * dt
        if abs(nx - self.sx) > self.LEASH:
            self.dir = -self.dir
            return
        edge = int(nx + (0.9 if self.dir > 0 else 0.1))
        foot = int(self.y) + 1
        wall = level.solid(edge, int(self.y))
        ledge = level.tile(edge, foot) not in "#B-"
        if wall or ledge:
            self.dir = -self.dir
        else:
            self.x = nx


class Charger(Walker):
    """Patrols slowly but rushes at the hero on sight. Stompable.

    The rush is telegraphed: on sighting the hero it freezes for ``TELL``
    seconds (leaning back, sparks at its feet) before it moves at ``RUSH``, so
    the first frame of aggression is readable rather than instant.  The freeze
    is the whole difference between "reacted to" and "got hit from nowhere".
    """
    kind = "charger"
    SPEED = 1.6
    RUSH = 8.5
    LEASH = 7
    SIGHT_X = 8.0
    SIGHT_Y = 3.0
    TELL = telegraph("charger")

    def reset(self):
        super().reset()
        self.tell_t = 0.0
        self.tell_max = self.TELL

    def update(self, dt, level, px=None, py=None):
        spotted = (px is not None and py is not None
                   and abs(px - self.x) < self.SIGHT_X
                   and abs(py - self.y) < self.SIGHT_Y)
        if spotted and not self.raging:
            self.raging = True
            self.tell_t = self.TELL
        elif not spotted:
            self.raging = False
            self.tell_t = 0.0
        if self.raging:
            self.dir = -1 if px < self.x else 1
            if self.tell_t > 0:
                self.tell_t = max(0.0, self.tell_t - dt)
                return
        spd = self.RUSH if self.raging else self.SPEED
        nx = self.x + self.dir * spd * dt
        if not self.raging and abs(nx - self.sx) > self.LEASH:
            self.dir = -self.dir
            return
        edge = int(nx + (0.9 if self.dir > 0 else 0.1))
        foot = int(self.y) + 1
        if (level.solid(edge, int(self.y))
                or level.tile(edge, foot) not in "#B-"):
            if not self.raging:
                self.dir = -self.dir
            return
        self.x = nx


class Spiker(Walker):
    """Armored roller - deadly from every side, stomp included."""
    kind = "spiker"


class Flyer:
    """Bobs up and down in place. Stompable."""
    kind = "flyer"

    def __init__(self, x, y, rng=None):
        self.sx, self.sy = x, y
        self.phase = (rng or random).random() * math.tau
        self.reset()

    def reset(self):
        self.x = float(self.sx)
        self.y = float(self.sy)
        self.t = 0.0
        self.alive = True

    def update(self, dt, level):
        self.t += dt
        self.y = self.sy + math.sin(self.t * 2.2 + self.phase) * 2.4


class Particles:
    DEFAULT_CAP = 320

    def __init__(self, rng=None, cap=None):
        self.items = []             # [x, y, vx, vy, life, ch, color]
        self.rng = rng or random
        self.cap = self.DEFAULT_CAP if cap is None else int(cap)
        self.density = 1.0

    def set_budget(self, quality=1, motion=1):
        """Scale bursts and the hard cap from the two presentation settings.

        Reduced motion keeps feedback but thins it; low graphics shortens the
        ambient budget.  The cap is a hard bound, not a suggestion: a bug that
        spawns every frame must not grow the list without limit.
        """
        self.density = (1.0 if int(quality) else 0.6) \
            * (1.0 if int(motion) else 0.5)
        self.cap = max(48, int(self.DEFAULT_CAP * self.density))

    def spawn(self, x, y, vx, vy, life, ch, color):
        if len(self.items) >= self.cap:
            return
        self.items.append([x, y, vx, vy, life, ch, color])

    def burst(self, x, y, n, chs, colors, speed=8.0):
        if self.density < 1.0:
            n = max(1, int(round(n * self.density)))
        for _ in range(n):
            a = self.rng.random() * math.tau
            self.spawn(x, y, math.cos(a) * speed * 1.6,
                       math.sin(a) * speed - 4,
                       self.rng.uniform(0.25, 0.6),
                       self.rng.choice(chs), self.rng.choice(colors))

    def coin(self, x, y):
        self.burst(x + 0.5, y + 0.5, 4, "·+", (226, 220, 229), 6)

    def gem(self, x, y):
        self.burst(x + 0.5, y + 0.5, 8, "·+*", (201, 213, 219), 9)

    def resonance(self, x, y):
        self.burst(x, y, 12, "·+*○", (51, 45, 195, 231), 10)

    def stomp(self, x, y):
        self.burst(x + 0.5, y + 0.5, 6, "x·", (250, 244), 8)

    def dust(self, x, y):
        for dx in (-0.4, 1.2):
            self.spawn(x + dx, y - 0.4, dx * 3, -1.5, 0.25, "·", 246)

    def takeoff(self, x, y):
        """Deterministic jump puff (does not consume RNG)."""
        for dx, vx in ((-0.35, -2.2), (0.35, 2.2), (0.0, 0.0)):
            self.spawn(x + dx, y - 0.25, vx, -1.0, 0.20, "·", 250)

    def dash(self, x, y, face=1):
        """Deterministic dash spark burst (does not consume RNG)."""
        for i in range(5):
            self.spawn(x - face * (0.2 + i * 0.16), y - 0.7 + (i % 3) * 0.25,
                       -face * (6.0 + i), -0.8 + i * 0.4, 0.18 + i * 0.02,
                       "+", (120, 210, 255))

    def wall(self, x, y, face=1):
        """Small shove-off puff for a wall jump (does not consume RNG)."""
        for i in range(3):
            self.spawn(x - face * 0.2, y - 0.5 - i * 0.35,
                       -face * (2.0 + i), -1.2, 0.22, "·", 250)

    def death(self, x, y):
        self.burst(x + 0.5, y + 1, 14, "*+x", (196, 208, 226, 252), 11)

    def firework(self, x, y):
        self.burst(x, y, 16, "*+·", (196, 226, 46, 51, 201, 208), 10)

    def update(self, dt):
        alive = []
        for p in self.items:
            p[4] -= dt
            if p[4] > 0:
                p[0] += p[2] * dt
                p[1] += p[3] * dt
                p[3] += 25 * dt
                alive.append(p)
        self.items = alive


class Sentry:
    """Stationary turret. Fires slow bolts at the hero when in range."""
    kind = "sentry"
    RANGE_X = 13.0
    RANGE_Y = 5.0
    COOLDOWN = 1.7

    def __init__(self, x, y):
        self.sx, self.sy = x, y
        self.reset()

    def reset(self):
        self.x = float(self.sx)
        self.y = float(self.sy)
        self.cd = 0.9
        self.aiming = False
        self.face = -1
        self.alive = True

    @property
    def about_to_fire(self):
        """Muzzle glowing *and* the hero in range: the glow is a promise."""
        return bool(self.aiming
                    and self.cd <= PROFILES["sentry"]["telegraph"])

    def update(self, dt, level, px, py, shots):
        self.t = getattr(self, "t", 0.0) + dt
        self.cd -= dt
        dx = px - (self.x + 0.5)
        if abs(dx) > 0.4:
            self.face = 1 if dx > 0 else -1
        self.aiming = abs(dx) < self.RANGE_X and abs(py - self.y) < self.RANGE_Y
        if self.aiming and self.cd <= 0:
            self.cd = self.COOLDOWN
            shots.append(Projectile(self.x + 0.5 + self.face * 0.7,
                                    self.y + 0.35, self.face * 9.5))


class Projectile:
    SPEED = 9.5

    def __init__(self, x, y, vx):
        self.x = x
        self.y = y
        self.vx = vx
        self.life = 3.0
        self.alive = True

    def rect(self):
        return (self.x - 0.25, self.y, 0.5, 0.5)

    def update(self, dt, level):
        self.x += self.vx * dt
        self.life -= dt
        if (self.life <= 0 or abs(self.vx) < 0.1
                or level.solid(int(round(self.x)), int(round(self.y)))):
            self.alive = False


class Mover:
    """Moving platform. Carries the hero along its oscillation axis."""
    kind = "mover"
    W = 3.0
    SPEED = 1.6
    AMP = 2.5

    def __init__(self, x, y, axis="h"):
        self.sx, self.sy = float(x), float(y)
        self.axis = axis
        self.reset()

    def reset(self):
        self.x = self.sx
        self.y = self.sy
        self.px = self.x
        self.py = self.y
        self.t = 0.0

    def update(self, dt):
        self.px = self.x
        self.py = self.y
        self.t += dt
        off = math.sin(self.t * self.SPEED) * self.AMP
        if self.axis == "h":
            self.x = self.sx + off
            self.y = self.sy
        else:
            self.x = self.sx
            self.y = self.sy - off


def make_enemy(kind, x, y, rng=None):
    if kind == "walker":
        return Walker(x, y)
    if kind == "charger":
        return Charger(x, y)
    if kind == "spiker":
        return Spiker(x, y)
    if kind == "sentry":
        return Sentry(x, y)
    if kind == "boss":
        return Boss(x, y, rng=rng)
    if kind == "king":
        return Boss(x, y, king=True, rng=rng)
    return Flyer(x, y, rng=rng)


class Boss:
    """Arena guardian. Hovers near its throne, periodically dives at the
    hero and must be stomped three times. Grows faster with every hit."""

    kind = "boss"
    blocks_goal = True
    W = 2.4
    H = 2.0

    def __init__(self, x, y, king=False, rng=None):
        self.king = bool(king)
        self.kind = "king" if self.king else "boss"
        self.sx, self.sy = x, y
        self.rng = rng or random
        self.reset()

    @property
    def display_name(self):
        return "CRYSTAL KING" if self.king else "OBSIDIAN GUARDIAN"

    def reset(self):
        self.x = float(self.sx)
        self.y = float(self.sy)
        base_hp = 5 if self.king else 3
        self.hp = base_hp
        self.max_hp = base_hp
        self.state = "hover"
        self.st = 0.0
        self.tell = telegraph(self.kind)
        self.tell_t = 0.0
        self.tell_max = self.tell
        self.t = self.rng.random() * math.tau
        self.hurt = 0.0
        self.face = -1
        self.alive = True
        self.dvx = 0.0
        self.dvy = 0.0
        self.tx = self.sx
        self.ty = self.sy
        self.shoot_cd = 1.2

    def rect(self):
        return (self.x, self.y, self.W, self.H)

    def hit(self):
        """Return True if a stomp landed (not in recovery)."""
        if self.hurt > 0:
            return False
        self.hurt = 0.9 if self.king else 1.1
        self.hp -= 1
        return True

    def update(self, dt, level, px=0.0, py=0.0, shots=None):
        if not self.alive:
            return
        self.t += dt
        self.st += dt
        self.hurt = max(0.0, self.hurt - dt)
        rage = self.max_hp - self.hp
        agro = abs(px - self.x) < 32.0
        lo_y, hi_y = 1.0, level.h - 2.6
        lo_x, hi_x = 1.0, level.w - self.W - 1.0

        if self.state == "hover":
            self.y = self.sy + math.sin(self.t * 2.6) * 1.1
            self.face = -1 if px < self.x else 1
            hover_dur = max(0.8, 2.2 - rage * 0.45)
            if self.king and shots is not None:
                self.shoot_cd = getattr(self, "shoot_cd", 1.2) - dt
                if agro and self.shoot_cd <= 0:
                    self.shoot_cd = max(1.1, 2.0 - rage * 0.25)
                    shots.append(Projectile(
                        self.x + self.W / 2 + self.face * 0.8,
                        self.y + 0.9,
                        self.face * 10.5))
            if agro and self.st > hover_dur:
                # Gather before the dive: the boss holds station, the renderer
                # opens a closing ring, and the dive aims where the hero is
                # when the tell ends, so the tell is the dodge window.
                self.state = "wind"
                self.st = 0.0
                self.tell_t = self.tell
        elif self.state == "wind":
            self.face = -1 if px < self.x else 1
            self.y = self.sy + math.sin(self.t * 2.6) * 1.1
            if self.king and shots is not None:
                self.shoot_cd = getattr(self, "shoot_cd", 1.2) - dt
                if agro and self.shoot_cd <= 0:
                    self.shoot_cd = max(1.1, 2.0 - rage * 0.25)
                    shots.append(Projectile(
                        self.x + self.W / 2 + self.face * 0.8,
                        self.y + 0.9,
                        self.face * 10.5))
            self.tell_t = max(0.0, self.tell_t - dt)
            if self.tell_t <= 0.0:
                self.tx = px
                self.ty = min(py, hi_y)
                d = math.hypot(self.tx - self.x, self.ty - self.y) or 1.0
                self.dvx = (self.tx - self.x) / d
                self.dvy = (self.ty - self.y) / d
                self.state = "dive"
                self.st = 0.0
        elif self.state == "dive":
            v = 13.0 * (1.0 + rage * (0.3 if self.king else 0.22)) * dt
            self.x = max(lo_x, min(hi_x, self.x + self.dvx * v))
            self.y = max(lo_y, min(hi_y, self.y + self.dvy * v))
            if (self.st > 1.5
                    or math.hypot(self.tx - self.x, self.ty - self.y) < 0.6):
                self.state = "return"
                self.st = 0.0
        else:
            d = math.hypot(self.sx - self.x, self.sy - self.y) or 1.0
            v = (7.0 + rage) * dt
            self.x += (self.sx - self.x) / d * v
            self.y = max(lo_y, self.y + (self.sy - self.y) / d * v)
            if d < 0.5:
                self.state = "hover"
                self.st = 0.0
