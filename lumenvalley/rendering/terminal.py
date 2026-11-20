"""Terminal (ANSI) presentation for the headless simulation.

This is the legacy renderer.  It reads simulation state and never mutates
gameplay rules; the pygame front-end lives in ``pygame_view.py``.
"""

import math

import background as bgmod
import sprites as sp
from lumenvalley.constants import (
    ED_THEMES, ED_TOOLS, ED_W, GAME_H, GAME_OY, PAR_TIMES, STEP, THEMES,
    VERSION, VIEW_H, VIEW_W,
)
from lumenvalley import motion
from lumenvalley.bestiary import BOSS_KINDS, enemy_pose, is_telling
from lumenvalley.ghosts import playhead
from lumenvalley.progress import medal_for, pace_line, threat_count
from lumenvalley.ui import HINTS
from entities import PLAYER_H, PLAYER_W
from levels import Level


def _fmt_time(t):
    return "%02d:%02d" % (int(t) // 60, int(t) % 60)


LETTERS = {
    "C": [" ██████╗ ", "██╔════╝", "██║      ", "██║      ", "╚██████╗ ", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
}
LOGO1 = ["".join(LETTERS[c][i] for c in "LUMEN") for i in range(6)]
LOGO2 = ["".join(LETTERS[c][i] for c in "VALLEY") for i in range(6)]
LOGO1_C = (51, 45, 39, 33, 27, 21)
LOGO2_C = (213, 212, 207, 176, 171, 164)


class TerminalPresentation:
    # -------------------------------------------------------------- draw --
    def draw(self):
        scr = self.scr
        scr.clear()
        st = self.state
        if st == self.TITLE:
            self._draw_title()
        elif st == self.LEVELSEL:
            self._draw_levelsel()
        elif st == self.EDITOR:
            self._draw_editor()
        elif st == self.SETTINGS:
            self._draw_settings()
        elif st == self.HELP:
            self._draw_help()
        elif st == self.CREDITS:
            self._draw_credits()
        elif st == self.RECORDS:
            self._draw_records()
        elif st == self.CONFIRM:
            self._draw_confirm()
        elif st == self.VICTORY:
            self._draw_victory()
        elif st == self.GAMEOVER:
            self._draw_gameover()
        else:
            self._draw_world()
            if st == self.INTRO:
                self._draw_intro()
            elif st == self.PAUSE:
                self._draw_pause()
            elif st == self.COMPLETE:
                self._draw_complete()

    def _cam_int(self):
        sx = 0
        if self.settings.get("motion", 1) and self.shake > 0:
            strength = (0.0, 1.0, 1.6)[self.settings.get("shake", 1)]
            sx = int(self.cosmetic_rng.uniform(-1, 1) * self.shake * 4
                     * strength)
        return max(0, int(round(self.cam)) + sx)

    def _draw_world(self, include_player=True):
        scr = self.scr
        lvl = self.lvl
        cam = self._cam_int()
        bgmod.draw_sky(scr, lvl.theme, GAME_OY, GAME_H)
        bg_t = motion.background_time(self.t, self.settings.get("motion", 1))
        bgmod.draw_parallax(scr, lvl.theme, cam, GAME_OY, GAME_H, bg_t)
        self._draw_tiles(cam)
        self._draw_items(cam)
        self._draw_ghost(cam)
        self._draw_enemies(cam)
        if include_player and (self.player.alive or self.state == self.DEAD):
            self._draw_player(cam)
        for pt in self.parts.items:
            scr.put(int(round(pt[0])) - cam, int(round(pt[1])) + GAME_OY,
                    pt[5], pt[6])
        for s in self.shots:
            sx = int(round(s.x)) - cam
            if 0 <= sx < VIEW_W:
                scr.put(sx, int(round(s.y)) + GAME_OY, "●", 213)
        self._draw_hud()
        self._draw_hint()

    def _draw_tiles(self, cam):
        scr = self.scr
        lvl = self.lvl
        th = self.theme
        t = self.t
        x0 = max(0, cam - 1)
        x1 = min(lvl.w, cam + VIEW_W + 2)
        for gy in range(lvl.h):
            sy = gy + GAME_OY
            for gx in range(x0, x1):
                ch = lvl.grid[gy][gx]
                sx = gx - cam
                if ch == "#":
                    above = lvl.tile(gx, gy - 1)
                    if above not in ("#", "B"):
                        scr.put(sx, sy, "▀", th["grass"], th["ground"])
                        if (gx * 2654435761 % 7) < 2:
                            blade = ('"', ",", "'")[gx % 3]
                            scr.over(sx, sy - 1, blade, th["blade"])
                    elif (gx * 73 + gy * 151) % 23 == 0:
                        scr.put(sx, sy, "·", th["gdot"], th["ground"])
                    else:
                        scr.put(sx, sy, " ", 252, th["ground"])
                elif ch == "B":
                    scr.put(sx, sy, "▒", th["brick_f"], th["brick_b"])
                elif ch == "-":
                    scr.over(sx, sy, "═", th["plat"])
                elif ch == "^":
                    scr.put(sx, sy, "▲", th["spike"], 16)
                elif ch == "~":
                    deep = lvl.tile(gx, gy + 1) == "~"
                    if deep:
                        scr.put(sx, sy, " ", 202, 52)
                    else:
                        i = int(t * 7 + gx * 1.3) % 3
                        scr.put(sx, sy, ("~", "≈", "-")[i],
                                (226, 208, 202)[i], 52)

    def _draw_items(self, cam):
        scr = self.scr
        lvl = self.lvl
        t = self.t
        star_cols = (226, 214, 196, 201)
        for (x, y) in self.powers:
            sx = int(round(x - cam))
            sy = GAME_OY + int(round(y)) - 1
            if 0 <= sx < VIEW_W:
                scr.over(sx, sy, "*",
                         star_cols[int(t * 10) % 4])
        for mi, m in enumerate(self.movers):
            sx = int(round(m.x - cam))
            sy = GAME_OY + int(round(m.y))
            if -4 <= sx <= VIEW_W:
                col = 39 if m.axis == "h" else 45
                for k in range(int(m.W)):
                    scr.over(sx + k, sy, "=", col)
                if int(t * 3) % 2:
                    if m.axis == "h":
                        scr.over(sx + int(m.W) - 1, sy, ">", 226)
                    else:
                        scr.over(sx + 1, sy - (1 if m.y <= m.sy else -1),
                                 "^" if m.y <= m.sy else "v", 226)
        for (x, y) in lvl.springs:
            spr = sp.SPRING_DOWN if self.spring_anim.get((x, y), 0) > 0 \
                else sp.SPRING_UP
            sp.blit_bottom(scr, spr, x + 0.5, y + 1 + GAME_OY)
        for (x, y) in self.coins:
            f = int(t * 6 + x * 0.7 + y) % 4
            sp.blit_bottom(scr, sp.COIN_FRAMES[f], x + 0.5, y + 1 + GAME_OY)
        for (x, y) in self.gems:
            sp.blit_bottom(scr, sp.GEM, x + 0.5, y + 1 + GAME_OY)
        field = getattr(self, "resonance", None)
        armed_pulse = motion.prism_pulse(t, self.settings.get("motion", 1))
        for (x, y) in getattr(lvl, "resonance", ()):
            sx = int(round(x - cam))
            sy = GAME_OY + int(round(y))
            if 0 <= sx < VIEW_W:
                armed = field is None or field.armed((x, y))
                if armed:
                    glyph = "◎" if armed_pulse >= 0.5 else "◉"
                    scr.over(sx, sy, glyph, 51)
                else:
                    scr.over(sx, sy, "○", 240)
        for (x, y) in lvl.checkpoints:
            on = (x, y) in self.cp_on
            col = 46 if on else 240
            scr.put(x - cam, y - 1 + GAME_OY, "│", 245)
            scr.put(x - cam, y - 2 + GAME_OY, "o" if on else "·", col)
            scr.put(x - cam + 1, y - 2 + GAME_OY, "»", col)
        fx, fy = lvl.flag
        sx = fx - cam
        gy = fy
        while lvl.tile(fx, gy + 1) not in ("#", "B") and gy < lvl.h - 1:
            gy += 1
        scr.put(sx, fy - 1 + GAME_OY, "●", 226)
        for yy in range(fy, gy + 1):
            scr.put(sx, yy + GAME_OY, "║", 145)
        wave = int(t * 2) % 2
        scr.put(sx + 1, fy + GAME_OY, "▛" if wave else "▜", 196)
        scr.put(sx + 1, fy + 1 + GAME_OY, "★", 226)

    def _draw_ghost(self, cam):
        if not self.ghost or self.state != self.PLAY:
            return
        pos = playhead(self.ghost, self.level_time)
        if pos is None:
            return
        if self._ghost_rows is None:
            self._ghost_rows = sp.ghostify(sp.PLAYER_IDLE)
        gx, gy, face = pos
        sp.blit_bottom(self.scr, self._ghost_rows,
                       gx + PLAYER_W / 2 - cam, gy + PLAYER_H + GAME_OY,
                       flip=face < 0)

    def _draw_enemies(self, cam):
        scr = self.scr
        t = self.t
        for e in self.enemies:
            if not e.alive:
                continue
            sx = e.x - cam
            boss = e.kind in BOSS_KINDS
            span = 9 if boss else 3
            if not -span <= sx <= VIEW_W + span:
                continue
            if boss and e.hurt > 0 and int(e.hurt * 12) % 2:
                continue
            name, _tell = enemy_pose(e, t)
            if e.kind == "sentry":
                flip = getattr(e, "face", 1) > 0
            elif boss:
                flip = e.face > 0
            else:
                flip = getattr(e, "dir", 0) > 0
            rows = getattr(sp, name)
            cx = e.x + (1.2 if boss else 0.45)
            bottom = e.y + (e.H if boss else 1)
            sp.blit_bottom(scr, rows, cx, bottom + GAME_OY, flip=flip)
            head = int(math.floor(bottom - len(rows) / 2.0))
            if boss:
                for i in range(e.hp):
                    scr.put(int(round(cx - cam)) + i * 2,
                            head - 1 + GAME_OY, "\u25cf",
                            240 if e.hurt > 0 else 196)
            if is_telling(e):
                scr.put(int(round(cx - cam)), head - (2 if boss else 1) + GAME_OY,
                        "!", 220)

    def _draw_player(self, cam):
        pl = self.player
        if pl.invuln > 0 and int(pl.invuln * 12) % 2:
            return
        # One pose rule for every front-end (lumenvalley.motion): the terminal
        # used to keep its own copy and drifted from the pygame build.
        frame, fx, fy = motion.player_pose(
            on_ground=pl.on_ground, vx=pl.vx, vy=pl.vy, dash_t=pl.dash_t,
            land_t=pl.land_t, air_t=pl.air_t, distance=pl.run_dist,
            wall_dir=pl.wall_dir, wall_jump_t=pl.wall_jump_t,
            dead=not pl.alive, hurt_t=pl.hurt_t, respawn_t=pl.respawn_t,
            celebrate=pl.celebrate)
        rows = getattr(sp, "PLAYER_" + frame)
        if fx != 1.0 or fy != 1.0:
            rows = sp.scale(rows, fx, fy)
        sp.blit_bottom(self.scr, rows,
                       pl.x + PLAYER_W / 2 - cam, pl.bottom + GAME_OY,
                       flip=pl.face < 0)

    # ---------------------------------------------------------------- hud --
    def _draw_hud(self):
        scr = self.scr
        acc = self.theme["accent"]
        scr.rect_bg(0, 0, VIEW_W, 2, 16)
        hearts = "♥" * min(self.lives, 5)
        dim = "·" * max(0, min(5, 5 - self.lives))
        scr.text(1, 0, hearts, 196, 16)
        scr.text(1 + len(hearts), 0, dim, 88, 16)
        name = self.lvl.name.upper()
        scr.text_center(0, name, acc, 16)
        scr.text(VIEW_W - 14, 0, "SCORE %06d" % self.score, 252, 16)
        scr.text(1, 1, "● %02d" % self.coins_n, 226, 16)
        scr.text(9, 1, "◆ %d" % self.gems_n, 51, 16)
        ready = self.player.can_dash and self.player.dash_cd <= 0
        scr.text(18, 1, "DASH OK" if ready else "DASH --",
                 acc if ready else 88, 16)
        chain = getattr(getattr(self, "resonance", None), "chain", 0)
        if chain > 0:
            scr.text(28, 1, "PRISM x%d" % chain, 51, 16)
        scr.text(VIEW_W - 23, 1, "TIME %s" % _fmt_time(self.level_time),
                 245, 16)
        if self.muted:
            scr.text(VIEW_W - 29, 1, "MUTE", 240, 16)
        scr.text(VIEW_W - 11, 1, "HI %06d" % max(self.hiscore, self.score),
                 178, 16)
        if self.toast_msg and int((self.toast_msg[2] - self.t) * 6) % 2:
            msg, col, _ = self.toast_msg
            scr.text_center(2, msg, col)
        else:
            pct = max(0.0, min(1.0, (self.player.x - self.lvl.start[0])
                               / max(1.0, self.lvl.flag[0]
                                     - self.lvl.start[0])))
            n = 24
            filled = int(pct * n)
            bar = "■" * filled + "·" * (n - filled)
            scr.text(VIEW_W - len(bar) - 3, 2, "▐" + bar + "▌", acc)

    def _draw_hint(self):
        hint = "←→ MOVE · SPACE JUMP/WALL · X DASH · ↓ DROP · ESC PAUSE · R RETRY · M SND"
        self.scr.text_center(VIEW_H - 1, hint[:VIEW_W], 240)

    # ------------------------------------------------------------- panels --
    def _panel(self, w, h, title_color=252):
        scr = self.scr
        x = (VIEW_W - w) // 2
        y = (VIEW_H - h) // 2
        scr.rect_bg(x, y, w, h, 16)
        scr.put(x, y, "┌", title_color, 16)
        scr.put(x + w - 1, y, "┐", title_color, 16)
        scr.put(x, y + h - 1, "└", title_color, 16)
        scr.put(x + w - 1, y + h - 1, "┘", title_color, 16)
        for i in range(1, w - 1):
            scr.put(x + i, y, "─", title_color, 16)
            scr.put(x + i, y + h - 1, "─", title_color, 16)
        for j in range(1, h - 1):
            scr.put(x, y + j, "│", title_color, 16)
            scr.put(x + w - 1, y + j, "│", title_color, 16)
        return x, y

    def _dim(self):
        scr = self.scr
        for yy in range(GAME_OY, GAME_OY + GAME_H):
            for xx in range(VIEW_W):
                if (xx + yy) % 2 == 0:
                    ch, fg, _ = scr.cells[yy][xx]
                    scr.cells[yy][xx] = (ch, 236, 16)

    # -------------------------------------------------------------- states --
    def _draw_intro(self):
        self._dim()
        x, y = self._panel(42, 8, self.theme["accent"])
        scr = self.scr
        scr.text_center(y + 2, "LEVEL %d OF %d" % (self.li + 1, len(self.levels)),
                        245, 16)
        scr.text_center(y + 3, self.lvl.name, 226, 16)
        if self.li is not None:
            best = self.best_times[self.li]
            if best is not None:
                note = "BEST TIME  %s  ·  RACE THE GHOST!" % _fmt_time(best) \
                    if self.ghost else "BEST TIME  %s" % _fmt_time(best)
                scr.text_center(y + 4, note, 178, 16)
        dots = "." * max(1, int(self.st_t * 3))
        scr.text_center(y + 6, "GET READY " + dots, 252, 16)

    def _draw_menu(self, menu, top, width=40):
        """Render a menu row-by-row in the legacy grid.

        Same model as the pygame/menu renderer: focus is a leading marker, a
        disabled row is dim, values are right-aligned inside ``width``.
        """
        scr = self.scr
        for row, item in enumerate(menu.visible_items()):
            i = menu.window()[0] + row
            sel = i == menu.index
            col = 226 if sel else (240 if not item.enabled else 252)
            body = item.label
            if item.value:
                body = "%s%s" % (body.ljust(width - len(item.value)),
                                 item.value)
            cur = ">" if sel else " "
            scr.text_center(top + row, "%s %s %s" % (cur, body[:width], cur),
                            col, 16)

    def _draw_settings(self):
        self._dim()
        x, y = self._panel(56, 16, 141)
        scr = self.scr
        scr.text_center(y + 2, "SETTINGS", 141, 16)
        self._draw_menu(self.settings_menu, y + 4, 46)
        scr.text_center(y + 14, HINTS["settings"], 240, 16)

    def _draw_pause(self):
        self._dim()
        x, y = self._panel(40, 13)
        scr = self.scr
        scr.text_center(y + 2, "PAUSED", 226, 16)
        self._draw_menu(self.pause_menu, y + 4, 24)
        scr.text_center(y + 11, HINTS["pause"], 240, 16)

    def _draw_credits(self):
        scr = self.scr
        bgmod.draw_sky(scr, "cave", 0, VIEW_H)
        bgmod.draw_crystals(scr, self.t * 5.0, 0, self.t, 0.35, 14, 23.0)
        x, y = self._panel(64, 15, 51)
        scr.text_center(y + 2, "CREDITS", 226, 16)
        lines = (
            ("LUMENVALLEY", 51),
            ("GAME DESIGN & CODE   SIAM (IAMS1AM)", 252),
            ("ENGINE   PYTHON + PYGAME-CE", 245),
            ("BROWSER BUILD   PYGBAG / WEBASSEMBLY", 245),
            ("PIXEL ART   PROCEDURAL SPRITES", 245),
            ("MUSIC & SFX   PROCEDURAL SYNTHESIS", 245),
            ("TYPE   FREESANS BOLD (GNU FREE FONT)", 245),
            ("THANKS   EVERY PLAYER AND PLAYTESTER", 245),
            ("THANKS FOR PLAYING", 226),
        )
        for i, (txt, col) in enumerate(lines):
            scr.text_center(y + 4 + i, txt, col, 16)
        scr.text_center(y + 13, HINTS["credits"], 240, 16)

    def _draw_records(self):
        scr = self.scr
        bgmod.draw_sky(scr, "dusk", 0, VIEW_H)
        bgmod.draw_crystals(scr, self.t * 5.0, 0, self.t, 0.35, 14, 23.0)
        x, y = self._panel(72, 18, 141)
        scr.text_center(y + 2, "RECORDS", 141, 16)
        for i, line in enumerate(self._record_summary()):
            scr.text_center(y + 3 + i, line, 245 if i < 2 else 178, 16)
        self._draw_menu(self.records_menu, y + 6, 60)
        if self.records_menu.clipped():
            start, end = self.records_menu.window()
            scr.text_center(y + 12, "%d-%d OF %d"
                            % (start + 1, end, len(self.records_menu)), 240,
                            16)
        scr.text_center(y + 13, self._records_detail()[:66], 240, 16)
        scr.text_center(y + 14, HINTS["records"], 240, 16)

    def _draw_confirm(self):
        if self.confirm_back == self.PAUSE:
            self._draw_world()
        elif self.confirm_back == self.SETTINGS:
            self._draw_settings()
        else:
            self._draw_title()
        self._dim()
        x, y = self._panel(58, 9, 214)
        scr = self.scr
        scr.text_center(y + 2, "ARE YOU SURE?", 226, 16)
        scr.text_center(y + 3, self.pending_prompt, 252, 16)
        self._draw_menu(self.confirm_menu, y + 5, 24)
        scr.text_center(y + 7, HINTS["confirm"], 240, 16)

    def _draw_complete(self):
        self._dim()
        s = self.stats or dict(time=0, time_bonus=0, coins=0, gems=0,
                               level_bonus=0)
        x, y = self._panel(52, 18, 226)
        scr = self.scr
        scr.text_center(y + 2, "LEVEL CLEAR!", 46, 16)
        if self.li is not None:
            best = self.best_times[self.li]
            best_txt = _fmt_time(best) if best is not None else "--:--"
            if getattr(self, "new_best_time", False):
                best_txt += " *NEW*"
        else:
            best_txt = "--:--"
        rows = (
            ("TIME  %s" % _fmt_time(s["time"]), 252),
            ("BEST  %s" % best_txt, 178),
            ("COINS %d / %d" % (self.coins_n, self.level_coins_total), 226),
            ("GEMS  %d / %d" % (self.gems_n, self.level_gems_total), 51),
            ("DEATHS %d" % getattr(self, "deaths_level", 0), 245),
            ("MEDAL %s   GRADE %s" % (getattr(self, "medal", "") or "-",
                                      getattr(self, "grade", "") or "-"),
             226),
            ("TOTAL SCORE  %06d" % self.score, 178),
        )
        # Campaign-only row: a custom or editor run has no challenge set.
        if (self.li is not None and not self.playing_custom
                and not self.test_mode):
            rows = rows[:-1] + (
                ("CHALLENGES %d / %d"
                 % self._challenge_progress(self.li), 51), rows[-1])
        for i, (txt, col) in enumerate(rows):
            scr.text_center(y + 3 + i, txt, col, 16)
        self._draw_menu(self.results_menu, y + 12, 44)
        if self.save_note == "SAVE FAILED":
            note = "SAVE FAILED"
        elif self.record_note:
            note = self.record_note
        elif self.new_record:
            note = "NEW BEST SCORE!"
        else:
            note = self.save_note or HINTS["results"]
        scr.text_center(y + 16, note, 240, 16)

    def _draw_gameover(self):
        scr = self.scr
        bgmod.draw_sky(scr, "dusk", 0, VIEW_H)
        x, y = self._panel(44, 9, 196)
        scr.text_center(y + 2, "GAME OVER", 196, 16)
        if self.new_record:
            scr.text_center(y + 4, "NEW BEST SCORE!", 226, 16)
        else:
            scr.text_center(y + 4, "SCORE %06d" % self.score, 252, 16)
        scr.text_center(y + 5, "BEST  %06d" % self.hiscore, 178, 16)
        if int(self.t * 2) % 2:
            scr.text_center(y + 7, "PRESS ENTER", 245, 16)

    def _draw_victory(self):
        scr = self.scr
        bgmod.draw_sky(scr, "day", 0, VIEW_H)
        bgmod.draw_parallax(scr, "day", self.t * 4.0, 0, VIEW_H, self.t)
        for pt in self.parts.items:
            scr.put(int(round(pt[0])), int(round(pt[1])), pt[5], pt[6])
        self.parts.update(STEP)
        x, y = self._panel(54, 12, 51)
        scr.text_center(y + 2, "LUMENVALLEY IS FREE!", 51, 16)
        scr.text_center(y + 4, "FINAL SCORE  %06d" % self.score, 226, 16)
        prefix = "HARD MODE CLEARED!  " if getattr(self, "hard", False) \
            else ""
        run_txt = prefix + "CLEAR TIME  %s" % _fmt_time(self.run_time)
        if getattr(self, "new_best_run", False):
            run_txt += "  *NEW BEST*"
        elif self.best_total is not None:
            run_txt += "  (BEST %s)" % _fmt_time(self.best_total)
        scr.text_center(y + 5, run_txt, 178, 16)
        scr.text_center(y + 6, "BEST SCORE   %06d" % self.hiscore, 178, 16)
        if int(self.t * 2) % 2:
            scr.text_center(y + 9, "PRESS ENTER FOR TITLE", 245, 16)

    def _draw_help(self):
        scr = self.scr
        bgmod.draw_sky(scr, "cave", 0, VIEW_H)
        bgmod.draw_crystals(scr, self.t * 6.0, 0, self.t, 0.4, 14, 11.0)
        # 17 lines at y+2..y+18 plus the hint at y+20: a 22-row panel is the
        # only size that fits them without drawing over its own border.
        x, y = self._panel(64, 22, 37)
        lines = (
            ("HOW TO PLAY", 226),
            ("Reach the flag at the end of each level.", 252),
            ("Stomp enemies from above. Avoid spikes and lava.", 252),
            ("CHARGERS rush when they spot you.", 245),
            ("SPIKERS are armored - even stomping them hurts!", 245),
            ("←→ / A D     move", 245),
            ("SPACE / W    jump · press again on walls to wall-jump", 245),
            ("X / L        air dash (once per jump)", 245),
            ("PRISM RINGS  dash through to refill + chain 100/200/300", 51),
            ("↓ / S        drop through platforms", 245),
            ("SPRINGS      launch you to high ledges", 245),
            ("CHECKPOINTS  activate as you pass them", 245),
            ("ESC          pause · M          sound on/off", 245),
            ("SCORING   coin 100 · gem 500 · stomp 200 + time bonus", 178),
            ("MEDALS    GOLD 60% PAR · SILVER 80% · BRONZE 100%", 178),
            ("RECORDS   CHALLENGES, ACHIEVEMENTS AND GHOST RACES", 51),
            ("YOU HAVE 3 LIVES PER RUN. GOOD LUCK!", 196),
        )
        for i, (txt, col) in enumerate(lines):
            scr.text_center(y + 2 + i, txt, col, 16)
        scr.text_center(y + 20, "ESC / ENTER - BACK", 240, 16)

    def _draw_title(self):
        scr = self.scr
        cam = self.t * 3.0
        bgmod.draw_sky(scr, "day", 0, VIEW_H)
        bgmod.draw_parallax(scr, "day", cam, 0, VIEW_H, self.t)
        for xx in range(VIEW_W):
            scr.put(xx, VIEW_H - 2, " ", 252, 70)
            if xx % 3 == 0:
                scr.over(xx, VIEW_H - 2, ('"', ",", "'")[xx % 3], 77)
            scr.put(xx, VIEW_H - 1, " ", 252, 94)
        for pt in self.fx.items:
            scr.put(int(round(pt[0])), int(round(pt[1])), pt[5], pt[6])
        lx = (VIEW_W - len(LOGO1[0])) // 2
        for j, row in enumerate(LOGO1):
            scr.text(lx + 1, j + 2, row, 236)
            scr.text(lx, j + 1, row, LOGO1_C[j])
        lx2 = (VIEW_W - len(LOGO2[0])) // 2
        for j, row in enumerate(LOGO2):
            scr.text(lx2 + 1, j + 8, row, 236)
            scr.text(lx2, j + 7, row, LOGO2_C[j])
        sub = "· A TERMINAL PLATFORM ADVENTURE ·  v" + VERSION
        scr.text_center(13, sub, 245)
        start, end = self.title_menu.window()
        self._draw_menu(self.title_menu, 15, 24)
        dx = (self.t * 7.0) % (VIEW_W + 30) - 20
        f = int(self.t * 8) % 2
        sp.blit_bottom(scr, (sp.PLAYER_RUN_A, sp.PLAYER_RUN_B)[f],
                       dx, VIEW_H - 2)
        if self.title_menu.clipped():
            scr.text_center(21, "%d-%d OF %d" % (start + 1, end,
                                                 len(self.title_menu)), 240)
        scr.text_center(VIEW_H - 2, "BEST %06d" % self.hiscore, 240)
        scr.text_center(VIEW_H - 1, HINTS["title"], 240)

    def _draw_levelsel(self):
        scr = self.scr
        bgmod.draw_sky(scr, "night", 0, VIEW_H)
        bgmod.draw_parallax(scr, "night", self.t * 4.0, 0, VIEW_H, self.t)
        menu = self.level_menu
        start, end = menu.window()
        rows = end - start
        x, y = self._panel(72, rows + 12, 141)
        scr.text_center(y + 2, menu.title, 141, 16)
        self._draw_menu(menu, y + 4, 60)
        cur = menu.current()
        lvl = None
        ci = None
        if cur is not None and cur.action.startswith("level:"):
            ci = int(cur.action.split(":", 1)[1])
            lvl = self.levels[ci]
        elif cur is not None and cur.action.startswith("custom:"):
            j = int(cur.action.split(":", 1)[1])
            entry = self.customs[j]
            lvl = Level(entry["name"], entry.get("theme", "day"),
                        entry["rows"])
        base = y + 5 + rows
        if lvl is not None and ci is not None:
            best = self.best_times[ci]
            medal = medal_for(best, PAR_TIMES[ci]) if best is not None else ""
            scr.text_center(base, "BEST %s   MEDAL %s" % (
                _fmt_time(best) if best is not None else "--:--",
                medal or "-"), 178, 16)
            scr.text_center(base + 1, pace_line(PAR_TIMES[ci], _fmt_time),
                            240, 16)
            threats = threat_count(lvl)
            band = ("LOW" if threats <= 7 else
                    "MEDIUM" if threats <= 12 else "HIGH")
            scr.text_center(base + 2, "COINS %d/%d   GEMS %d/%d   THREATS %d (%s)"
                            % (self.best_coins[ci], len(lvl.coins),
                               self.best_gems[ci], len(lvl.gems),
                               threats, band), 226, 16)
            status = ("LOCKED" if ci > self.unlocked else
                      "CLEARED" if self.cleared[ci] else "UNPLAYED")
            done, total = self._challenge_progress(ci)
            scr.text_center(base + 3, "%s   %s   CHALLENGES %d/%d"
                            % (lvl.name, status, done, total), 245, 16)
        elif lvl is not None:
            scr.text_center(base, lvl.name, 245, 16)
            scr.text_center(base + 1, "CUSTOM   COINS %d   GEMS %d   THREATS %d"
                            % (len(lvl.coins), len(lvl.gems),
                               threat_count(lvl)), 226, 16)
        foot = menu.footer
        if menu.clipped():
            foot = "%d-%d OF %d · %s" % (start + 1, end, len(menu), foot)
        scr.text_center(y + rows + 10, foot, 240, 16)
        if self.toast_msg and int((self.toast_msg[2] - self.t) * 6) % 2:
            msg, col, _ = self.toast_msg
            scr.text_center(y + 2, msg, col, 16)

    def _draw_editor(self):
        scr = self.scr
        if self.ed_grid is None:
            self._ed_init()
        theme = THEMES[ED_THEMES[self.ed_theme_i]]
        bgmod.draw_sky(scr, ED_THEMES[self.ed_theme_i], GAME_OY, GAME_H)
        scr.rect_bg(0, 0, VIEW_W, 2, 16)
        tch, tname = ED_TOOLS[self.ed_tool]
        blink = int(self.t * 4) % 2 == 0
        scr.text(1, 0, "EDITOR", 226, 16)
        scr.text(9, 0, "[%s] %s" % (tch.strip() or "_", tname), 226 if blink
                 else 245, 16)
        scr.text(30, 0, "THEME %s" % ED_THEMES[self.ed_theme_i].upper(),
                 178, 16)
        scr.text(VIEW_W - 28, 0, "X %d" % self.ed_cx, 245, 16)
        scr.text(VIEW_W - 20, 0,
                 "SPACE PAINT · X ERASE · E/Q TOOL"[:20], 240, 16)
        scr.text_center(1, "N THEME · ENTER TEST · V SAVE · ESC EXIT",
                        240, 16)
        x0 = self.ed_cam
        for gy in range(20):
            sy = gy + GAME_OY
            for sx in range(VIEW_W):
                gx = x0 + sx
                if gx >= ED_W:
                    break
                ch = self.ed_grid[gy][gx]
                if ch == " ":
                    continue
                if ch == "#":
                    above = (self.ed_grid[gy - 1][gx]
                             if gy > 0 else " ")
                    if above not in ("#", "B"):
                        scr.put(sx, sy, "▀", theme["grass"],
                                theme["ground"])
                    else:
                        scr.put(sx, sy, " ", 252, theme["ground"])
                elif ch == "B":
                    scr.put(sx, sy, "▒", theme["brick_f"], theme["brick_b"])
                elif ch == "-":
                    scr.over(sx, sy, "═", theme["plat"])
                elif ch == "^":
                    scr.put(sx, sy, "▲", theme["spike"], 16)
                elif ch == "~":
                    i = int(self.t * 7 + gx * 1.3) % 3
                    scr.put(sx, sy, ("~", "≈", "-")[i],
                            (226, 208, 202)[i], 52)
                else:
                    col = dict(c=226, g=51, s=214, e=201, f=141, h=214,
                               w=196, t=245, Z=201, b=201, P=46)
                    col["F"] = 226
                    col["*"] = 46
                    scr.put(sx, sy, ch, col.get(ch, 252))
        cx = self.ed_cx - x0
        cy = self.ed_cy + GAME_OY
        if blink:
            for dx_, dy_ in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                scr.put(cx + dx_, cy + dy_, "+", 231)
        pal = "".join(t[0] if t[0].strip() else "_" for t in ED_TOOLS)
        scr.rect_bg(0, VIEW_H - 1, VIEW_W, 1, 16)
        scr.text(1, VIEW_H - 1, pal, 244, 16)
        pos = sum(len(t[0]) for t in ED_TOOLS[:self.ed_tool])
        scr.put(1 + pos, VIEW_H - 1, tch.strip() or "_", 226, 16)
        if self.toast_msg and int((self.toast_msg[2] - self.t) * 6) % 2:
            msg, col, _ = self.toast_msg
            scr.text_center(2, msg, col)

    # ---------------------------------------------------------------- run --
    def attach(self, term_w, term_h):
        self.scr.center_in(term_w, term_h)

