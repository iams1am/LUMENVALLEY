"""Progress and grade rules shared by every screen.

These are deliberately pure functions: the level-select preview, the results
screen, the terminal renderer and the tests must agree on what "GOLD" means.
Nothing here imports pygame or touches a save store.
"""

MEDALS = ("GOLD", "SILVER", "BRONZE")


def first_uncleared(cleared, unlocked):
    """Index of the first uncleared level the player may enter.

    Returns 0 when everything is clear (a fresh lap) and clamps a damaged save
    to a valid level index.
    """
    if not cleared:
        return 0
    last = max(0, min(len(cleared) - 1, int(unlocked)))
    for i in range(last + 1):
        if not cleared[i]:
            return i
    return 0


def has_progress(hiscore, cleared, unlocked):
    """True when CONTINUE has somewhere to go."""
    return bool(hiscore) or int(unlocked) > 0 or any(bool(v) for v in cleared)


def medal_for(time, par):
    """A medal from a completion time, or ``""`` when there is no record.

    The cutoffs come from :func:`medal_cutoffs` so the level preview that
    prints "GOLD <= 12.4" cannot drift from the rule that awards the medal.
    """
    if time is None or par is None:
        return ""
    cutoffs = medal_cutoffs(par)
    if cutoffs is None:
        return ""
    try:
        time = float(time)
    except (TypeError, ValueError):
        return ""
    gold, silver, bronze = cutoffs
    if time <= gold:
        return "GOLD"
    if time <= silver:
        return "SILVER"
    if time <= bronze:
        return "BRONZE"
    return "CLEARED"


def medal_cutoffs(par):
    """Return ``(gold, silver, bronze)`` seconds, or ``None`` without a par."""
    try:
        par = float(par)
    except (TypeError, ValueError):
        return None
    if par <= 0:
        return None
    return par * 0.6, par * 0.8, par


def pace_line(par, fmt):
    """The "GOLD <= .. SILVER <= .. BRONZE <= .." preview line.

    ``fmt`` is the caller's clock formatter, so the terminal grid and the
    pygame HUD print the same numbers in their own typography.
    """
    cutoffs = medal_cutoffs(par)
    if cutoffs is None:
        return "PACE  --"
    return "PACE  GOLD <= %s   SILVER <= %s   BRONZE <= %s" % tuple(
        fmt(value) for value in cutoffs)


def medal_counts(best_times, pars):
    """Count earned medals without inventing a record for unplayed levels."""
    counts = {medal: 0 for medal in MEDALS}
    for time, par in zip(best_times, pars):
        medal = medal_for(time, par)
        if medal in counts:
            counts[medal] += 1
    return counts


def grade_for(time, par, deaths, coins, coins_total, gems, gems_total):
    """Letter grade for the results screen.

    Time sets the baseline; a death-free, fully collected run of gold-medal
    pace earns S.  A missed collectible or a death cannot reduce the time
    medal below its base grade, so the screen never looks arbitrary.
    """
    medal = medal_for(time, par)
    base = {"GOLD": "A", "SILVER": "B", "BRONZE": "C"}.get(medal, "D")
    perfect = (int(deaths) == 0 and int(coins) >= int(coins_total)
               and int(gems) >= int(gems_total))
    if medal == "GOLD" and perfect:
        return "S"
    return base


# ------------------------------------------------------------- mastery --
#: Optional per-level objectives.  Every requirement is checkable from the
#: completion result; none of them can unlock a level or block CONTINUE.
CHALLENGES = {
    "untouched": ("UNTOUCHED", "CLEAR WITHOUT DYING"),
    "gems": ("ALL GEMS", "FIND EVERY GEM"),
    "pace": ("GOLD PACE", "BEAT THE GOLD-MEDAL TIME"),
    "high": ("HIGH GEM", "COLLECT THE GEM HIGHEST IN THE LEVEL"),
    "chain2": ("PRISM PAIR", "CHAIN 2 RINGS IN ONE AIRTIME"),
    "chain3": ("PRISM TRIO", "CHAIN 3 RINGS IN ONE AIRTIME"),
}


def challenge_ids(level):
    """The optional challenge ids authored by *level*'s content.

    Every level gets the three result-based objectives.  The "high gem" is the
    gem drawn highest in the grid (smallest row), which is the one objective
    the authored content already supports without inventing a hidden room;
    it is labelled for what it is, not as a secret.  Resonance levels add a
    movement objective sized to their authored ring line: two rings for the
    cavern chain, three for the citadel.
    """
    ids = ["untouched", "gems", "pace"]
    if getattr(level, "high_gem", None):
        ids.append("high")
    nodes = len(getattr(level, "resonance", ()) or ())
    if nodes >= 3:
        ids.append("chain3")
    elif nodes >= 2:
        ids.append("chain2")
    return tuple(ids)


