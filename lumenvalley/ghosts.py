"""Versioned ghost-replay payloads.

The simulation records one player position every ``GHOST_SAMPLE`` fixed steps,
so a ghost is a 20 Hz polyline.  That sample rate is part of the format: the
renderer interpolates between frames using ``GHOST_HZ`` and must not guess a
different rate.  A ghost entry is either

* a legacy bare list of ``[x, y, face]`` frames (implicit format 1), or
* a versioned mapping ``{"format": 2, "frames": [...], "time": seconds}``.

Version 2 adds the completion time and an explicit format marker.  Unknown
newer formats are ignored by the reader (and preserved by the save writer, see
``Game.ghost_quarantine``) instead of being interpreted as coordinates.
"""

import math

from lumenvalley.constants import STEP

#: Current ghost payload format.  Format 1 is the original bare list.
GHOST_FORMAT = 2
#: Frames recorded per sampled position.  Three fixed steps at 60 Hz = 20 Hz.
GHOST_SAMPLE = 3
#: Playback sample rate, derived from the one authoritative fixed step.
GHOST_HZ = 1.0 / (STEP * GHOST_SAMPLE)
#: Guard against a corrupt save asking the renderer to allocate forever.
MAX_GHOST_FRAMES = 30000


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and abs(number) <= 1000000 else None


def clean_frames(raw):
    """Return validated ``[x, y, face]`` frames, or ``[]`` when unusable.

    Bad individual frames are dropped rather than destroying an otherwise
    readable ghost; the renderer only needs a monotonic-enough polyline.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for frame in raw[:MAX_GHOST_FRAMES]:
        if not isinstance(frame, (list, tuple)) or len(frame) < 3:
            continue
        x = _finite(frame[0])
        y = _finite(frame[1])
        if x is None or y is None:
            continue
        try:
            face = -1 if int(frame[2]) < 0 else 1
        except (TypeError, ValueError):
            face = 1
        out.append([round(x, 3), round(y, 3), face])
    return out


def sample_trace(trace):
    """Convert a per-frame simulation trace into stored ghost frames.

    The slicing rule and ``GHOST_HZ`` are two halves of the same decision: the
    simulation emits one trace row per fixed step, so every ``GHOST_SAMPLE``
    rows is exactly ``1 / GHOST_HZ`` seconds.  Change one and the ghost's speed
    changes; ``tests/test_mastery.py`` pins them together.
    """
    if not isinstance(trace, (list, tuple)):
        return []
    return clean_frames(trace[::GHOST_SAMPLE])


def encode_ghost(frames, time=None, level=None):
    """Build a format-2 ghost record from raw frames."""
    payload = {
        "format": GHOST_FORMAT,
        "frames": clean_frames(frames),
        "time": _finite(time),
    }
    if level is not None:
        payload["level"] = int(level)
    return payload


def decode_ghost(raw):
    """Return ``{"format", "frames", "time", "level"}`` or ``None``.

    A bare list is accepted as the original format 1.  A mapping whose
    ``format`` is newer than this build is deliberately rejected: playing it as
    coordinates could draw nonsense, and silently rewriting it would destroy
    data written by a newer build.
    """
    if isinstance(raw, (list, tuple)):
        frames = clean_frames(raw)
        if not frames:
            return None
        return {"format": 1, "frames": frames, "time": None, "level": None}
    if not isinstance(raw, dict):
        return None
    try:
        version = int(raw.get("format", 1))
    except (TypeError, ValueError):
        return None
    if version < 1 or version > GHOST_FORMAT:
        return None
    frames = clean_frames(raw.get("frames"))
    if not frames:
        return None
    level = raw.get("level")
    try:
        level = int(level) if level is not None else None
    except (TypeError, ValueError):
        level = None
    return {
        "format": version,
        "frames": frames,
        "time": _finite(raw.get("time")),
        "level": level,
    }


def playhead(frames, elapsed):
    """Interpolated ``(x, y, face)`` at *elapsed* seconds, or ``None``.

    Both renderers draw the ghost through this function so the sampling rate,
    the interpolation and the end-of-run behaviour cannot drift apart.
    ``None`` means "nothing to draw yet" (no ghost, a negative or non-finite
    clock, or a run that has outlived the recording).
    """
    if not frames:
        return None
    try:
        elapsed = float(elapsed)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(elapsed) or elapsed < 0:
        return None
    ft = elapsed * GHOST_HZ
    index = int(ft)
    last = len(frames) - 1
    if index > last:
        return None
    frac = ft - index
    try:
        x0, y0, face = frames[index][0], frames[index][1], frames[index][2]
        x1, y1 = frames[min(index + 1, last)][0], frames[min(index + 1, last)][1]
    except (IndexError, TypeError, ValueError):
        return None
    return (x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac, face)


def is_future_ghost(raw):
    """True when *raw* is a mapping from a newer ghost format."""
    if not isinstance(raw, dict):
        return False
    try:
        return int(raw.get("format", 1)) > GHOST_FORMAT
    except (TypeError, ValueError):
        return False
