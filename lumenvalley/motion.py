"""Cosmetic motion: the camera, the player's pose, and effect budgets.

Phase 6's gate is *movement feels at least as good as the original, no camera
jitter, reduced motion works*.  "Feels" is not measurable.  The parts that are
measurable live here, as pure functions with no pygame import:

* the camera never jumps by more than a few pixels in one frame, never reverses
  while the player runs one way, and stops dead when the player stands still;
* the player has one named pose per animation frame, chosen from distance
  travelled (feet planted) rather than wall-clock time, so the run cycle looks
  the same at any frame rate;
* effects are budgeted from the quality and reduced-motion settings, so the
  cheap path is a decision, not an accident.

The simulation owns the camera position (it is part of the saved run state and
it culls ambient effects); the renderer owns the shake offset, because a shake
must never consume gameplay randomness.
"""

import math

from entities import LAND_TIMER, PLAYER_W

from .constants import STEP, VIEW_W

# --- camera -------------------------------------------------------------

DEADZONE = 3.0        # cells the player may move before the camera engages
RELEASE = 0.35        # cells of offset at which follow disengages again
LOOKAHEAD = 0.18      # cells of lead per cell/second of horizontal speed
LOOKAHEAD_MAX = 3.5   # cells: a dash must not throw the camera a screen away
FOLLOW = 7.0          # 1/s: exponential approach rate
SETTLE = 0.05         # cells (one pixel): closer than this snaps, so it stops

SHAKE_MAX_T = 0.5     # seconds of shake the renderer may ask for
SHAKE_MAX_PX = 6.0    # ART_BIBLE cap on a single shake offset
MAX_SPEED = 24.0      # cells/s: camera pan speed cap (480 px/s)
ARENA_TRIGGER = 16.0  # cells: boss and hero must both be this close
ARENA_LEAD = 0.35     # share of the hero-to-boss gap the camera takes


def arena_focus(player_center, boss_center, trigger=ARENA_TRIGGER):
    """World-x the camera should frame during a boss encounter, or None.

    The arenas are wide enough that both fighters fit in the 80-cell view; the
    camera only shifts partway toward the boss so the hero never leaves the
    safe middle.  Outside ``trigger`` cells the normal follow camera applies.
    """
    if boss_center is None:
        return None
    gap = float(boss_center) - float(player_center)
    if abs(gap) > trigger:
        return None
    return float(player_center) + gap * ARENA_LEAD


class Camera:
    """Horizontal follow camera with a deadzone and a settle threshold.

    The old camera eased toward ``player + face*3 + vx*0.2`` every frame, so it
    chased every twitch, and the renderer rounded its position to whole 20 px
    cells: a full-speed run moved the world in 20 px snaps roughly twelve times
    a second.  This keeps the position smooth and hands the renderer a
    sub-cell value; the renderer rounds to whole pixels, not whole cells.
    """

    def __init__(self, view_w=VIEW_W):
        self.view_w = float(view_w)
        self.x = 0.0
        self.engaged = False
        self.shake = 0.0

    def reset(self, x=0.0):
        self.x = float(x)
        self.engaged = False
        self.shake = 0.0

    def bounds(self, level_w):
        return 0.0, max(0.0, float(level_w) - self.view_w)

    def clamp(self, level_w):
        lo, hi = self.bounds(level_w)
        self.x = max(lo, min(hi, self.x))

    def update(self, dt, player_x, vx, level_w, focus_x=None):
        """Follow ``player_x`` for one step of ``dt`` seconds.

        ``focus_x`` is an optional world-x override (boss arena framing).  The
        approach is exponential but speed-capped, so a long transition is a
        smooth pan rather than a one-frame camera cut.
        """
        centre = player_x + PLAYER_W / 2.0
        if focus_x is None:
            offset = centre - (self.x + self.view_w / 2.0)
            if self.engaged:
                if abs(offset) < RELEASE:
                    self.engaged = False
            elif abs(offset) > DEADZONE:
                self.engaged = True
            target = centre - self.view_w / 2.0
            if self.engaged:
                lead = max(-LOOKAHEAD_MAX,
                           min(LOOKAHEAD_MAX, vx * LOOKAHEAD))
                target = centre + lead - self.view_w / 2.0
        else:
            self.engaged = True
            target = float(focus_x) - self.view_w / 2.0
        if self.engaged:
            # Frame-rate independent exponential approach: half the remaining
            # distance after ``ln(2)/FOLLOW`` seconds, whatever the step is.
            step = (target - self.x) * (1.0 - math.exp(-FOLLOW * dt))
            limit = max(0.0, MAX_SPEED * dt)
            step = max(-limit, min(limit, step))
            self.x += step
            if abs(target - self.x) < SETTLE:
                self.x = target
        self.clamp(level_w)
        if self.shake > 0.0:
            self.shake = max(0.0, self.shake - dt)

    def kick(self, seconds):
        self.shake = min(SHAKE_MAX_T, max(self.shake, seconds))


# --- player poses -------------------------------------------------------

