"""The enemy and boss contract: what each kind is and how it warns you.

Phase 9's gate is *bosses unskippable, readable, distinguishable*.  Only the
first half is a boolean (a living ``blocks_goal`` enemy stops the flag, asserted
in ``tests/test_regressions.py``).  The other two need a written rule, because
"readable" and "distinguishable" are otherwise opinions:

* **Readable** — every enemy whose attack can hurt an unwarned player declares a
  telegraph, in seconds, during which it is visibly winding up and *cannot*
  carry out the attack.  A patrol that never changes pace needs none; a charger
  that goes from 1.6 to 8.5 cells/s and a boss that dives across the arena do.
* **Distinguishable** — every kind owns a sprite set whose silhouette (the set
  of filled pixels, ignoring colour) is different from every other kind's, and
  the two bosses own different sets and different attack lists.  Colour alone
  is not a distinction: the palette converts per theme and accessibility
  settings can flatten it (D16).

The simulation reads its timings from here and both renderers read their
sprite names from :func:`enemy_pose`, so a new enemy cannot ship without a tell
and a silhouette, and the terminal build cannot drift from the pygame one.
"""

import math

# Every key here is a ``kind`` produced by ``entities.make_enemy``.  ``sprites``
# are attribute names in ``sprites.py``; ``wind`` is the frame shown while the
# telegraph is running (None means the tell reuses the watch frame).
PROFILES = {
    "walker": {
        "label": "WALKER",
        "role": "patrol",
        "telegraph": 0.0,
        "stompable": True,
        "blocks_goal": False,
        "attack": "contact",
        "sprites": ("WALKER_A", "WALKER_B"),
        "wind": None,
    },
    "charger": {
        "label": "CHARGER",
        "role": "rusher",
        # It used to accelerate from patrol to rush on the same frame it saw
        # the hero.  The tell freezes it long enough to read the direction.
        "telegraph": 0.32,
        "stompable": True,
        "blocks_goal": False,
        "attack": "contact",
        "sprites": ("CHARGER_A", "CHARGER_B"),
        "wind": "CHARGER_WIND",
    },
    "spiker": {
        "label": "SPIKER",
        "role": "armored",
        "telegraph": 0.0,
        "stompable": False,
        "blocks_goal": False,
        "attack": "contact",
        "sprites": ("SPIKER_A", "SPIKER_B"),
        "wind": None,
    },
    "sentry": {
        "label": "SENTRY",
        "role": "turret",
        # Fires only after the muzzle has glowed, and only when the hero is in
        # range, so the glow is a promise rather than decoration.
        "telegraph": 0.30,
        "stompable": True,
        "blocks_goal": False,
        "attack": "bolt",
        "sprites": ("SENTRY_A", "SENTRY_B"),
        "wind": "SENTRY_B",
    },
    "flyer": {
        "label": "FLYER",
        "role": "aerial",
        "telegraph": 0.0,
        "stompable": True,
        "blocks_goal": False,
        "attack": "contact",
        "sprites": ("FLYER_UP", "FLYER_DOWN"),
        "wind": None,
    },
    "boss": {
        "label": "OBSIDIAN GUARDIAN",
        "role": "guardian",
        # Hover -> tell -> dive.  The dive aims where the hero stands when the
        # tell ends, so the telegraph is the dodge window.
        "telegraph": 0.45,
        "stompable": True,
        "blocks_goal": True,
        "attack": "dive",
        "sprites": ("BOSS_A", "BOSS_B"),
        "wind": "BOSS_WIND",
    },
    "king": {
        "label": "CRYSTAL KING",
        "role": "final",
        "telegraph": 0.45,
        "stompable": True,
        "blocks_goal": True,
        "attack": "dive+bolt",
        "sprites": ("KING_A", "KING_B"),
        "wind": "KING_WIND",
    },
}

KINDS = tuple(PROFILES)
BOSS_KINDS = tuple(k for k in KINDS if PROFILES[k]["blocks_goal"])


def profile(kind):
    """The contract entry for an enemy kind (a ``Flyer`` for unknown kinds)."""
    return PROFILES.get(kind, PROFILES["flyer"])


def telegraph(kind):
    return float(PROFILES.get(kind, PROFILES["flyer"])["telegraph"])


def is_telling(e):
    """True while an enemy is visibly winding up before its attack."""
    if getattr(e, "tell_t", 0.0) > 0.0:
        return True
    return bool(getattr(e, "about_to_fire", False))


def tell_amount(e):
    """0..1 fill of the current telegraph, for rim/arc decoration.

    Reaches 1.0 on the frame the attack is released, so a renderer can draw a
    ring that closes exactly when the charger rushes or the boss dives.
    """
    tell_max = getattr(e, "tell_max", 0.0)
    if tell_max > 0.0 and getattr(e, "tell_t", 0.0) > 0.0:
        return max(0.0, min(1.0, 1.0 - e.tell_t / tell_max))
    if getattr(e, "about_to_fire", False):
        window = max(0.01, PROFILES["sentry"]["telegraph"])
        return max(0.0, min(1.0, 1.0 - max(0.0, e.cd) / window))
    return 0.0


def enemy_pose(e, t):
    """``(sprite attribute name, tell fill)`` for one enemy this frame.

    One rule for both front-ends: while the tell runs the wind frame replaces
    the loop, so the warning cannot be visible in one renderer and missing in
    the other.
    """
    p = profile(e.kind)
    fill = tell_amount(e) if is_telling(e) else 0.0
    if is_telling(e) and p["wind"]:
        return p["wind"], fill
    if e.kind in BOSS_KINDS:
        a, b = p["sprites"]
        return (a if int(t * 5) % 2 else b), fill
    if e.kind == "flyer":
        up = math.sin(t * 5 + e.phase) > 0
        name = p["sprites"][0] if up else p["sprites"][1]
        return name, fill
    # Ground kinds step their loop with distance (pinned against a wall the
    # cycle stops, like the hero's run cycle) and speed up when enraged.
    rate = 4
    if e.kind == "charger":
        rate = 12 if getattr(e, "raging", False) else 4
    elif e.kind == "sentry":
        rate = 2
    elif e.kind == "spiker":
        rate = 5
    elif e.kind == "walker":
        rate = 6
    a, b = p["sprites"]
    return (a if int(t * rate + e.x) % 2 else b), fill
