"""Headless simulation: states, physics, combat, editor and progression.

This module intentionally imports no renderer, terminal, audio backend or
filesystem adapter.  ``Game`` talks to an injected SaveStore and audio sink;
the defaults are an in-memory store and a silent sink, so it runs headless.
"""

import random
import sys

from lumenvalley.constants import (
    ED_THEMES, ED_TOOLS, ED_W, GAME_H, GAME_OY,
    K_DOWN, K_ENTER, K_ESC, K_JUMP, K_LEFT, K_RIGHT, K_UP,
    MAX_CUSTOMS, PAR_TIMES, TEACH_CUES, THEMES, VIEW_W,
)
from lumenvalley.ghosts import (decode_ghost, encode_ghost, is_future_ghost,
                            sample_trace)
from lumenvalley.motion import Camera, arena_focus, ember_chance
from lumenvalley.progress import (
    ACHIEVEMENT_ORDER, ACHIEVEMENT_INFO, ACHIEVEMENTS, CHALLENGES,
    challenge_ids, earned_achievements, evaluate_challenges, first_uncleared,
    grade_for, has_progress, medal_counts, medal_for,
)
from lumenvalley.resonance import ResonanceField, chain_points
from lumenvalley.store import SAVE_SCHEMA_VERSION, MemoryStore
from lumenvalley.ui import HINTS, Item, Menu
from entities import (Player, make_enemy, Particles, Mover,
                      PLAYER_H, PLAYER_W, STOMP_BOUNCE, SPRING_V)
from levels import load_levels, Level

# Persisted settings.  Volumes are steps of 10; the rest are small enums so a
# damaged save can be clamped without a schema migration.
DEFAULT_SETTINGS = {"master": 7, "music": 6, "sfx": 8,
                    "shake": 1, "motion": 1, "quality": 1}
SETTING_RANGES = {"master": (0, 10), "music": (0, 10), "sfx": (0, 10),
                  "shake": (0, 2), "motion": (0, 1), "quality": (0, 1)}
SHAKE_LABELS = ("OFF", "NORMAL", "STRONG")

#: Columns available for the results note inside the terminal's 52-column
#: completion panel (two of those columns are the border).
RECORD_NOTE_MAX = 50


class _SilentAudio:
    """No-op audio sink used when the host does not provide one."""

    def play(self, name):
        pass

    def set_enabled(self, on):
        pass

    def set_volumes(self, master, music, sfx):
        pass


