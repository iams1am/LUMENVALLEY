"""Frame-time distribution and live counters for the diagnostics overlay.

This module is deliberately standard-library only: no pygame, no renderer, no
clock.  A front-end feeds it measured milliseconds and asks for a summary; the
tests can therefore prove the percentile and spike rules without a window.

The recorder keeps a bounded rolling window.  A compact window is enough for a
developer overlay and - unlike a growing list - cannot become a memory leak of
its own.
"""

import math
from collections import deque

#: Target frame budget.  Derived from the shipping 60 Hz target.
TARGET_FPS = 60.0
FRAME_BUDGET_MS = 1000.0 / TARGET_FPS
#: A frame slower than twice the budget is a visible hitch, not jitter.
SEVERE_MS = FRAME_BUDGET_MS * 2.0

#: Overlay geometry, shared by the renderer and the test that measures it.
#: The overlay is deliberately invisible to the layout probe, so an explicit
#: size check is the only guard against text running out of its box.  The box
#: is sized to its own content rather than to a guessed number.
OVERLAY_MARGIN = 8
OVERLAY_PAD = 12
OVERLAY_LINE_H = 22

#: A mean frame rate above this reads as healthy; below the second, as broken.
FPS_GOOD = 58.0
FPS_WARN = 45.0


def _finite(value):
    """Return a finite, non-negative float, or ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def percentile(sorted_values, fraction):
    """Nearest-rank percentile of an already sorted sequence.

    ``fraction`` is 0.0-1.0.  Nearest-rank is used because it always returns an
    observed sample and is easy to reason about in a small overlay window.
    """
    if not sorted_values:
        return 0.0
    fraction = max(0.0, min(1.0, float(fraction)))
    if fraction <= 0.0:
        return float(sorted_values[0])
    index = int(math.ceil(fraction * len(sorted_values))) - 1
    index = max(0, min(len(sorted_values) - 1, index))
    return float(sorted_values[index])


class FrameRecorder:
    """A bounded rolling window of ``(frame, update, render)`` millisecond samples."""

    def __init__(self, capacity=600):
        self.capacity = max(1, int(capacity))
        self._samples = deque(maxlen=self.capacity)
        self.total = 0
        self.spikes = 0
        self.severe = 0

    def __len__(self):
        return len(self._samples)

    def add(self, frame_ms, update_ms=0.0, render_ms=0.0):
        """Record one frame.  Invalid frame times are ignored, not clamped."""
        frame = _finite(frame_ms)
        if frame is None:
            return False
        update = _finite(update_ms)
        render = _finite(render_ms)
        self._samples.append((frame, update or 0.0, render or 0.0))
        self.total += 1
        if frame > FRAME_BUDGET_MS:
            self.spikes += 1
        if frame > SEVERE_MS:
            self.severe += 1
        return True

    def clear(self):
        self._samples.clear()
        self.total = 0
        self.spikes = 0
        self.severe = 0

    def samples(self):
        """The current window, oldest first."""
        return list(self._samples)

    def summary(self):
        """Means, percentiles and spike counts for the current window.

        ``frame`` is the whole loop period (present and sleep included), so
        ``update + render`` is always less than it; that inequality is what
        makes the split in the overlay honest rather than a guess.
        """
        if not self._samples:
            return {
                "count": 0, "total": self.total, "fps": 0.0,
                "frame": 0.0, "update": 0.0, "render": 0.0,
                "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0,
                "spikes": self.spikes, "severe": self.severe,
            }
        frames = sorted(sample[0] for sample in self._samples)
        count = len(frames)
        mean_frame = sum(frames) / count
        mean_update = sum(sample[1] for sample in self._samples) / count
        mean_render = sum(sample[2] for sample in self._samples) / count
        return {
            "count": count,
            "total": self.total,
            "fps": (1000.0 / mean_frame) if mean_frame > 0.0 else 0.0,
            "frame": mean_frame,
            "update": mean_update,
            "render": mean_render,
            "p50": percentile(frames, 0.50),
            "p95": percentile(frames, 0.95),
            "p99": percentile(frames, 0.99),
            "max": frames[-1],
            "spikes": self.spikes,
            "severe": self.severe,
        }


def rate_role(fps):
    """``good`` / ``warn`` / ``bad`` for a mean frame rate."""
    if fps >= FPS_GOOD:
        return "good"
    if fps >= FPS_WARN:
        return "warn"
    return "bad"


def tail_role(p99):
    """``good`` / ``warn`` / ``bad`` for the tail of the distribution."""
    if p99 <= FRAME_BUDGET_MS:
        return "good"
    if p99 <= SEVERE_MS:
        return "warn"
    return "bad"


def overlay_lines(summary, counts=None, mem=None):
    """The overlay's text as ``(text, role)`` pairs.

    Built here rather than in the renderer so the strings can be measured by a
    test with the real font: the overlay bypasses the layout probe, which means
    nothing else would ever catch a line that outgrows its box.
    """
    counts = counts or {}
    lines = [
        ("DIAGNOSTICS   F2/F3/` HIDE", "heading"),
        ("FPS %5.1f   FRAME %6.2f ms" % (summary["fps"], summary["frame"]),
         rate_role(summary["fps"])),
        ("UPDATE %5.2f   RENDER %5.2f ms" % (summary["update"],
                                            summary["render"]), "body"),
        ("P50 %5.2f  P95 %5.2f  P99 %5.2f  MAX %5.2f"
         % (summary["p50"], summary["p95"], summary["p99"],
            summary["max"]), tail_role(summary["p99"])),
        ("SPIKES >%.1f ms  %d   >%.1f ms  %d"
         % (FRAME_BUDGET_MS, summary["spikes"], SEVERE_MS,
            summary["severe"]), "hint"),
        ("ENTITIES %d   PARTICLES %d   FRAMES %d"
         % (counts.get("entities", 0), counts.get("particles", 0),
            summary["total"]), "body"),
    ]
    if mem:
        lines.append(("MEM %5.1f MB   PEAK %5.1f MB" % mem, "hint"))
    else:
        lines.append(("MEM N/A (SET LV_DIAG_MEMORY=1)", "hint"))
    return lines


def overlay_box(lines, measure, line_height, screen_w, screen_h):
    """``(x, y, w, h)`` for an overlay sized to its longest line.

    ``measure(text)`` is the renderer's own font metric - pygame's ``size``
    returns ``(width, height)``, so both that and a plain width are accepted.
    The box is exact for the font that will actually draw it, anchored to the
    bottom-left and clamped to the screen; a caller can tell "fits" from "was
    clamped" by comparing ``w`` to the measured width.
    """
    def width_of(text):
        size = measure(text)
        return size[0] if isinstance(size, (tuple, list)) else size

    widest = max([width_of(text) for text, _ in lines] or [0])
    width = min(screen_w - 2 * OVERLAY_MARGIN, widest + 2 * OVERLAY_PAD)
    height = min(screen_h - 2 * OVERLAY_MARGIN,
                 (len(lines) - 1) * OVERLAY_LINE_H + line_height
                 + 2 * OVERLAY_PAD)
    return (OVERLAY_MARGIN, screen_h - OVERLAY_MARGIN - height, width, height)


def _active(items):
    """Count objects that are alive (plain values count as alive)."""
    return sum(1 for item in items or () if getattr(item, "alive", True))


def entity_counts(game):
    """Live entity/particle counts from a simulation object.

    This is intentionally duck-typed: diagnostics must be able to inspect a
    game without importing the simulation or changing any gameplay state.
    """
    enemies = _active(getattr(game, "enemies", ()))
    shots = _active(getattr(game, "shots", ()))
    collectibles = (len(getattr(game, "coins", ()) or ())
                    + len(getattr(game, "gems", ()) or ()))
    movers = len(getattr(game, "movers", ()) or ())
    parts = getattr(game, "parts", None)
    fx = getattr(game, "fx", None)
    particles = (len(getattr(parts, "items", ()) or ())
                 + len(getattr(fx, "items", ()) or ()))
    return {
        "enemies": enemies,
        "shots": shots,
        "collectibles": collectibles,
        "movers": movers,
        "entities": enemies + shots + collectibles + movers,
        "particles": particles,
    }


def memory_mb():
    """``(current, peak)`` MiB from ``tracemalloc``, or ``None`` when off."""
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return None
        current, peak = tracemalloc.get_traced_memory()
    except (ImportError, RuntimeError, ValueError):
        return None
    return current / (1024.0 * 1024.0), peak / (1024.0 * 1024.0)
