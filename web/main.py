"""Browser entry point (pygbag / Emscripten).

The desktop loop in ``pygame_view.main()`` cannot be reused verbatim: the
browser needs ``await asyncio.sleep(0)`` every frame, must never call
``sys.exit``/``pygame.quit``, has no ``subprocess`` audio, and has to survive
tab switches and blocked autoplay.  Everything gameplay-related still comes
from the same headless core, so this file is loop plumbing only.

``python web/main.py`` also runs on the desktop for debugging (filesystem
saves, no JS bridge).
"""

import asyncio
import os
import sys
import time

import pygame

from lumenvalley.constants import STEP
from lumenvalley.timing import FixedStepClock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame_view  # noqa: E402
from pygame_view import (  # noqa: E402
    Audio, GAME_OY, H, InputState, KEYMAP, PygameGame, W, read_pad,
)

import web_store  # noqa: E402

MAX_FRAME_SECONDS = 0.05        # pause longer than this and we stop catching up


def js_window():
    try:
        import platform as py_platform
    except ImportError:
        return None
    return getattr(py_platform, "window", None)


def js_flag(name, default=False):
    window = js_window()
    if window is None:
        return default
    try:
        return bool(getattr(window, name))
    except Exception:                                  # noqa: BLE001 - JS error
        return default


def js_set(name, value):
    window = js_window()
    if window is None:
        return
    try:
        setattr(window, name, value)
    except Exception:                                  # noqa: BLE001 - JS error
        pass


def browser_fullscreen():
    """True/False from the shell, or None when there is no browser."""
    window = js_window()
    if window is None:
        return None
    try:
        return bool(window.document.fullscreenElement)
    except Exception:                                  # noqa: BLE001 - JS error
        return None


def toggle_fullscreen(screen):
    """Browser fullscreen belongs to the shell, not to SDL."""
    window = js_window()
    if window is not None:
        try:
            if browser_fullscreen():
                window.document.exitFullscreen()
            else:
                window.document.documentElement.requestFullscreen()
            return screen
        except Exception:                              # noqa: BLE001 - JS error
            pass
    flags = 0 if screen.get_flags() & pygame.FULLSCREEN else pygame.FULLSCREEN
    return pygame.display.set_mode((W, H), flags)


async def main():
    pygame.mixer.pre_init(frequency=22050, size=-16, channels=8, buffer=512)
    pygame.init()
    try:
        pygame.joystick.init()
    except pygame.error:
        pass
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Lumenvalley")

    audio = Audio()
    pygame_view.AUDIO = audio

    store = web_store.make_store()
    game = PygameGame(store=store)
    # The shell publishes the OS/browser accessibility preference; the game
    # owns the policy — a player's own choice outranks it, and the settings row
    # says "ON (SYSTEM)" when the preference is what turned motion off.
    game.apply_system_preferences(js_flag("LV_PREFERS_REDUCED_MOTION", False))
    input_state = InputState()
    sim_clock = FixedStepClock(STEP)
    last = time.perf_counter()

    paused = False
    audio_unlocked = audio.ok
    frames = 0
    running = True
    first_frame = True

    while running:
        now = time.perf_counter()
        elapsed = now - last
        frame_ms = elapsed * 1000.0
        last = now
        tapped = set()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                name = KEYMAP.get(ev.key)
                if name:
                    tapped.add(name)
                if ev.key in (pygame.K_F3, pygame.K_F2, pygame.K_BACKQUOTE):
                    game.toggle_diagnostics()
                if ev.key == pygame.K_F11:
                    screen = toggle_fullscreen(screen)
                if not audio_unlocked:
                    # Browsers allow audio only after a real gesture.
                    audio.init()
                    audio_unlocked = audio.ok
                    audio.cur_theme = None
            elif ev.type in (pygame.WINDOWFOCUSLOST, pygame.WINDOWMINIMIZED):
                paused = True
            elif ev.type in (pygame.WINDOWFOCUSGAINED, pygame.WINDOWRESTORED):
                paused = False
                last = time.perf_counter()

        # The shell's mute button writes a JS flag; the game owns the state.
        if js_flag("LV_MUTED", False) != game.muted:
            game.muted = not game.muted
            game.audio.set_enabled(not game.muted)

        # Tab hidden / shell export: flush through the normal save path.
        if js_flag("LV_SAVE_NOW", False):
            js_set("LV_SAVE_NOW", False)
            game._save_score()

        pad = set()
        if pygame.joystick.get_count():
            read_pad(pad)
        keys, edges = input_state.update(pygame.key.get_pressed(),
                                         pad | tapped)

        # Settings only records intent; the browser owns the real fullscreen.
        if getattr(game, "fullscreen_requests", 0):
            game.fullscreen_requests = 0
            screen = toggle_fullscreen(screen)
        real_fullscreen = browser_fullscreen()
        if real_fullscreen is not None:
            # Esc can leave browser fullscreen without telling the game.
            mode = "fullscreen" if real_fullscreen else "window"
            if game.screen_mode != mode:
                game.screen_mode = mode
                game._refresh_menus()

        if paused:
            # Drop accumulated time instead of replaying the whole pause.
            sim_clock.accumulator = 0.0
            steps = 0
        else:
            steps = sim_clock.advance(min(elapsed, MAX_FRAME_SECONDS))

        update_start = time.perf_counter()
        for _ in range(steps):
            if not game.step(STEP, keys, edges):
                running = False
                break
            input_state.consume()
            edges = set()
        update_ms = (time.perf_counter() - update_start) * 1000.0

        audio.update(game)
        render_start = time.perf_counter()
        game.draw_all(screen)
        if paused:
            game.dim(screen, alpha=170)
            game.text(screen, "PAUSED", game.font_big, (240, 240, 250),
                      GAME_OY * 20 + 120)
            game.text(screen, "click the canvas to resume", game.font_small,
                      (200, 200, 215), GAME_OY * 20 + 210)
        # Developer overlay, outside draw_all so normal screens and the layout
        # audit never see it.  It draws over the pause dim so FPS stays visible.
        game.draw_diagnostics(screen)
        render_ms = (time.perf_counter() - render_start) * 1000.0
        pygame.display.flip()
        if not paused:
            # A hidden tab is not a gameplay frame; do not poison the window
            # with the multi-second gap that focus loss produces.
            game.record_frame(frame_ms, update_ms, render_ms)

        if first_frame:
            first_frame = False
            js_set("LV_READY", True)
            if store.save_error:
                js_set("LV_SAVE_ERROR", store.save_error)
        # Exposed for the headless browser check and for debugging.
        js_set("LV_STATE", game.state)
        js_set("LV_MOTION", game.settings.get("motion", 1))
        frames += 1

        # Mandatory yield: pygbag drives the browser main loop from this.
        await asyncio.sleep(0)

    game._save_score()
    if store.save_error:
        print("save warning:", store.save_error, file=sys.stderr)
    return 0


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