def evaluate_challenges(time, par, deaths, gems, gems_total,
                        chain=0, high_gem=False):
    """All challenge ids a completed run satisfies.

    The caller intersects this with ``challenge_ids(level)``.  Thresholds are
    the same public medal cutoffs shown on the level preview: gold is 60% of
    par.  Missing data never raises and never invents a completion.
    """
    earned = set()
    if int(deaths) == 0:
        earned.add("untouched")
    if int(gems_total) > 0 and int(gems) >= int(gems_total):
        earned.add("gems")
    if medal_for(time, par) == "GOLD":
        earned.add("pace")
    if high_gem:
        earned.add("high")
    if int(chain) >= 2:
        earned.add("chain2")
    if int(chain) >= 3:
        earned.add("chain3")
    return earned


# --------------------------------------------------------- achievements --
#: Deliberately small: one honest milestone per system, not a badge wall.
ACHIEVEMENTS = (
    ("first_steps", "FIRST STEPS", "CLEAR ANY LEVEL"),
    ("crystal_clear", "CRYSTAL CLEAR", "FIND EVERY GEM IN A LEVEL"),
    ("treasure_hunter", "TREASURE HUNTER", "FIND EVERY COIN IN A LEVEL"),
    ("untouchable", "UNTOUCHABLE", "CLEAR A LEVEL WITHOUT DYING"),
    ("gold_standard", "GOLD STANDARD", "EARN A GOLD MEDAL"),
    ("prism_adept", "PRISM ADEPT", "CHAIN 3 RINGS IN ONE AIRTIME"),
    ("high_ground", "HIGH GROUND", "COLLECT A LEVEL'S HIGHEST GEM"),
    ("world_traveller", "WORLD TRAVELLER", "CLEAR ALL SIX LEVELS"),
)
ACHIEVEMENT_INFO = {aid: (label, detail)
                    for aid, label, detail in ACHIEVEMENTS}
ACHIEVEMENT_ORDER = {aid: i for i, (aid, _label, _detail) in
                     enumerate(ACHIEVEMENTS)}
ACHIEVEMENT_IDS = frozenset(ACHIEVEMENT_ORDER)


def earned_achievements(rows, challenge_marks=(), high_gems=()):
    """Derive achievements from persisted records.

    ``rows`` is one mapping per campaign level with ``cleared``, ``best``,
    ``coins``, ``gems``, ``coins_total``, ``gems_total`` and ``par``.  Deriving
    the set means an old save is credited for work it can prove (clears,
    medals, collections) without guessing at runs that left no record.
    """
    earned = set()
    if any(row.get("cleared") for row in rows):
        earned.add("first_steps")
    if rows and all(row.get("cleared") for row in rows):
        earned.add("world_traveller")
    for row in rows:
        if not row.get("cleared"):
            continue
        if (int(row.get("gems") or 0) >= int(row.get("gems_total") or 0) > 0):
            earned.add("crystal_clear")
        if (int(row.get("coins") or 0)
                >= int(row.get("coins_total") or 0) > 0):
            earned.add("treasure_hunter")
        if medal_for(row.get("best"), row.get("par")) == "GOLD":
            earned.add("gold_standard")
    for marks in (challenge_marks or {}).values():
        if "untouched" in marks:
            earned.add("untouchable")
        if "chain3" in marks:
            earned.add("prism_adept")
    # Level indices: set membership, never truthiness, because level 0 is a
    # real discovery and ``any(bool(v) for v in {0})`` is False.
    if high_gems:
        earned.add("high_ground")
    return earned


def threat_count(level):
    """Danger patches plus enemies, rather than an invented star rating.

    Adjacent hazard tiles form one patch: a wide lava pool is one threat, not
    thirty.  Two levels with the same count present the same number of authored
    danger sources.
    """
    hazard = {(x, y) for y, row in enumerate(level.grid)
              for x, cell in enumerate(row) if cell in "^~"}
    patches = 0
    while hazard:
        patches += 1
        stack = [hazard.pop()]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if (nx, ny) in hazard:
                    hazard.remove((nx, ny))
                    stack.append((nx, ny))
    return patches + len(level.enemies)