def _fmt_time(t):
    return "%02d:%02d" % (int(t) // 60, int(t) % 60)


def _overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Game:
    TITLE, HELP, LEVELSEL = "title", "help", "levelsel"
    EDITOR, SETTINGS, CREDITS = "editor", "settings", "credits"
    INTRO, PLAY, PAUSE = "intro", "play", "pause"
    DEAD, COMPLETE, GAMEOVER, VICTORY = "dead", "complete", "gameover", "victory"
    RECORDS = "records"
    CONFIRM = "confirm"

    def __init__(self, seed=None, store=None, audio=None, legacy_rng=False):
        self.levels = load_levels()
        self.store = store or MemoryStore()
        self.audio = audio or _SilentAudio()
        if seed is None:
            self.gameplay_rng = random.Random()
            self.cosmetic_rng = random.Random()
        else:
            self.gameplay_rng = random.Random(seed)
            self.cosmetic_rng = random.Random(seed ^ 0x5EED5EED)
        # Test/replay compatibility only: the original build used one global
        # random stream for gameplay and cosmetics.  Sharing one stream here
        # reproduces pre-Phase-2 golden traces exactly.  Normal play and all
        # new deterministic tests use the separated streams above.
        if legacy_rng:
            self.cosmetic_rng = self.gameplay_rng
        self.legacy_rng = bool(legacy_rng)
        self.parts = Particles(self.cosmetic_rng)
        self.fx = Particles(self.cosmetic_rng)
        self.muted = False
        self.gate_t = 0.0
        self.save_error = None
        self.save_warning = None
        # ``_load_save`` returns the complete persisted state as a dict; every
        # key is an attribute name, so there is no second list to keep in sync.
        for key, value in self._load_save().items():
            setattr(self, key, value)
        # Whether the host's accessibility preference may seed reduced motion.
        # Set by the browser front-end after construction; never persisted.
        self.system_reduced_motion = False
        self._migrate_progress()
        self.hard = False
        self._apply_audio()
        self._apply_effects()
        self._ghost_rows = None
        self.sel_lvl = 0
        self.new_best_time = False
        self.t = 0.0
        self.camera = Camera()
        self.state = self.TITLE
        self.trial_mode = False
        self.level_title = "SELECT LEVEL"
        self.help_back = self.TITLE
        self.deaths = 0
        self.deaths_level = 0
        self.medal = ""
        self.grade = ""
        self.next_name = ""
        self.high_gem_found = False
        self.chain_best = 0
        self.new_challenges = []
        self.new_achievements = []
        self.record_note = ""
        self._preview_cache = {}
        # Which platform capabilities the current front-end can honour.  The
        # web shell has its own fullscreen button, so it turns that row off
        # rather than showing a switch that would do nothing.
        self.ui_caps = {"fullscreen": True}
        self.screen_mode = "window"
        self.fullscreen_requests = 0
        self.settings_back = self.TITLE
        self.levelsel_back = self.TITLE
        self.title_menu = Menu("LUMENVALLEY", footer=HINTS["title"], rows=6)
        self.pause_menu = Menu("PAUSED", footer=HINTS["pause"], rows=7)
        self.settings_menu = Menu("SETTINGS", footer=HINTS["settings"], rows=7)
        self.level_menu = Menu("SELECT LEVEL", footer=HINTS["levels"], rows=7)
        self.results_menu = Menu("LEVEL CLEAR", footer=HINTS["results"], rows=4)
        self.records_menu = Menu("RECORDS", footer=HINTS["records"], rows=6)
        self.confirm_menu = Menu("ARE YOU SURE?", footer=HINTS["confirm"],
                                 rows=3)
        self.menu = self.title_menu
        self.sel = 0
        self.pause_sel = 0
        self.pending_action = None
        self.pending_prompt = ""
        self.confirm_back = self.TITLE
        self.credits_back = self.TITLE
        self.new_best_run = False
        self.new_hard_best = False
        self.prev_best_time = None
        self.save_note = ""
        self.trial_mode = False
        self.run_from_start = True
        self._score_seen = 0
        self.toast_msg = None
        self.new_record = False
        self.running = True
        self.score = 0
        self.lives = 3
        self.coins_n = 0
        self.gems_n = 0
        self.next_life = 3000
        self.run_time = 0.0
        self._amb_t = 0.0
        self.test_mode = False
        self.playing_custom = False
        self.custom_idx = None
        self.customs = self._load_customs()
        self.ed_grid = None
        self.ed_cam = 0
        self.ed_cx = 3
        self.ed_cy = 16
        self.ed_tool = 0
        self.ed_theme_i = 0
        self.ed_slot = None
        self._refresh_menus()

    # ------------------------------------------------------------ save --
    def _load_save(self):
        best = [None] * len(self.levels)
        best_coins = [0] * len(self.levels)
        best_gems = [0] * len(self.levels)
        settings = dict(DEFAULT_SETTINGS)
        explicit = set()
        hi = 0
        unlocked = 0
        cleared = [False] * len(self.levels)
        best_total = None
        hard_unlocked = False
        hard_best = None
        muted = False
        ghosts = {}
        ghost_quarantine = {}
        challenges = {}
        high_gems = set()
        achievements = set()

        # Read once.  The old implementation parsed it twice, which could mix
        # two different generations of the save.
        data = self.store.load_progress()
        if isinstance(data, dict):
            version = _as_int(data.get("schema_version",
                                       SAVE_SCHEMA_VERSION), 0)
            if version > SAVE_SCHEMA_VERSION:
                self.save_warning = (
                    "save schema %d is newer than supported %d"
                    % (version, SAVE_SCHEMA_VERSION))
                print("lumenvalley: " + self.save_warning, file=sys.stderr)
            hi = max(0, _as_int(data.get("hiscore", 0), 0))
            raw = data.get("best_times") or []
            if isinstance(raw, list):
                for i, v in enumerate(raw[:len(best)]):
                    best[i] = _as_float(v)
            unlocked = max(0, min(len(best) - 1,
                                  _as_int(data.get("unlocked", 0), 0)))
            cl = data.get("cleared") or []
            if isinstance(cl, list):
                for i, v in enumerate(cl[:len(cleared)]):
                    cleared[i] = bool(v)
            best_total = _as_float(data.get("best_total"))
            muted = bool(data.get("muted", False))
            hard_unlocked = bool(data.get("hard_unlocked", False))
            hard_best = _as_float(data.get("hard_best"))
            for key, target in (("best_coins", best_coins),
                               ("best_gems", best_gems)):
                raw_best = data.get(key) or []
                if isinstance(raw_best, list):
                    for i, v in enumerate(raw_best[:len(target)]):
                        target[i] = max(0, _as_int(v, 0))
            raw_settings = data.get("settings")
            if isinstance(raw_settings, dict):
                for key, (lo, high) in SETTING_RANGES.items():
                    if key in raw_settings:
                        settings[key] = max(lo, min(
                            high, _as_int(raw_settings.get(key),
                                          DEFAULT_SETTINGS[key])))
            # Which settings the player actually chose, so a host preference
            # can seed a default without overriding a decision.  Saves from
            # before this list existed encode one choice unambiguously:
            # reduced motion already ON means the player asked for it; OFF is
            # the default, so it stays open to the system preference.
            raw_explicit = data.get("settings_explicit")
            if isinstance(raw_explicit, list):
                explicit = {k for k in raw_explicit if k in SETTING_RANGES}
            else:
                explicit = {"motion"} if settings["motion"] == 0 else set()
            raw_g = data.get("ghosts") or {}
            if isinstance(raw_g, dict):
                for k, v in raw_g.items():
                    try:
                        idx = int(k)
                    except (TypeError, ValueError):
                        continue
                    if idx < 0 or idx >= len(self.levels):
                        continue
                    ghost = decode_ghost(v)
                    if ghost is None:
                        if is_future_ghost(v):
                            # Do not reinterpret a newer coordinate format;
                            # keep the exact payload so a later save cannot
                            # silently delete it.
                            ghost_quarantine[idx] = v
                            if self.save_warning is None:
                                self.save_warning = (
                                    "ghost %d uses a newer format" % idx)
                        continue
                    ghosts[idx] = ghost["frames"]
            raw_ch = data.get("challenges") or {}
            if isinstance(raw_ch, dict):
                for k, v in raw_ch.items():
                    try:
                        idx = int(k)
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= idx < len(self.levels) or not isinstance(v,
                                                                        list):
                        continue
                    valid = {cid for cid in v if cid in CHALLENGES}
                    if valid:
                        challenges[idx] = valid
            raw_high = data.get("high_gems") or []
            if isinstance(raw_high, list):
                for value in raw_high:
                    idx = _as_int(value, -1)
                    if 0 <= idx < len(self.levels):
                        high_gems.add(idx)
            raw_ach = data.get("achievements") or []
            if isinstance(raw_ach, list):
                achievements = {aid for aid in raw_ach
                                if aid in ACHIEVEMENT_INFO}
        elif isinstance(data, (int, float)) and not isinstance(data, bool):
            # Very old save: a bare high score.
            hi = max(0, int(data))
        return {
            "hiscore": hi,
            "best_times": best,
            "unlocked": unlocked,
            "cleared": cleared,
            "best_total": best_total,
            "ghosts": ghosts,
            "ghost_quarantine": ghost_quarantine,
            "muted": muted,
            "hard_unlocked": hard_unlocked,
            "hard_best": hard_best,
            "best_coins": best_coins,
            "best_gems": best_gems,
            "settings": settings,
            "settings_explicit": explicit,
            "challenges": challenges,
            "high_gems": high_gems,
            "achievements": achievements,
        }

    def _level_stat_rows(self):
        """Per-level records in the shape progress.earned_achievements wants."""
        rows = []
        for i, lvl in enumerate(self.levels):
            rows.append({
                "cleared": bool(self.cleared[i]),
                "best": self.best_times[i],
                "coins": self.best_coins[i],
                "gems": self.best_gems[i],
                "coins_total": len(lvl.coins),
                "gems_total": len(lvl.gems),
                "par": PAR_TIMES[i],
            })
        return rows

    def _migrate_progress(self):
        """Credit v1 saves for objectives their records can already prove.

        No-death and high-gem runs left no historical trace, so they are not
        invented.  Collection and gold-pace marks are exact and safe to derive.
        Achievements are then unioned with the explicit saved list so a later
        definition change cannot take an earned milestone away.
        """
        for i, lvl in enumerate(self.levels):
            if not self.cleared[i]:
                continue
            marks = set(self.challenges.get(i, ()))
            if len(lvl.gems) and self.best_gems[i] >= len(lvl.gems):
                marks.add("gems")
            if medal_for(self.best_times[i], PAR_TIMES[i]) == "GOLD":
                marks.add("pace")
            if marks:
                self.challenges[i] = marks
        self.achievements |= earned_achievements(
            self._level_stat_rows(), self.challenges, self.high_gems)

    def _sync_achievements(self):
        """Add newly provable achievements and return their ids."""
        before = set(self.achievements)
        self.achievements |= earned_achievements(
            self._level_stat_rows(), self.challenges, self.high_gems)
        return self.achievements - before

    def _achievement_sort(self, ids):
        return sorted(ids, key=lambda aid: ACHIEVEMENT_ORDER.get(aid, 999))

    def _ghost_payloads(self):
        """Versioned ghost records, plus any newer-format entries we preserve."""
        out = {}
        for idx in sorted(self.ghosts):
            time_value = (self.best_times[idx]
                          if 0 <= idx < len(self.best_times) else None)
            out[str(idx)] = encode_ghost(self.ghosts[idx],
                                         time=time_value, level=idx)
        for idx in sorted(self.ghost_quarantine):
            key = str(idx)
            if key not in out:
                out[key] = self.ghost_quarantine[idx]
        return out

    def _save_score(self):
        if self.score > self.hiscore:
            self.hiscore = self.score
            self.new_record = True
        self._sync_achievements()
        payload = {"schema_version": self.store.schema_version,
                   "hiscore": self.hiscore,
                   "best_times": self.best_times,
                   "unlocked": self.unlocked,
                   "cleared": self.cleared,
                   "best_total": self.best_total,
                   "ghosts": self._ghost_payloads(),
                   "muted": self.muted,
                   "hard_unlocked": self.hard_unlocked,
                   "hard_best": self.hard_best,
                   "best_coins": self.best_coins,
                   "best_gems": self.best_gems,
                   "challenges": {str(k): sorted(v)
                                  for k, v in sorted(self.challenges.items())
                                  if v},
                   "high_gems": sorted(self.high_gems),
                   "achievements": sorted(self.achievements),
                   "settings": dict(self.settings),
                   "settings_explicit": sorted(self.settings_explicit)}
        try:
            ok = self.store.save_progress(payload)
            if ok is False:
                raise OSError(getattr(self.store, "save_error", None)
                              or "save rejected")
        except (OSError, TypeError, ValueError) as exc:
            self.save_error = str(exc)
            print("lumenvalley: could not save progress: %s" % exc,
                  file=sys.stderr)
            self.toast("SAVE FAILED - CHECK DISK", 196, 3.0)
            return False
        self.save_error = None
        return True

    def _apply_audio(self):
        """Push persisted volume steps into the host audio backend."""
        fn = getattr(self.audio, "set_volumes", None)
        if fn is not None:
            fn(self.settings["master"], self.settings["music"],
               self.settings["sfx"])
        self.audio.set_enabled(not self.muted)

    def _apply_effects(self):
        """Push quality/reduced-motion into the particle budgets."""
        for pool in (self.parts, self.fx):
            pool.set_budget(self.settings.get("quality", 1),
                            self.settings.get("motion", 1))

    def apply_system_preferences(self, reduce_motion=False):
        """Seed reduced motion from the host's accessibility preference.

        The browser front-end calls this once at startup with the value of
        ``prefers-reduced-motion``.  It is a default, not an override: a player
        who has chosen a motion setting keeps it, and touching the row records
        that choice.  Returns True when the setting actually changed.
        """
        self.system_reduced_motion = bool(reduce_motion)
        if not self.system_reduced_motion:
            return False
        if "motion" in self.settings_explicit:
            return False
        if self.settings.get("motion", 1) == 0:
            return False
        self.settings["motion"] = 0
        self._apply_effects()
        self._refresh_menus()
        return True

    # ------------------------------------------------------- game flow --
    def new_game(self, start_level=0, hard=False, trial=False):
        self.hard = hard
        self.test_mode = False
        self.playing_custom = False
        self.custom_idx = None
        self.trial_mode = trial
        self.run_from_start = (start_level == 0)
        self.score = 0
        self.lives = 1 if hard else 3
        self.coins_n = 0
        self.gems_n = 0
        self.next_life = 3000
        self.new_record = False
        self.deaths = 0
        self.deaths_level = 0
        self.medal = ""
        self.grade = ""
        self.next_name = ""
        self.run_time = 0.0
        self.load_level(start_level)

    def load_level(self, idx):
        self.load_level_obj(self.levels[idx], idx)

    def load_level_obj(self, lvl, idx):
        self.li = idx
        self.lvl = lvl
        self.theme = THEMES[lvl.theme]
        self.player = Player(*self.lvl.start)
        self.respawn_pt = self.lvl.start
        self.enemies = [make_enemy(k, x, y, rng=self.gameplay_rng)
                        for k, x, y in self.lvl.enemies]
        self.movers = [Mover(x, y, a) for x, y, a
                       in getattr(self.lvl, "movers", [])]
        if getattr(self, "hard", False):
            for e in self.enemies:
                if hasattr(e, "SPEED"):
                    e.SPEED = round(e.SPEED * 1.2, 2)
                if hasattr(e, "RUSH"):
                    e.RUSH = round(e.RUSH * 1.2, 2)
        self.powers = set(getattr(self.lvl, "powerups", []))
        self.player_riding = None
        self.coins = set(self.lvl.coins)
        self.gems = set(self.lvl.gems)
        self.resonance = ResonanceField(
            getattr(self.lvl, "resonance", ()))
        self.teach_cues = list(TEACH_CUES.get(idx, ()))
        self.cp_on = set()
        self.spring_anim = {}
        self.stomp_chain = 0
        self.shots = []
        self.camera.reset(0.0)
        self.shake = 0.0
        self.level_time = 0.0
        self.level_coins_total = len(self.coins)
        self.level_gems_total = len(self.gems)
        self.deaths_level = 0
        self.medal = ""
        self.grade = ""
        self.next_name = ""
        self.new_best_time = False
        self.prev_best_time = None
        self.save_note = ""
        self._score_seen = 0
        self.lava_tiles = [
            (x, y) for y in range(self.lvl.h)
            for x in range(self.lvl.w) if self.lvl.grid[y][x] == "~"
        ]
        self.ghost = self.ghosts.get(idx) if idx is not None else None
        self.trace = []
        self.high_gem_found = False
        self.chain_best = 0
        self.new_challenges = []
        self.new_achievements = []
        self.record_note = ""
        self.parts.items = []
        self.stats = None
        self.state = self.INTRO
        self.st_t = 1.8

    def respawn(self):
        self.player.reset(*self.respawn_pt)
        self.player.invuln = 1.4
        self.player.respawn_t = 0.35
        self.player.celebrate = False
        for e in self.enemies:
            e.reset()
        for m in self.movers:
            m.reset()
        self.player_riding = None
        self.shots = []
        self.stomp_chain = 0
        self.resonance.rearm()
        self.parts.items = []
        self.state = self.PLAY
        self.toast("GO!", 226)

    def die(self, reason="enemy"):
        if reason == "enemy" and self.player.invuln > 0:
            return
        if self.state != self.PLAY:
            return
        self.player.alive = False
        self.player.hurt_t = 0.18
        self.death_reason = reason
        self.deaths += 1
        self.deaths_level += 1
        self.state = self.DEAD
        self.st_t = 1.15
        self.shake = 0.5
        self.audio.play("death")
        flavor = {"fell": ("THE VOID CLAIMS YOU", 141),
                  "spikes": ("SKEWERED!", 196),
                  "lava": ("MELTED!", 208),
                  "enemy": ("CLIPPED!", 203)}
        msg, col = flavor.get(reason, ("OUCH!", 252))
        self.toast(msg, col, 2.2)
        p = self.player
        self.parts.death(p.x + PLAYER_W / 2, p.y)

    def complete_level(self):
        par = PAR_TIMES[self.li] if self.li is not None else 90
        time_bonus = max(0, int((par - self.level_time)) * 10)
        level_bonus = 1000
        self.prev_best_time = None
        if self.li is not None:
            prev_best = self.best_times[self.li]
            self.prev_best_time = prev_best
            self.new_best_time = (prev_best is None
                                  or self.level_time < prev_best)
            if self.new_best_time:
                self.best_times[self.li] = round(self.level_time, 2)
                if self.trace:
                    frames = sample_trace(self.trace)
                    if frames:
                        self.ghosts[self.li] = frames
                        self.ghost_quarantine.pop(self.li, None)
            self.cleared[self.li] = True
            self.unlocked = max(self.unlocked, min(self.li + 1,
                                                   len(self.levels) - 1))
            self.best_coins[self.li] = max(self.best_coins[self.li],
                                           self.coins_n)
            self.best_gems[self.li] = max(self.best_gems[self.li],
                                          self.gems_n)
            self.next_name = (self.levels[self.li + 1].name
                              if self.li + 1 < len(self.levels) else "")
        else:
            self.new_best_time = False
            self.next_name = ""
        self.add_score(time_bonus + level_bonus)
        self.stats = dict(time=self.level_time, time_bonus=time_bonus,
                          coins=self.coins_n, gems=self.gems_n,
                          level_bonus=level_bonus)
        self.medal = (medal_for(self.level_time, par)
                      if self.li is not None else "")
        self.grade = grade_for(self.level_time, par, self.deaths_level,
                               self.coins_n, self.level_coins_total,
                               self.gems_n, self.level_gems_total)
        self.new_challenges = []
        self.new_achievements = []
        if self.li is not None and not self.test_mode:
            ids = set(challenge_ids(self.levels[self.li]))
            earned = evaluate_challenges(
                self.level_time, par, self.deaths_level,
                self.gems_n, self.level_gems_total,
                chain=self.chain_best, high_gem=self.high_gem_found) & ids
            before = set(self.challenges.get(self.li, ()))
            self.new_challenges = [cid for cid in CHALLENGES
                                   if cid in earned and cid not in before]
            if earned:
                self.challenges[self.li] = before | earned
            # The pickup already recorded this (and saved it); repeating it here
            # keeps the achievement honest if the pickup path was skipped.
            if self.high_gem_found:
                self.high_gems.add(self.li)
        self.new_achievements = self._achievement_sort(
            self._sync_achievements())
        self.record_note = self._record_note_text()
        saved = self._save_score()
        self.save_note = "AUTOSAVED" if saved else "SAVE FAILED"
        self.audio.play("fanfare")
        self._build_results_menu()
        self.player.celebrate = True
        self.state = self.COMPLETE
        self.st_t = 0.0
        self.fw_t = 0.0
        self._score_seen = 0

    def _record_note_text(self):
        """One concise results line for the best new milestone this run.

        Bounded here rather than sliced by each renderer: the terminal panel is
        52 columns and the pygame note is drawn at one size, so a note that
        only fits one of them is a bug in this method, not in the draw code.
        """
        bits = []
        if self.new_achievements:
            first = ACHIEVEMENT_INFO[self.new_achievements[0]][0]
            extra = len(self.new_achievements) - 1
            bits.append("ACH: " + first + (" +%d" % extra if extra else ""))
        if self.new_challenges:
            bits.append("%d NEW CHALLENGE%s"
                        % (len(self.new_challenges),
                           "" if len(self.new_challenges) == 1 else "S"))
        note = "  ·  ".join(bits)
        if len(note) > RECORD_NOTE_MAX and bits:
            # Drop the label before dropping a milestone: "FIRST STEPS" is
            # more useful than "ACHIEVEMENT: " when only one fits.
            bits = [bit.replace("ACHIEVEMENT: ", "") for bit in bits]
            note = "  ·  ".join(bits)
        if len(note) > RECORD_NOTE_MAX and bits:
            note = bits[0]
        return note[:RECORD_NOTE_MAX]

    def _build_results_menu(self):
        """Rows for the results screen, tailored to campaign/custom/test."""
        items = [Item("RETRY LEVEL", "retry")]
        if self.test_mode:
            items.append(Item("RETURN TO EDITOR", "results_editor"))
        elif self.playing_custom:
            items.append(Item("RETURN TO TITLE", "results_title"))
            items.append(Item("LEVEL SELECT", "levels"))
        else:
            if self.li is not None and self.li + 1 < len(self.levels):
                # The upcoming level is named above the menu instead of in the
                # row: "NEXT LEVEL" plus a level name is wider than the row and
                # used to overlap its own label.
                items.append(Item("NEXT LEVEL", "next"))
            else:
                items.append(Item("FINISH CAMPAIGN", "finish"))
            items.append(Item("LEVEL SELECT", "levels"))
        self.results_menu.set_items(items, index=min(1, len(items) - 1))

    def add_score(self, n):
        if getattr(self, "hard", False):
            n *= 2
        self.score += n
        if self.score >= self.next_life:
            self.lives = min(9, self.lives + 1)
            self.next_life += 3000
            self.audio.play("oneup")
            self.toast("1UP!", 46)

    def _discover_high_gem(self):
        """Record the first high-gem pickup in this run.

        The achievement should not wait for the flag: the gem has been reached
        even if the player dies afterwards.  Saving here also makes the
        discovery durable across crashes.
        """
        if self.high_gem_found:
            return
        self.high_gem_found = True
        if self.li is None or self.test_mode or self.playing_custom:
            return
        if self.li in self.high_gems:
            self.toast("HIGH GEM FOUND!", 51, 2.0)
            return
        self.high_gems.add(self.li)
        self._sync_achievements()
        saved = self._save_score()
        self.toast("HIGH GEM FOUND!" if saved
                   else "HIGH GEM - SAVE FAILED", 51, 2.2)

    def toast(self, msg, color=252, dur=1.6):
        self.toast_msg = (msg, color, self.t + dur)

    # ----------------------------------------------------------- input --
    @staticmethod
    def _inputs(keys):
        return dict(
            left=K_LEFT in keys or "a" in keys,
            right=K_RIGHT in keys or "d" in keys,
            down=K_DOWN in keys or "s" in keys,
            jump=K_JUMP in keys or "w" in keys or K_UP in keys,
            dash="x" in keys or "l" in keys,
        )

    # ------------------------------------------------------------ editor --
    def _load_customs(self):
        data = self.store.load_customs()
        if not isinstance(data, dict):
            return []
        out = []
        levels = data.get("levels", [])
        if not isinstance(levels, list):
            return []
        for lv in levels[:MAX_CUSTOMS]:
            if not isinstance(lv, dict):
                continue
            rows = [str(r) for r in lv.get("rows", [])]
            if len(rows) == 20:
                out.append(dict(name=str(lv.get("name", "CUSTOM"))[:18],
                                theme=str(lv.get("theme", "day")),
                                rows=rows))
        return out

    def _save_customs(self):
        try:
            self.store.save_customs(self.customs)
        except (OSError, TypeError, ValueError) as exc:
            self.save_error = str(exc)
            print("lumenvalley: could not save custom levels: %s" % exc,
                  file=sys.stderr)
            self.toast("CUSTOM SAVE FAILED - CHECK DISK", 196, 3.0)
            return False
        self.save_error = None
        return True

    def _ed_init(self):
        if self.ed_grid is None:
            g = [[" "] * ED_W for _ in range(20)]
            for x in range(ED_W):
                g[18][x] = "#"
                g[19][x] = "#"
            g[16][3] = "P"
            g[15][90] = "F"
            self.ed_grid = g

    def _ed_paint(self, ch, x=None, y=None):
        cx = self.ed_cx if x is None else x
        cy = self.ed_cy if y is None else y
        if 0 <= cx < ED_W and 0 <= cy < 20:
            self.ed_grid[cy][cx] = ch

    def _ed_validate(self):
        """Return human-readable problems that block saving/test-playing."""
        flat = "".join("".join(r) for r in self.ed_grid)
        problems = []
        starts = flat.count("P")
        flags = flat.count("F")
        if starts != 1:
            problems.append("NEED EXACTLY ONE START (P)")
        if flags != 1:
            problems.append("NEED EXACTLY ONE FLAG (F)")
        return problems

    def _ed_save(self):
        problems = self._ed_validate()
        if problems:
            self.toast(problems[0], 196, 2.2)
            return False
        name = "CUSTOM-%d" % (self.ed_slot + 1 if self.ed_slot is not None
                              else len(self.customs) + 1)
        entry = dict(name=name, theme=ED_THEMES[self.ed_theme_i],
                     rows=["".join(r) for r in self.ed_grid])
        if self.ed_slot is None or self.ed_slot >= len(self.customs):
            if len(self.customs) >= MAX_CUSTOMS:
                self.toast("LIBRARY FULL - DELETE A LEVEL FIRST", 196)
                return False
            self.ed_slot = len(self.customs)
            self.customs.append(entry)
        else:
            self.customs[self.ed_slot] = entry
        if not self._save_customs():
            return False
        self.audio.play("check")
        self.toast("SAVED AS " + name, 46)
        return True

    def _ed_testplay(self):
        problems = self._ed_validate()
        if problems:
            self.toast(problems[0], 196, 2.2)
            return False
        lvl = Level("TEST PLAY", ED_THEMES[self.ed_theme_i],
                    ["".join(r) for r in self.ed_grid])
        self.test_mode = True
        self.playing_custom = False
        self.custom_idx = None
        self.trial_mode = False
        self.run_from_start = False
        self.score = 0
        self.lives = 3
        self.load_level_obj(lvl, None)
        return True

    def new_game_custom(self, j):
        self.custom_idx = j
        entry = self.customs[j]
        lvl = Level(entry["name"], entry.get("theme", "day"), entry["rows"])
        self.test_mode = False
        self.playing_custom = True
        self.trial_mode = False
        self.run_from_start = False
        self.deaths = 0
        self.score = 0
        self.lives = 3
        self.coins_n = 0
        self.gems_n = 0
        self.next_life = 3000
        self.new_record = False
        self.run_time = 0.0
        self.load_level_obj(lvl, None)

    # ------------------------------------------------------------- step --
    def step(self, dt, keys, edges):
        self.t += dt
        if "m" in edges:
            self._toggle("sound")
        st = self.state
        if st == self.TITLE:
            self._step_title(dt, edges)
        elif st == self.LEVELSEL:
            self._step_levelsel(dt, edges)
        elif st == self.EDITOR:
            self._step_editor(dt, keys, edges)
        elif st == self.SETTINGS:
            self._step_settings(dt, edges)
        elif st == self.CREDITS:
            self._step_credits(dt, edges)
        elif st == self.RECORDS:
            self._step_records(dt, edges)
        elif st == self.CONFIRM:
            self._step_confirm(dt, edges)
        elif st == self.HELP:
            if edges & {K_ESC, K_ENTER}:
                self.state = self.TITLE
                self.menu = self.title_menu
        elif st == self.INTRO:
            self.parts.update(dt)
            if self.st_t <= 0 or edges & {K_JUMP, K_ENTER}:
                self.state = self.PLAY
                self.toast("GO!", 226)
            else:
                self.st_t -= dt
        elif st == self.PLAY:
            if K_ESC in edges:
                self.state = self.PAUSE
                self.pause_menu.select(0)
                self.pause_sel = 0
                self.menu = self.pause_menu
            elif "r" in edges:
                if self.test_mode:
                    lvl = Level("TEST PLAY", ED_THEMES[self.ed_theme_i],
                                ["".join(r) for r in self.ed_grid])
                    self.load_level_obj(lvl, None)
                elif self.playing_custom:
                    self.new_game_custom(self.custom_idx)
                else:
                    self.load_level(self.li)
            else:
                self._gameplay(dt, keys)
        elif st == self.PAUSE:
            if edges & {K_UP, "w"}:
                self.pause_menu.move(-1)
            if edges & {K_DOWN, "s"}:
                self.pause_menu.move(1)
            self.pause_sel = self.pause_menu.index
            if K_ESC in edges:
                self.state = self.PLAY
            elif K_ENTER in edges:
                cur = self.pause_menu.current()
                if cur is not None:
                    self._activate(cur.action)
        elif st == self.DEAD:
            self.parts.update(dt)
            self.player.hurt_t = max(0.0, self.player.hurt_t - dt)
            for e in self.enemies:
                if e.kind in ("boss", "king"):
                    e.update(dt, self.lvl, self.player.x, self.player.y,
                             self.shots)
                elif e.kind == "sentry":
                    e.update(dt, self.lvl, self.player.x, self.player.y,
                             self.shots)
                elif e.kind in ("walker", "charger"):
                    e.update(dt, self.lvl, self.player.x, self.player.y)
                else:
                    e.update(dt, self.lvl)
            for s in self.shots:
                s.update(dt, self.lvl)
            self.shots = [s for s in self.shots if s.alive]
            self.st_t -= dt
            if self.st_t <= 0:
                self.lives -= 1
                if self.lives < 0:
                    self._save_score()
                    if self.test_mode:
                        self.state = self.EDITOR
                    elif self.playing_custom:
                        self.state = self.TITLE
                    else:
                        self.state = self.GAMEOVER
                else:
                    self.respawn()
        elif st == self.COMPLETE:
            self._fireworks(dt)
            if edges & {K_UP, "w"}:
                self.results_menu.move(-1)
            if edges & {K_DOWN, "s"}:
                self.results_menu.move(1)
            if edges & {K_ENTER, K_JUMP}:
                cur = self.results_menu.current()
                if cur is not None:
                    self._activate(cur.action)
        elif st == self.GAMEOVER:
            if edges & {K_ENTER, K_JUMP}:
                self._save_score()
                self._return_to_title()
        elif st == self.VICTORY:
            self._fireworks(dt)
            if edges & {K_ENTER, K_JUMP}:
                self._return_to_title()
        return self.running

    def _step_editor(self, dt, keys, edges):
        self._ed_init()
        dx = ((1 if K_RIGHT in keys or "d" in keys else 0)
              - (1 if K_LEFT in keys or "a" in keys else 0))
        dy = ((1 if K_DOWN in keys or "s" in keys else 0)
              - (1 if K_UP in keys or "w" in keys else 0))
        self.ed_cx = max(0, min(ED_W - 1, self.ed_cx + dx * 2))
        self.ed_cy = max(0, min(19, self.ed_cy + dy))
        if self.ed_cx < self.ed_cam + 2:
            self.ed_cam = max(0, self.ed_cx - 2)
        if self.ed_cx > self.ed_cam + VIEW_W - 3:
            self.ed_cam = min(ED_W - VIEW_W, self.ed_cx - VIEW_W + 3)
        if K_JUMP in edges:
            self._ed_paint(ED_TOOLS[self.ed_tool][0])
        elif "x" in edges:
            self._ed_paint(" ")
        if "e" in edges:
            self.ed_tool = (self.ed_tool + 1) % len(ED_TOOLS)
        if "q" in edges:
            self.ed_tool = (self.ed_tool - 1) % len(ED_TOOLS)
        if "n" in edges:
            self.ed_theme_i = (self.ed_theme_i + 1) % len(ED_THEMES)
            self.toast("THEME: " + ED_THEMES[self.ed_theme_i].upper(), 245)
        if "v" in edges:
            self._ed_save()
        if K_ENTER in edges:
            self._ed_testplay()
        if K_ESC in edges:
            self.state = self.TITLE

    # ------------------------------------------------------------- menus --
    def _cleared_count(self):
        return sum(1 for value in self.cleared if value)

    def _has_records(self):
        return any(t is not None for t in self.best_times)

    def _challenge_progress(self, i):
        """``(done, total)`` for campaign level *i* in its authored set."""
        if i is None or not (0 <= i < len(self.levels)):
            return 0, 0
        ids = set(challenge_ids(self.levels[i]))
        return len(ids & set(self.challenges.get(i, ()))), len(ids)

    def _challenge_totals(self):
        total = sum(len(challenge_ids(lvl)) for lvl in self.levels)
        done = sum(self._challenge_progress(i)[0]
                   for i in range(len(self.levels)))
        return done, total

    def _record_summary(self):
        """Three transparent summary lines shared by both renderers."""
        medals = medal_counts(self.best_times, PAR_TIMES)
        done, total = self._challenge_totals()
        coins = sum(min(self.best_coins[i], len(lvl.coins))
                    for i, lvl in enumerate(self.levels))
        coin_total = sum(len(lvl.coins) for lvl in self.levels)
        gems = sum(min(self.best_gems[i], len(lvl.gems))
                   for i, lvl in enumerate(self.levels))
        gem_total = sum(len(lvl.gems) for lvl in self.levels)
        return [
            "CLEARED %d/%d   MEDALS %dG %dS %dB"
            % (self._cleared_count(), len(self.levels),
               medals["GOLD"], medals["SILVER"], medals["BRONZE"]),
            "CHALLENGES %d/%d   ACHIEVEMENTS %d/%d"
            % (done, total, len(self.achievements), len(ACHIEVEMENTS)),
            "COLLECTION %d/%d COINS   %d/%d GEMS"
            % (coins, coin_total, gems, gem_total),
        ]

    def _records_detail(self):
        cur = self.records_menu.current()
        if cur is not None and cur.action.startswith("ach:"):
            aid = cur.action.split(":", 1)[1]
            return ACHIEVEMENT_INFO.get(aid, ("", ""))[1]
        return "OPTIONAL CHALLENGES NEVER GATE LEVELS"

    def _open_records(self):
        self._sync_achievements()
        self._refresh_menus()
        self.records_menu.select(0)
        self.menu = self.records_menu
        self.state = self.RECORDS

    def _level_value(self, i):
        if i > self.unlocked:
            return "LOCKED"
        best = self.best_times[i]
        done, total = self._challenge_progress(i)
        marks = "%d/%d" % (done, total)
        if best is None:
            return "UNPLAYED  %s" % marks
        medal = medal_for(best, PAR_TIMES[i])
        if not self.cleared[i]:
            return "%s  TIME SET  %s" % (_fmt_time(best), marks)
        return "%s  %s  %s" % (_fmt_time(best), medal or "CLEARED", marks)

    def _refresh_menus(self):
        """Rebuild every menu from current progress/settings.

        Rebuilding keeps the focus index where it was (``Menu.set_items``
        clamps), so toggling a setting does not throw the player back to the
        top of the list.  The one exception is the first build: CONTINUE is
        focused when there is a save, NEW GAME when there is not.
        """
        progress = has_progress(self.hiscore, self.cleared, self.unlocked)
        if progress:
            nxt = first_uncleared(self.cleared, self.unlocked)
            cont_value = ("NEW LAP" if all(self.cleared)
                          else "LEVEL %d" % (nxt + 1))
        else:
            cont_value = "NO SAVE"
        records = sum(1 for t in self.best_times if t is not None)
        items = [Item("CONTINUE", "continue", cont_value,
                      enabled=progress),
                 Item("NEW GAME", "new"),
                 Item("LEVEL SELECT", "levels",
                      "%d/%d CLEARED" % (self._cleared_count(),
                                         len(self.levels))),
                 Item("TIME TRIALS", "trials",
                      "%d RECORD%s" % (records, "" if records == 1 else "S"),
                      enabled=records > 0),
                 Item("RECORDS", "records",
                      "%d/%d" % (len(self.achievements),
                                 len(ACHIEVEMENTS))),
                 Item("SETTINGS", "settings"),
                 Item("HOW TO PLAY", "help"),
                 Item("CREDITS", "credits"),
                 Item("LEVEL EDITOR", "editor")]
        if self.hard_unlocked:
            items.append(Item("HARD MODE", "hard", "1 LIFE"))
        items.append(Item("QUIT", "quit"))
        first_build = not self.title_menu.items
        self.title_menu.set_items(items)
        if first_build:
            self.title_menu.select_action("continue" if progress else "new")

        on_checkpoint = (getattr(self, "respawn_pt", None) is not None
                         and getattr(self, "lvl", None) is not None
                         and self.respawn_pt != self.lvl.start)
        self.pause_menu.set_items([
            Item("RESUME", "resume"),
            Item("RESTART CHECKPOINT", "checkpoint",
                 "CHECKPOINT" if on_checkpoint else "START"),
            Item("RESTART LEVEL", "restart"),
            Item("LEVEL SELECT", "ask_levels"),
            Item("SETTINGS", "settings"),
            Item("QUIT TO TITLE", "ask_quit"),
        ])

        can_fullscreen = bool(self.ui_caps.get("fullscreen", False))
        self.settings_menu.set_items([
            Item("MASTER VOLUME", "vol:master",
                 "%d/10" % self.settings["master"]),
            Item("MUSIC VOLUME", "vol:music",
                 "%d/10" % self.settings["music"]),
            Item("SFX VOLUME", "vol:sfx", "%d/10" % self.settings["sfx"]),
            Item("SOUND", "sound", "OFF" if self.muted else "ON"),
            Item("SCREEN SHAKE", "shake", SHAKE_LABELS[self.settings["shake"]]),
            Item("REDUCED MOTION", "motion", self._motion_label()),
            Item("GRAPHICS", "quality",
                 "HIGH" if self.settings["quality"] else "LOW"),
            Item("FULLSCREEN", "screen",
                 "ON" if self.screen_mode == "fullscreen" else "OFF",
                 enabled=can_fullscreen,
                 note="" if can_fullscreen else "BROWSER BUTTON"),
            Item("RESET DEFAULTS", "reset"),
            Item("BACK", "back"),
        ])

        rows = []
        for aid, label, _detail in ACHIEVEMENTS:
            rows.append(Item(label, "ach:%s" % aid,
                             "EARNED" if aid in self.achievements else "LOCKED"))
        rows.append(Item("BACK", "records_back"))
        self.records_menu.set_items(rows)

        rows = []
        if self.trial_mode:
            self.level_menu.title = "TIME TRIALS"
            self.level_menu.footer = HINTS["trials"]
            for i, lvl in enumerate(self.levels):
                best = self.best_times[i]
                if best is None:
                    continue
                medal = medal_for(best, PAR_TIMES[i])
                rows.append(Item("%d. %s" % (i + 1, lvl.name),
                                 "trial:%d" % i,
                                 "%s  %s" % (_fmt_time(best), medal)))
            if not rows:
                rows.append(Item("NO RECORDS YET", "none", "CLEAR A LEVEL",
                                 enabled=False))
        else:
            self.level_menu.title = "SELECT LEVEL"
            self.level_menu.footer = HINTS["levels"]
            for i, lvl in enumerate(self.levels):
                locked = i > self.unlocked
                rows.append(Item("%d. %s" % (i + 1, lvl.name),
                                 "level:%d" % i, self._level_value(i),
                                 enabled=not locked))
            for j, entry in enumerate(self.customs):
                rows.append(Item("%d. %s" % (len(self.levels) + j + 1,
                                              entry["name"]),
                                 "custom:%d" % j, "CUSTOM"))
        self.level_menu.set_items(rows)
        self.sel = self.title_menu.index
        self.pause_sel = self.pause_menu.index
        self.sel_lvl = self.level_menu.index

    def menu_items(self):
        """Labels of the active menu (compatibility for the terminal build)."""
        return [it.label for it in self.menu.items]

    # ``sel``/``pause_sel``/``sel_lvl`` used to be plain integers.  They now
    # proxy the menu focus so old callers (tests, scripts) cannot silently set
    # an index the renderer does not read.
    def _index_prop(attr):                       # noqa: N805 - descriptor
        def get(self):
            menu = getattr(self, attr, None)
            if menu is None:
                return 0
            return menu.index

        def set_(self, value):
            menu = getattr(self, attr, None)
            if menu is None:
                self.__dict__["_" + attr] = value
            else:
                menu.select(int(value))
        return property(get, set_)

    sel = _index_prop("title_menu")
    pause_sel = _index_prop("pause_menu")
    sel_lvl = _index_prop("level_menu")

    def _motion_label(self):
        """Say *why* motion is off when the host asked for it."""
        if self.settings["motion"] != 0:
            return "OFF"
        if self.system_reduced_motion and "motion" not in self.settings_explicit:
            return "ON (SYSTEM)"
        return "ON"

    def _open_settings(self, back_state):
        self.settings_back = back_state
        self._refresh_menus()
        self.menu = self.settings_menu
        self.state = self.SETTINGS

    def _adjust(self, action, delta):
        """Change one setting.  Enter passes 0 (one step / toggle)."""
        if action.startswith("vol:"):
            key = action.split(":", 1)[1]
            if key not in ("master", "music", "sfx"):
                return False
            lo, hi = SETTING_RANGES[key]
            step = 1 if delta >= 0 else -1
            self.settings[key] = max(lo, min(hi, self.settings[key] + step))
            self._apply_audio()
        elif action == "shake":
            step = 1 if delta >= 0 else -1
            self.settings["shake"] = (self.settings["shake"] + step) % 3
        elif action == "motion":
            self.settings["motion"] = 0 if self.settings["motion"] else 1
            # An explicit choice is the player's, and outranks the system
            # preference from now on (including after a reload).
            self.settings_explicit.add("motion")
            self._apply_effects()
        elif action == "quality":
            self.settings["quality"] = 0 if self.settings["quality"] else 1
            self.settings_explicit.add("quality")
            self._apply_effects()
        elif action == "sound":
            self.muted = not self.muted
            self.audio.set_enabled(not self.muted)
            self.toast("SOUND " + ("OFF" if self.muted else "ON"), 245)
        elif action == "screen":
            if not self.ui_caps.get("fullscreen"):
                return False
            self.screen_mode = ("fullscreen" if self.screen_mode == "window"
                                else "window")
            self.fullscreen_requests += 1
        else:
            return False
        self._refresh_menus()
        return True

    def _toggle(self, action):
        """Compatibility wrapper for settings rows that have two states."""
        return self._adjust(action, 0)

    def _reset_settings(self):
        self.settings = dict(DEFAULT_SETTINGS)
        self.muted = False
        self.screen_mode = "window"
        # Back to defaults also means "undecided": the system preference may
        # apply again, exactly as on a first visit.
        self.settings_explicit = set()
        if self.system_reduced_motion:
            self.settings["motion"] = 0
        self._apply_audio()
        self._apply_effects()
        self._refresh_menus()
        self.toast("SETTINGS RESET", 245)
        self._save_score()

    def _restart_current(self):
        if self.test_mode:
            lvl = Level("TEST PLAY", ED_THEMES[self.ed_theme_i],
                        ["".join(r) for r in self.ed_grid])
            self.load_level_obj(lvl, None)
        elif self.playing_custom:
            self.new_game_custom(self.custom_idx)
        else:
            self.load_level(self.li)

    def _open_level_select(self, trial=False):
        """Open the campaign list or the records-only time-trial list."""
        from_pause = self.state == self.PAUSE
        previous = getattr(self, "li", None)
        self.trial_mode = bool(trial) and self._has_records()
        self.levelsel_back = self.PAUSE if from_pause else self.TITLE
        self.state = self.LEVELSEL
        self._refresh_menus()
        if self.trial_mode:
            self.level_menu.select(0)
        elif from_pause and previous is not None:
            self.level_menu.select(previous)
        else:
            self.level_menu.select(first_uncleared(self.cleared,
                                                   self.unlocked))
        self.menu = self.level_menu
        self.sel_lvl = self.level_menu.index

    def _return_to_title(self, select_continue=True):
        self.trial_mode = False
        self.state = self.TITLE
        self.menu = self.title_menu
        self._refresh_menus()
        if select_continue:
            self.title_menu.select_action(
                "continue" if has_progress(self.hiscore, self.cleared,
                                           self.unlocked) else "new")

    def _ask_confirm(self, prompt, action, back_state):
        self.pending_prompt = prompt
        self.pending_action = action
        self.confirm_back = back_state
        # Destructive prompts default to NO so a stray Enter cannot wipe a run.
        self.confirm_menu.set_items([Item("YES", "yes"),
                                     Item("NO", "no")], index=1)
        self.menu = self.confirm_menu
        self.state = self.CONFIRM

    def _resolve_confirm(self, accepted):
        action = self.pending_action
        back = self.confirm_back
        self.pending_action = None
        if accepted and action:
            if action == "levels":
                self.levelsel_back = back
            self._activate(action)
            return
        self.state = back
        self.menu = {self.TITLE: self.title_menu,
                     self.PAUSE: self.pause_menu,
                     self.SETTINGS: self.settings_menu}.get(
                         back, self.title_menu)
        self._refresh_menus()

    def _finish_campaign(self):
        self.state = self.VICTORY
        self.player.celebrate = True
        self.fw_t = 0.0
        self.new_best_run = (self.best_total is None
                             or self.run_time < self.best_total)
        if self.run_from_start and not self.trial_mode and self.new_best_run:
            self.best_total = round(self.run_time, 2)
        if not self.trial_mode:
            self.hard_unlocked = True
        if self.hard and self.run_from_start and not self.trial_mode:
            prev = self.hard_best
            self.new_hard_best = prev is None or self.run_time < prev
            if self.new_hard_best:
                self.hard_best = round(self.run_time, 2)
        self._save_score()
        self.audio.play("win")

    def _activate(self, action):
        """Run the action of a menu row.  Actions are stable ids, not labels."""
        if action in ("start", "new"):
            self.fx.items = []
            self.new_game()
        elif action == "continue":
            self.fx.items = []
            self.new_game(first_uncleared(self.cleared, self.unlocked))
        elif action == "hard":
            self.fx.items = []
            self.new_game(hard=True)
        elif action == "trials":
            self._open_level_select(trial=True)
        elif action == "levels":
            self._open_level_select(trial=False)
        elif action == "settings":
            self._open_settings(self.state)
        elif action == "editor":
            self.ed_slot = None
            self._ed_init()
            self.ed_cam = 0
            self.ed_cx = 3
            self.ed_cy = 16
            self.state = self.EDITOR
        elif action == "help":
            self.help_back = self.TITLE
            self.state = self.HELP
        elif action == "credits":
            self.credits_back = self.TITLE
            self.state = self.CREDITS
        elif action == "records":
            self._open_records()
        elif action == "records_back":
            self._return_to_title(select_continue=False)
        elif action.startswith("ach:"):
            # Achievement rows are readable, not buttons.  Keep the focus so a
            # keyboard player can read the detail line without a dead end.
            self.audio.play("check")
        elif action == "resume":
            self.state = self.PLAY
        elif action == "checkpoint":
            if getattr(self, "lvl", None) is not None:
                self.respawn()
                self.toast("CHECKPOINT", 46)
        elif action == "restart":
            self._restart_current()
        elif action == "ask_levels":
            self._ask_confirm("ABANDON THIS RUN AND CHOOSE A LEVEL?",
                              "levels", self.PAUSE)
        elif action == "ask_quit":
            self._ask_confirm("ABANDON THIS RUN AND RETURN TO TITLE?",
                              "quit_to_title", self.PAUSE)
        elif action == "ask_reset":
            self._ask_confirm("RESTORE EVERY SETTING TO ITS DEFAULT?",
                              "reset", self.SETTINGS)
        elif action == "reset":
            self._reset_settings()
        elif action == "quit_to_title":
            if self.test_mode:
                self.state = self.EDITOR
            else:
                self._save_score()
                self._return_to_title()
        elif action == "back":
            self.state = self.settings_back
            self._save_score()
            self._refresh_menus()
            self.menu = {self.TITLE: self.title_menu,
                         self.PAUSE: self.pause_menu}.get(
                             self.state, self.title_menu)
        elif action == "results_editor":
            self.state = self.EDITOR
            self._refresh_menus()
        elif action == "results_title":
            self._save_score()
            self._return_to_title()
        elif action == "retry":
            self._restart_current()
        elif action == "next":
            if self.li is not None and self.li + 1 < len(self.levels):
                self.load_level(self.li + 1)
        elif action == "finish":
            self._finish_campaign()
        elif action in ("sound", "screen", "shake", "motion", "quality") \
                or action.startswith("vol:"):
            self._adjust(action, 0)
        elif action == "quit":
            if self.state == self.PAUSE:
                self._ask_confirm("ABANDON THIS RUN AND RETURN TO TITLE?",
                                  "quit_to_title", self.PAUSE)
            else:
                self.running = False
        elif action.startswith("level:"):
            self.sel_lvl = int(action.split(":", 1)[1])
            self.fx.items = []
            self.new_game(self.sel_lvl)
        elif action.startswith("trial:"):
            self.sel_lvl = int(action.split(":", 1)[1])
            self.fx.items = []
            self.new_game(self.sel_lvl, trial=True)
        elif action.startswith("custom:"):
            self.sel_lvl = len(self.levels) + int(action.split(":", 1)[1])
            self.fx.items = []
            self.new_game_custom(int(action.split(":", 1)[1]))
        return action

    def _step_settings(self, dt, edges):
        m = self.settings_menu
        if edges & {K_UP, "w"}:
            m.move(-1)
        if edges & {K_DOWN, "s"}:
            m.move(1)
        cur = m.current()
        if cur is not None and edges & {K_LEFT, "a", K_RIGHT, "d"}:
            if cur.enabled:
                delta = -1 if edges & {K_LEFT, "a"} else 1
                self._adjust(cur.action, delta)
            else:
                self.audio.play("stomp")
        if K_ESC in edges:
            self._save_score()
            self.state = self.settings_back
            self._refresh_menus()
            self.menu = {self.TITLE: self.title_menu,
                         self.PAUSE: self.pause_menu}.get(
                             self.state, self.title_menu)
        elif K_ENTER in edges and cur is not None:
            if cur.enabled:
                self._activate(cur.action)
            else:
                self.audio.play("stomp")
                self.toast("USE THE BROWSER FULLSCREEN BUTTON", 245)

    def _step_confirm(self, dt, edges):
        if edges & {K_UP, "w"}:
            self.confirm_menu.move(-1)
        if edges & {K_DOWN, "s"}:
            self.confirm_menu.move(1)
        if K_ESC in edges:
            self._resolve_confirm(False)
            return
        if edges & {K_ENTER, K_JUMP}:
            cur = self.confirm_menu.current()
            self._resolve_confirm(cur is not None and cur.action == "yes")

    def _step_credits(self, dt, edges):
        self.fx.update(dt)
        if edges & {K_ESC, K_ENTER, K_JUMP}:
            self.state = self.credits_back
            self._refresh_menus()
            self.menu = {self.TITLE: self.title_menu,
                         self.PAUSE: self.pause_menu}.get(
                             self.state, self.title_menu)

    def _step_records(self, dt, edges):
        if edges & {K_UP, "w"}:
            self.records_menu.move(-1)
        if edges & {K_DOWN, "s"}:
            self.records_menu.move(1)
        if K_ESC in edges:
            self._return_to_title(select_continue=False)
            return
        if edges & {K_ENTER, K_JUMP}:
            cur = self.records_menu.current()
            if cur is not None:
                self._activate(cur.action)

    def _step_title(self, dt, edges):
        self._amb_t -= dt
        if self._amb_t <= 0:
            self._amb_t = 0.12
            self.fx.spawn(self.cosmetic_rng.uniform(0, VIEW_W),
                      GAME_OY + GAME_H,
                      self.cosmetic_rng.uniform(-1, 1),
                      -self.cosmetic_rng.uniform(1.5, 4),
                      self.cosmetic_rng.uniform(1.2, 2.4),
                      self.cosmetic_rng.choice("+*·"),
                      self.cosmetic_rng.choice((44, 45, 201, 213, 50)))
        self.fx.update(dt)
        if edges & {K_UP, "w"}:
            self.title_menu.move(-1)
        if edges & {K_DOWN, "s"}:
            self.title_menu.move(1)
        self.sel = self.title_menu.index
        self.menu = self.title_menu
        if "q" in edges:
            self.running = False
            return
        if K_ENTER in edges or K_JUMP in edges:
            cur = self.title_menu.current()
            if cur is not None:
                self._activate(cur.action)

    def _step_levelsel(self, dt, edges):
        if edges & {K_ESC}:
            self.trial_mode = False
            back = getattr(self, "levelsel_back", self.TITLE)
            self.state = back
            self._refresh_menus()
            self.menu = {self.PAUSE: self.pause_menu}.get(back,
                                                          self.title_menu)
            return
        if edges & {K_UP, "w"}:
            self.level_menu.move(-1)
        if edges & {K_DOWN, "s"}:
            self.level_menu.move(1)
        self.sel_lvl = self.level_menu.index
        if K_ENTER in edges or K_JUMP in edges:
            cur = self.level_menu.current()
            if cur is None:
                return
            if not cur.enabled:
                self.audio.play("stomp")
                self.toast("LOCKED - CLEAR THE PREVIOUS LEVEL", 196, 1.6)
                return
            self._activate(cur.action)

    def _fireworks(self, dt):
        self.parts.update(dt)
        self.fw_t -= dt
        if self.fw_t <= 0:
            self.fw_t = 0.38
            x = self.cosmetic_rng.uniform(self.cam + 8, self.cam + VIEW_W - 8)
            y = self.cosmetic_rng.uniform(GAME_OY + 2, GAME_OY + 9)
            self.parts.firework(x, y)

    # --------------------------------------------------------- gameplay --
    def _gameplay(self, dt, keys):
        inp = self._inputs(keys)
        p = self.lvl
        pl = self.player
        for m in self.movers:
            m.update(dt)
        if self.player_riding is not None:
            m = self.movers[self.player_riding]
            pl.x += m.x - m.px
            pl.y += m.y - m.py
        prev_bottom = pl.bottom
        was_dashing = pl.dash_t > 0
        pl.update(dt, p, inp["left"], inp["right"], inp["jump"],
                  inp["down"], self.parts, inp["dash"], rng=self.cosmetic_rng)
        if pl.just_jumped:
            pl.just_jumped = False
            self.audio.play("jump")
            if pl.wall_jump_t > 0:
                self.parts.wall(pl.x + PLAYER_W / 2, pl.bottom - 0.4,
                                pl.face)
            else:
                self.parts.takeoff(pl.x + PLAYER_W / 2, pl.bottom)
        if pl.just_dashed:
            pl.just_dashed = False
            self.audio.play("dash")
            self.parts.dash(pl.x + PLAYER_W / 2, pl.bottom - 0.8, pl.face)

        # Teaching cues fire once, before the mechanic they explain.  A ring
        # hit later in this same frame overrides the cue's toast.
        for cue in list(self.teach_cues):
            if pl.x >= cue[0]:
                self.teach_cues.remove(cue)
                self.toast(cue[1], cue[2], 2.0)

        # A dash that started on this very frame is active for this frame's
        # movement, so it must collect too; otherwise a ring reached exactly as
        # the player presses dash is skipped by one 0.43-cell step.
        node = self.resonance.scan(
            pl.rect(), was_dashing or pl.dash_t > 0, self.t)
        if node is not None:
            pl.resonate()
            pts = chain_points(self.resonance.chain)
            self.chain_best = max(self.chain_best, self.resonance.chain)
            self.add_score(pts)
            self.audio.play("resonance")
            self.parts.resonance(node[0] + 0.5, node[1] + 0.5)
            if self.resonance.chain > 1:
                self.toast("PRISM CHAIN x%d  +%d"
                           % (self.resonance.chain, pts), 51, 1.2)
            else:
                self.toast("PRISM RESONANCE  +%d" % pts, 51, 1.2)
        if pl.on_ground:
            self.resonance.rearm()

        self.level_time += dt
        self.run_time += dt

        pr = pl.rect()
        if pl.on_ground:
            self.stomp_chain = 0
        if pl.y > p.h + 1:
            self.die("fell")
            return

        for tx in {int(pl.x + 0.15), int(pl.x + PLAYER_W - 0.15)}:
            for ty in range(int(pl.y + 0.1), int(pl.bottom - 0.05) + 1):
                tile = p.tile(tx, ty)
                if tile == "^":
                    self.die("spikes")
                    return
                if tile == "~":
                    self.die("lava")
                    return

        for c in list(self.coins):
            if _overlap(pr, (c[0] - 0.15, c[1] - 0.3, 1.3, 1.6)):
                self.coins.discard(c)
                self.coins_n += 1
                self.add_score(100)
                self.audio.play("coin")
                self.parts.coin(c[0], c[1])
        for g in list(self.gems):
            if _overlap(pr, (g[0] - 0.3, g[1] - 0.3, 1.6, 1.6)):
                self.gems.discard(g)
                self.gems_n += 1
                self.add_score(500)
                self.audio.play("gem")
                self.parts.gem(g[0], g[1])
                self.toast("CRYSTAL +500", 51)
                if g == getattr(self.lvl, "high_gem", None):
                    self._discover_high_gem()

        for s in p.springs:
            cx = pl.x + PLAYER_W / 2
            if (pl.vy >= -0.01 and abs(cx - (s[0] + 0.5)) < 1.0
                    and s[1] + 0.55 <= pl.bottom <= s[1] + 1.35):
                pl.vy = -SPRING_V
                pl.on_ground = False
                pl.coyote = 0.0
                pl.can_dash = False
                self.spring_anim[s] = 0.30
                self.audio.play("spring")
                self.parts.burst(s[0] + 0.5, s[1], 4, "'\"",
                                 (250, 246), 7)
        for k in list(self.spring_anim):
            self.spring_anim[k] -= dt
            if self.spring_anim[k] <= 0:
                del self.spring_anim[k]

        for c in self.lvl.checkpoints:
            if c not in self.cp_on and _overlap(pr, (c[0] - 0.5, c[1] - 3, 2, 5)):
                self.cp_on.add(c)
                self.respawn_pt = (c[0], c[1] - 1)
                self.audio.play("check")
                self.parts.burst(c[0] + 0.5, c[1] - 1, 6, "*+",
                                 (46, 82, 120), 8)
                self.toast("CHECKPOINT", 46)

        for e in self.enemies:
            if not e.alive:
                continue
            if e.kind in ("boss", "king"):
                e.update(dt, p, pl.x, pl.y, self.shots)
                er = e.rect()
                if _overlap(pr, er):
                    if pl.vy > 0 and pl.bottom - e.y < 1.2:
                        pl.y = e.y - PLAYER_H
                        pl.vy = -STOMP_BOUNCE
                        if e.hit():
                            self.audio.play("bosshit")
                            self.add_score(800)
                            self.parts.stomp(e.x + 1.2, e.y)
                            self.shake = max(self.shake, 0.3)
                            if e.hp <= 0:
                                e.alive = False
                                self.add_score(2000)
                                self.parts.death(e.x + 1.2, e.y + 1)
                                self.parts.firework(e.x + 1.2, e.y)
                                self.audio.play("fanfare")
                                self.shake = 0.5
                                self.toast(
                                    "%s DEFEATED!  +2000"
                                    % getattr(e, "display_name",
                                              "GUARDIAN"), 196, 2.5)
                        else:
                            self.audio.play("stomp")
                    else:
                        self.die("enemy")
                        return
                continue
            if e.kind == "sentry":
                e.update(dt, p, pl.x, pl.y, self.shots)
                continue
            if e.kind in ("walker", "charger"):
                e.update(dt, p, pl.x, pl.y)
            else:
                e.update(dt, p)
            er = (e.x, e.y, 0.9, 1.0)
            if not _overlap(pr, er):
                continue
            if e.kind == "spiker":
                self.die("enemy")
                return
            if pl.vy > 0 and pl.bottom - e.y < 0.75:
                e.alive = False
                pl.y = e.y - PLAYER_H
                pl.vy = -STOMP_BOUNCE
                self.stomp_chain += 1
                pts = min(200 * self.stomp_chain, 1000)
                self.add_score(pts)
                self.audio.play("stomp")
                self.parts.stomp(e.x, e.y)
                if self.stomp_chain >= 2:
                    self.toast("COMBO x%d  +%d" % (self.stomp_chain, pts),
                               214, 1.2)
                self.shake = max(self.shake, 0.12)
            else:
                self.die("enemy")
                return

        for pw in list(self.powers):
            if _overlap(pr, (pw[0] - 0.2, pw[1] - 0.3, 1.4, 1.6)):
                self.powers.discard(pw)
                pl.invuln = max(pl.invuln, 6.0)
                self.add_score(500)
                self.audio.play("star")
                self.parts.burst(pw[0] + 0.5, pw[1], 10, "*+",
                                 (226, 214, 196, 201), 9)
                self.toast("STAR POWER!", 226)
        for s in self.shots:
            s.update(dt, p)
            if s.alive and _overlap(pr, s.rect()):
                self.die("enemy")
                return
        self.shots = [s for s in self.shots if s.alive]

        self.player_riding = None
        for mi, m in enumerate(self.movers):
            if (pl.vy >= -0.01
                    and pl.x + PLAYER_W > m.x + 0.15
                    and pl.x < m.x + m.W - 0.15
                    and prev_bottom <= m.py + 0.2
                    and -0.05 <= pl.bottom - m.y <= 0.55):
                pl.y = m.y - PLAYER_H
                pl.vy = 0.0
                pl.on_ground = True
                pl.can_dash = True
                pl.coyote = 0.1
                self.player_riding = mi
                break

        self.trace.append((round(pl.x, 1), round(pl.y, 1), pl.face))

        if pl.x + PLAYER_W >= self.lvl.flag[0]:
            blockers = [e for e in self.enemies
                        if getattr(e, "blocks_goal", False) and e.alive]
            if blockers:
                if self.t > self.gate_t:
                    self.gate_t = self.t + 2.0
                    self.toast("DEFEAT THE %s!"
                               % getattr(blockers[0], "display_name",
                                         "GUARDIAN"), 196)
            else:
                self.complete_level()
                return

        self._update_camera(dt)
        self._spawn_embers()
        self.parts.update(dt)
        if self.toast_msg and self.t > self.toast_msg[2]:
            self.toast_msg = None
        if self.shake > 0:
            self.shake = max(0.0, self.shake - dt * 1.6)

    @property
    def cam(self):
        """Viewport left edge in cells (kept as a property for readers)."""
        return self.camera.x

    @cam.setter
    def cam(self, value):
        self.camera.x = float(value)
        self.camera.clamp(self.lvl.w if hasattr(self, "lvl") else 0)

    def _update_camera(self, dt):
        """Follow the player with a deadzone; see ``lumenvalley.motion.Camera``."""
        pl = self.player
        if self.legacy_rng:
            # The Phase 2 trace records camera positions, so replay mode keeps
            # the original chase formula byte-for-byte.  Shipping runs use the
            # deadzone camera below.
            target = (pl.x + PLAYER_W / 2 - VIEW_W / 2
                      + pl.face * 3 + pl.vx * 0.2)
            self.camera.x += (target - self.camera.x) * min(1.0, dt * 6.0)
            self.camera.x = max(0.0, min(self.lvl.w - VIEW_W, self.camera.x))
            return
        focus = None
        for e in self.enemies:
            if e.alive and e.kind in ("boss", "king"):
                focus = arena_focus(pl.x + PLAYER_W / 2,
                                    e.x + getattr(e, "W", 1.0) / 2.0)
                if focus is not None:
                    break
        self.camera.update(dt, pl.x, pl.vx, self.lvl.w, focus_x=focus)

    def _spawn_embers(self):
        # Low graphics shortens the ambient budget; reduced motion keeps the
        # glow but drops the drifting specks entirely (ART_BIBLE §5).
        if self.settings.get("motion", 1) == 0:
            return
        if not self.lava_tiles \
                or self.cosmetic_rng.random() > ember_chance(
                    self.settings.get("quality", 1)):
            return
        x, y = self.cosmetic_rng.choice(self.lava_tiles)
        if not (self.cam - 2 <= x <= self.cam + VIEW_W + 2):
            return
        self.parts.spawn(x + self.cosmetic_rng.random(), y,
                         self.cosmetic_rng.uniform(-1.5, 1.5),
                         -self.cosmetic_rng.uniform(3, 9),
                         self.cosmetic_rng.uniform(0.3, 0.7),
                         self.cosmetic_rng.choice("*·'"),
                         self.cosmetic_rng.choice((208, 202, 226)))

