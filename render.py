"""Flicker-free frame renderer.

A Screen is a fixed-size grid of (char, fg, bg) cells. Each frame is built
off-screen, then flushed with a single stdout write: the cursor is homed and
the whole frame overwritten (no clear -> no flicker). Color escape codes are
emitted only when the color actually changes, keeping frames small."""

from lumenvalley.constants import VIEW_W, VIEW_H

# 256-color palette indices used across the game.
FG_DEFAULT = 252


class Screen:
    def __init__(self, width=VIEW_W, height=VIEW_H):
        self.w = width
        self.h = height
        self.cells = [[(" ", FG_DEFAULT, None) for _ in range(width)]
                      for _ in range(height)]
        self.off_x = 0   # margins used to center the view in big terminals
        self.off_y = 0

    def clear(self, bg=None):
        blank = (" ", FG_DEFAULT, bg)
        for row in self.cells:
            for x in range(self.w):
                row[x] = blank

    def put(self, x, y, ch, fg=FG_DEFAULT, bg=None):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.cells[y][x] = (ch, fg, bg)

    def over(self, x, y, ch, fg):
        """Draw a char keeping whatever background is already there."""
        if 0 <= x < self.w and 0 <= y < self.h:
            _, _, bg = self.cells[y][x]
            self.cells[y][x] = (ch, fg, bg)

    def text(self, x, y, s, fg=FG_DEFAULT, bg=None):
        for i, ch in enumerate(s):
            self.put(x + i, y, ch, fg, bg)

    def text_center(self, y, s, fg=FG_DEFAULT, bg=None):
        self.text((self.w - len(s)) // 2, y, s, fg, bg)

    def rect_bg(self, x, y, w, h, bg):
        for yy in range(y, y + h):
            if 0 <= yy < self.h:
                row = self.cells[yy]
                for xx in range(x, x + w):
                    if 0 <= xx < self.w:
                        ch, fg, _ = row[xx]
                        row[xx] = (ch, fg, bg)

    def center_in(self, term_w, term_h):
        self.off_x = max(0, (term_w - self.w) // 2)
        self.off_y = max(0, (term_h - self.h) // 2)

    def frame(self):
        """Build the full frame as one ANSI string."""
        out = ["\x1b[H"]
        cur_fg = cur_bg = object()  # sentinel: force first color emit
        pad_top = "\r\n" * self.off_y
        if pad_top:
            out.append(pad_top)
        margin = " " * self.off_x
        for y, row in enumerate(self.cells):
            if y:
                out.append("\r\n")
            if margin:
                out.append("\x1b[0m")
                out.append(margin)
                cur_fg = cur_bg = object()
            for ch, fg, bg in row:
                if fg != cur_fg or bg != cur_bg:
                    if bg is None:
                        out.append("\x1b[0m\x1b[38;5;%dm" % fg)
                    else:
                        out.append("\x1b[38;5;%d;48;5;%dm" % (fg, bg))
                    cur_fg, cur_bg = fg, bg
                out.append(ch)
        out.append("\x1b[0m")
        return "".join(out)
