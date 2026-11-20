"""Procedural chiptune loops - one track per level theme.

Each track is a square-wave lead over a sine bass, sequenced in eighth
notes and mixed into a single looping PCM buffer. Rendered once per theme
on demand; consumed by pygame_view through pygame.mixer.
"""

import math
import struct

RATE = 22050

TRACKS = {
    #        bpm  lead (midi, eighths)                    bass
    "day": (132,
            [(72, 2), (76, 2), (79, 2), (76, 2), (81, 2), (79, 2),
             (76, 2), (72, 2), (74, 2), (77, 2), (81, 2), (77, 2),
             (79, 2), (76, 2), (72, 2), (67, 2)],
            [(48, 4), (55, 4), (52, 4), (57, 4),
             (48, 4), (55, 4), (53, 4), (55, 4)]),
    "cave": (96,
            [(69, 3), (0, 1), (72, 2), (71, 4), (69, 2), (0, 2),
             (64, 4), (67, 2), (71, 2), (69, 4), (0, 6)],
            [(45, 8), (41, 8), (43, 8), (40, 8)]),
    "dusk": (112,
            [(74, 2), (77, 2), (81, 2), (79, 2), (77, 2), (74, 2),
             (72, 2), (74, 2), (70, 2), (74, 2), (77, 2), (74, 2),
             (72, 4), (69, 2), (67, 2)],
            [(50, 8), (47, 8), (48, 8), (45, 8)]),
    "dawn": (116,
            [(65, 2), (69, 2), (72, 2), (77, 2), (76, 2), (72, 2),
             (74, 2), (77, 2), (81, 4), (79, 2), (77, 2), (76, 4),
             (72, 2), (69, 2), (72, 4)],
            [(41, 8), (46, 8), (48, 8), (43, 8)]),
    "night": (140,
            [(64, 1), (67, 1), (71, 1), (76, 1), (64, 1), (67, 1),
             (71, 1), (76, 1), (65, 1), (68, 1), (72, 1), (77, 1),
             (65, 1), (68, 1), (72, 1), (77, 1), (67, 1), (71, 1),
             (74, 1), (79, 1), (67, 1), (71, 1), (74, 1), (79, 1),
             (62, 1), (67, 1), (71, 1), (76, 1), (62, 1), (67, 1),
             (71, 1), (76, 1)],
            [(40, 2), (40, 2), (43, 2), (43, 2), (38, 2), (38, 2),
             (40, 2), (40, 2), (40, 2), (40, 2), (43, 2), (43, 2),
             (45, 2), (45, 2), (43, 2), (43, 2)]),
}


def _freq(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _add(buf, start, dur, freq, wave, vol):
    n = min(len(buf) - start, int(dur * RATE))
    if n <= 0:
        return
    ph = 0.0
    pf = freq / RATE
    for i in range(n):
        ph += pf
        fr = ph % 1.0
        if wave == "sq":
            s = 1.0 if fr < 0.5 else -1.0
        else:
            s = math.sin(2.0 * math.pi * fr)
        env = 1.0 - 0.35 * (i / n)
        buf[start + i] += s * vol * env


def render(theme):
    """Return raw mono int16 PCM bytes for the theme's music loop."""
    bpm, lead, bass = TRACKS[theme]
    stepdur = 60.0 / bpm / 2.0
    steps = max(sum(d for _, d in lead), sum(d for _, d in bass))
    total = int(steps * stepdur * RATE)
    buf = [0.0] * total
    pos = 0
    for m, d in lead:
        if m:
            _add(buf, int(pos * stepdur * RATE), d * stepdur,
                 _freq(m), "sq", 0.085)
        pos += d
    pos = 0
    for m, d in bass:
        if m:
            _add(buf, int(pos * stepdur * RATE), d * stepdur,
                 _freq(m) / 2, "sin", 0.22)
        pos += d
    pcm = bytearray()
    for v in buf:
        v = max(-1.0, min(1.0, v))
        pcm += struct.pack("<h", int(v * 32767 * 0.55))
    return bytes(pcm)
