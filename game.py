"""Terminal entry point and compatibility facade for Lumenvalley.

The simulation lives in :mod:`lumenvalley.sim`; this module wires it to the legacy
ANSI presentation, the desktop save files and the platform audio module.  Run
with ``python game.py`` for the terminal build, or ``python pygame_view.py``
for the graphical build.
"""

import sys
import time

import sound
import storage
from lumenvalley.constants import (
    ED_THEMES, ED_TOOLS, GAME_H, GAME_OY,
    STEP, THEMES, VERSION, VIEW_H, VIEW_W,
)
from lumenvalley.rendering.terminal import TerminalPresentation
from lumenvalley.sim import Game as _CoreGame
from lumenvalley.timing import FixedStepClock
from render import Screen

HERE = storage.HERE
SAVE_PATH = storage.SAVE_PATH
CUSTOM_PATH = storage.CUSTOM_PATH

# This module is a facade: the desktop front-end and a few tests import these
# names from ``game`` (that is how they were imported before the simulation was
# split out of the monolith).  Listing them keeps the contract explicit and
# stops a linter from deleting a deliberate re-export.
__all__ = [
    "Game", "STEP", "Screen", "FixedStepClock",
    "THEMES", "VERSION", "VIEW_W", "VIEW_H", "GAME_OY", "GAME_H",
    "ED_TOOLS", "ED_THEMES",
    "HERE", "SAVE_PATH", "CUSTOM_PATH",
]


class Game(_CoreGame, TerminalPresentation):
    """Headless simulation plus the terminal renderer and desktop adapters."""

    def __init__(self, seed=None, store=None, audio=None):
        super().__init__(seed=seed,
                         store=store or storage.DesktopStore(),
                         audio=audio or sound)
        self.scr = Screen()


def main():
    # Imported here, not at module level: term.py pulls in msvcrt/termios/tty,
    # which do not exist in a browser, and the pygame/web front-ends import
    # this facade.
    from term import Terminal

    size = Terminal.size()
    if size.columns < VIEW_W or size.lines < VIEW_H:
        print("Terminal too small: need at least %dx%d." % (VIEW_W, VIEW_H))
        print("Resize the window and try again.")
        return 1
    with Terminal() as term:
        game = Game()
        game.attach(size.columns, size.lines)
        prev = set()
        last = time.perf_counter()
        clock = FixedStepClock(STEP)
        running = True
        while running:
            try:
                now = time.perf_counter()
                steps = clock.advance(now - last)
                last = now
                keys = set(term.read_keys())
                edges = keys - prev
                prev = keys
                ran = False
                for _ in range(steps):
                    if not game.step(STEP, keys, edges):
                        running = False
                        break
                    edges = set()
                    ran = True
                game.draw()
                term.write(game.scr.frame())
                term.flush()
                if not ran:
                    sleep_for = clock.sleep_time()
                    if sleep_for > 0:
                        time.sleep(min(sleep_for, 0.01))
            except KeyboardInterrupt:
                return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