# ART_BIBLE §5: an eight-frame identity across idle, run, jump, fall and dash.
# The run cycle is four frames, stepped by ground distance (STRIDE cells per
# frame, so a full stride is eight cells ~ 160 px).
RUN_FRAMES = ("RUN_A", "RUN_D", "RUN_B", "RUN_C")
STRIDE = 2.0
RUN_MIN_VX = 0.8          # cells/s: below this the player is standing
ANTICIPATION = 2 * STEP   # take-off crouch, in frames
LAND_SQUASH_T = 3 * STEP  # landing squash lasts exactly three frames
LAND_SQUASH = 0.28
AIR_STRETCH = 0.10
DASH_STRETCH = 0.16
APEX_VY = 2.2             # |vy| below this counts as the jump apex
WALL_JUMP_POSE_T = 0.18   # seconds the push-off pose is held
RESPAWN_POSE_T = 0.35     # seconds the materialise pose is held
HURT_POSE_T = 0.18        # seconds of lethal-hit flinch before the body


def run_frame(distance):
    """Run-cycle index for ground distance in cells (0..3)."""
    return int(distance // STRIDE) % len(RUN_FRAMES)


def player_pose(on_ground=False, vx=0.0, vy=0.0, dash_t=0.0, land_t=0.0,
                air_t=0.0, distance=0.0, wall_dir=0, wall_jump_t=0.0,
                dead=False, hurt_t=0.0, respawn_t=0.0, celebrate=False):
    """Return ``(frame, fx, fy)`` for the player, as a pure function.

    ``frame`` is a sprite name without the ``PLAYER_`` prefix.  ``fx``/``fy``
    are squash/stretch multipliers; the renderer scales the 8x8 sprite by them.
    States are checked in priority order, so a dash beats a land, a death beats
    everything, and an airborne frame never shows a run cycle.
    """
    fx = fy = 1.0
    if dead:
        # One-hit deaths: the flinch is the "damage" state, then the body.
        if hurt_t > 0:
            return "HURT", 1.06, 0.94
        return "DEAD", 1.0, 1.0
    if celebrate:
        return "VICTORY", 1.0, 1.0
    if dash_t > 0:
        frame = "DASH"
        fx = 1.0 + DASH_STRETCH
        fy = 1.0 / fx
    elif respawn_t > 0:
        # Scale up from the ground as the hero re-materialises.
        k = min(1.0, respawn_t / RESPAWN_POSE_T)
        frame = "RESPAWN"
        fx = fy = 1.0 - 0.22 * k
    elif wall_jump_t > 0:
        frame = "WALL_JUMP"
        fx, fy = 1.08, 0.94
    elif not on_ground:
        if air_t < ANTICIPATION + 1e-9:
            # Take-off flex.  The physics has no wind-up frames, so the
            # anticipation is drawn on the first two airborne frames instead
            # of delaying the jump (which would change how it plays).
            frame = "ANTICIPATE"
            fx, fy = 1.08, 0.88
        elif wall_dir:
            frame = "WALL"
            fx, fy = 0.94, 1.04
        elif abs(vy) < APEX_VY:
            frame = "APEX"
            fy = 1.0 + AIR_STRETCH * 0.5
            fx = 2.0 - fy
        elif vy < 0:
            frame = "JUMP"
            fy = 1.0 + AIR_STRETCH
            fx = 2.0 - fy
        else:
            frame = "FALL"
            fy = 1.0 + AIR_STRETCH * 0.5
            fx = 2.0 - fy
    elif land_t > LAND_TIMER - LAND_SQUASH_T:
        # The gameplay timer is 0.14 s (frozen by the Phase 2 trace); only its
        # first three frames drive the visible squash.
        frame = "LAND"
        k = (land_t - (LAND_TIMER - LAND_SQUASH_T)) / LAND_SQUASH_T
        k = max(0.0, min(1.0, k))
        fy = 1.0 - LAND_SQUASH * k
        fx = 1.0 + LAND_SQUASH * 0.9 * k
    elif abs(vx) > RUN_MIN_VX:
        frame = RUN_FRAMES[run_frame(distance)]
    else:
        frame = "IDLE"
    return frame, fx, fy


# --- effects ------------------------------------------------------------

AFTERIMAGE_ALPHA = 102     # 40 % of 255 (ART_BIBLE §5)
AFTERIMAGE_STEP = 0.75     # cells between dash afterimages


def afterimages(quality=1, motion=1):
    """Offsets (cells behind the player) of the dash afterimages to draw."""
    if int(motion) == 0:
        return ()
    count = 3 if int(quality) else 1
    return tuple(AFTERIMAGE_STEP * (i + 1) for i in range(count))


def ember_chance(quality=1):
    """Probability per step that a lava tile emits an ember."""
    return 0.25 if int(quality) else 0.10


def background_time(t, motion=1):
    """Clock for time-driven ambient drift; frozen under reduced motion."""
    return float(t) if int(motion) else 0.0


def prism_pulse(t, motion=1):
    """0..1 pulse for an armed prism ring; a steady mid-value when reduced."""
    if not int(motion):
        return 0.5
    return 0.5 + 0.5 * math.sin(float(t) * math.tau * 1.6)


def shake_offset(rng, shake, strength=1, motion=1):
    """One frame of screen shake, in pixels, capped by the art bible.

    ``strength`` is the settings row (0 off, 1 normal, 2 strong).  The caller
    passes a cosmetic RNG: drawing must never consume gameplay randomness.
    """
    if not int(motion) or shake <= 0 or strength <= 0:
        return 0, 0
    amp = min(SHAKE_MAX_PX, SHAKE_MAX_PX * (shake / SHAKE_MAX_T)
              * (0.5 if strength == 1 else 1.0))
    sx = int(round(rng.uniform(-1, 1) * amp))
    sy = int(round(rng.uniform(-1, 1) * amp * 0.7))
    return sx, sy
