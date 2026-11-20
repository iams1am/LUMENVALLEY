"""Screen-independent UI model: menus, focus rules and type minimums.

The simulation owns *what* a menu contains and which row is focused; the
front-ends own *how* it looks.  Nothing here imports pygame, ANSI codes or a
terminal, so the same menus drive the browser build, the desktop window and the
legacy terminal, and they can be unit-tested headlessly.

Layout rules that the front-ends must obey live here as numbers rather than
prose, so ``tools/check_ui.py`` can measure them (the same approach Phase 4
took for colour):

* body text is at least ``MIN_BODY_PX`` logical pixels tall, which is
  ``at_720(MIN_BODY_PX)`` real pixels in a 720p window;
* a focused row must be visibly different from an unfocused one - a filled
  focus bar, not just a different colour (colour alone fails for colour-blind
  players and is invisible in screenshots at 720p);
* every row in a scrolling list stays inside its panel.
"""

# ------------------------------------------------------------------ type --
# The game surface is a fixed 1600x480 logical canvas (10:3).  A 1280x720
# window is width-limited, so the canvas is shown at 0.8x: a 20 px logical glyph
# is 16 real pixels on screen.  Getting this factor wrong is how "legible at
# 720p" claims quietly become false, so it is stated here and measured.
LOGICAL_H = 480
LOGICAL_W = 1600
REFERENCE_W = 1280
REFERENCE_H = 720
SCALE_720 = float(REFERENCE_W) / LOGICAL_W     # 0.8, width-limited


def at_720(logical_px):
    """Return how tall ``logical_px`` is in a 1280x720 window (0.8x)."""
    return logical_px * SCALE_720


#: Smallest acceptable body/UI text, in logical pixels (16 px at 720p).
MIN_BODY_PX = 20
#: Smallest acceptable footer hint, in logical pixels (14 px at 720p).
MIN_HINT_PX = 17
#: Smallest acceptable heading, in logical pixels (20 px at 720p).
MIN_HEADING_PX = 25
#: A body glyph must still be at least this many real pixels at 720p.
MIN_BODY_REAL_720 = 16.0
#: WCAG AA for body text against its background.
MIN_CONTRAST = 4.5
#: WCAG AA for large/secondary text (hints, values).
MIN_CONTRAST_HINT = 3.0
#: Height of one menu row, in logical pixels.
ROW_H = 44
#: A row must not crowd its neighbour.
MIN_ROW_GAP = 4
#: How many rows a scrolling list shows before it scrolls.
MENU_ROWS = 7


# --------------------------------------------------------------- menus --
class Item:
    """One selectable row.

    ``action`` is a stable identifier the simulation dispatches on; display
    strings are never used as logic.  ``value`` is right-aligned detail
    (a best time, ON/OFF), ``note`` is a short left-side hint.
    """

    __slots__ = ("label", "action", "value", "enabled", "note")

    def __init__(self, label, action=None, value="", enabled=True, note=""):
        self.label = label
        self.action = action if action is not None else label
        self.value = value
        self.enabled = enabled
        self.note = note

    def __repr__(self):
        state = "" if self.enabled else " disabled"
        return "<Item %r -> %s%s>" % (self.label, self.action, state)


class Menu:
    """A list of rows plus the focus index.

    Focus never rests on a disabled row: ``move()`` skips them and wraps, so a
    keyboard-only player cannot get stuck on a locked level or an unavailable
    option.
    """

    def __init__(self, title, items=(), index=0, footer="", rows=None):
        self.title = title
        self.items = list(items)
        self.footer = footer
        self.rows = rows or MENU_ROWS
        self.index = 0
        self.select(index)

    # -- queries ---------------------------------------------------------
    def __len__(self):
        return len(self.items)

    def current(self):
        if not self.items:
            return None
        return self.items[self.index]

    def enabled_indices(self):
        return [i for i, it in enumerate(self.items) if it.enabled]

    def window(self):
        """Return ``(start, end)`` keeping the focus inside a scrolling list."""
        n = len(self.items)
        if n <= self.rows:
            return 0, n
        start = min(max(0, self.index - self.rows + 1), n - self.rows)
        return start, start + self.rows

    def visible_items(self):
        start, end = self.window()
        return self.items[start:end]

    def clipped(self):
        """True when the list needs scroll indicators."""
        return len(self.items) > self.rows

    # -- mutation --------------------------------------------------------
    def index_of(self, action):
        """Index of the first row with *action*, or ``None``."""
        for i, item in enumerate(self.items):
            if item.action == action:
                return i
        return None

    def select_action(self, action):
        """Focus the row with *action*; leave the focus alone if absent."""
        i = self.index_of(action)
        if i is None:
            return self.index
        return self.select(i)

    def select(self, index):
        """Focus ``index``, or the nearest enabled row (stable if all are off)."""
        live = self.enabled_indices()
        if not live:
            self.index = max(0, min(index, len(self.items) - 1))
            return self.index
        if index in live:
            self.index = index
            return self.index
        # Nearest enabled row by distance, preferring forward.
        self.index = min(live, key=lambda i: (abs(i - index), i < index))
        return self.index

    def move(self, delta):
        """Move the focus by ``delta`` enabled rows, wrapping."""
        live = self.enabled_indices()
        if not live or delta == 0:
            return self.index
        if self.index not in live:
            self.select(self.index)
        pos = live.index(self.index)
        self.index = live[(pos + delta) % len(live)]
        return self.index

    def set_items(self, items, index=None):
        """Replace the rows, keeping (or clamping) the focus."""
        self.items = list(items)
        self.select(self.index if index is None else index)
        return self.index


# ------------------------------------------------------------- screens --
#: Footer hints per screen.  Kept here so both front-ends say the same thing.
# Kept short enough to sit inside the narrowest panel (pause, 520 px at the
# shipping resolution); tools/check_ui.py fails a hint that leaves its panel.
HINTS = {
    "title": "UP/DOWN SELECT   ENTER CONFIRM",
    "levels": "UP/DOWN SELECT   ENTER PLAY   ESC BACK",
    "trials": "UP/DOWN SELECT   ENTER RACE   ESC BACK",
    "pause": "ARROWS + ENTER   ESC RESUME",
    "settings": "UP/DOWN SELECT   <-/-> CHANGE   ESC BACK",
    "help": "ESC / ENTER BACK",
    "credits": "ESC / ENTER BACK",
    "results": "UP/DOWN SELECT   ENTER CONFIRM",
    "records": "UP/DOWN READ   ESC BACK",
    "confirm": "UP/DOWN SELECT   ENTER CONFIRM   ESC CANCEL",
    "complete": "ENTER CONTINUE",
    "gameover": "ENTER TITLE",
    "victory": "ENTER TITLE",
}
