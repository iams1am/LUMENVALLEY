"""Tiny chiptune SFX engine.

Synthesizes square-wave WAV blobs in memory and plays them through the
platform's built-in player: winsound on Windows, afplay/aplay/paplay
elsewhere. Higher-priority sounds interrupt lower ones; equal/lower are
dropped while something is playing. Set LV_MUTE=1 to force silence.
"""

import io
import math
import os
import struct
import tempfile
import time

# subprocess/shutil are imported lazily: the browser has no subprocess module,
# and the pygame/web front-ends import this file for its SPECS table only.

RATE = 22050

_env_muted = os.environ.get("LV_MUTE") == "1"
_enabled = not _env_muted
_player = None
_checked = False
_proc = None
_snd = {}
_cur = ("", 0.0, 0)


def _note(freq, dur, wave="square", vol=0.3):
    n = int(RATE * dur)
    out = bytearray()
    ph = 0.0
    for i in range(n):
        ph += freq / RATE
        fr = ph % 1.0
        if wave == "square":
            s = 1.0 if fr < 0.5 else -1.0
        elif wave == "saw":
            s = 2.0 * fr - 1.0
        else:
            s = math.sin(2.0 * math.pi * fr)
        v = vol * (1.0 - 0.5 * i / n)
        out += struct.pack("<h", int(max(-1.0, min(1.0, s)) * v * 32767))
    return bytes(out)


SPECS = {
    "jump": lambda: _note(330, 0.05) + _note(494, 0.05) + _note(659, 0.09),
    "dash": lambda: _note(720, 0.04) + _note(520, 0.05) + _note(360, 0.07),
    "coin": lambda: _note(988, 0.06) + _note(1319, 0.13),
    "gem": lambda: _note(659, 0.07) + _note(831, 0.07)
                    + _note(988, 0.07) + _note(1319, 0.18),
    "stomp": lambda: _note(392, 0.08) + _note(131, 0.12),
    "spring": lambda: _note(262, 0.06) + _note(523, 0.06)
                      + _note(784, 0.06) + _note(1047, 0.12),
    "death": lambda: _note(440, 0.15) + _note(349, 0.15)
                     + _note(262, 0.15) + _note(175, 0.30),
    "check": lambda: _note(1047, 0.08) + _note(1568, 0.20),
    "oneup": lambda: _note(523, 0.06) + _note(659, 0.06) + _note(784, 0.06)
                     + _note(1047, 0.06) + _note(1319, 0.16),
    "star": lambda: _note(784, 0.06) + _note(988, 0.06)
                    + _note(1175, 0.06) + _note(1568, 0.16),
    "resonance": lambda: _note(1047, 0.05, wave="sine", vol=0.24)
                         + _note(1319, 0.05, wave="sine", vol=0.24)
                         + _note(1760, 0.12, wave="sine", vol=0.22),
    "bosshit": lambda: _note(98, 0.22, wave="saw") + _note(196, 0.12),
    "fanfare": lambda: _note(523, 0.12) + _note(523, 0.12)
                       + _note(523, 0.12) + _note(659, 0.34),
    "win": lambda: _note(523, 0.12) + _note(659, 0.12) + _note(784, 0.12)
                   + _note(1047, 0.26) + _note(784, 0.12) + _note(1047, 0.45),
}

PRI = {"death": 3, "fanfare": 3, "win": 3, "oneup": 2, "bosshit": 2,
       "gem": 2, "check": 2, "star": 2, "resonance": 2,
       "jump": 1, "coin": 1, "stomp": 1, "spring": 1,
       "dash": 1}


def _wav_bytes(pcm):
    buf = io.BytesIO()
    n = len(pcm)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", n + 36))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", n))
    buf.write(pcm)
    return buf.getvalue()


def _ensure_player():
    global _player, _checked
    if _checked:
        return
    _checked = True
    if os.name == "nt":
        try:
            import winsound  # noqa: F401
            _player = "winsound"
            return
        except ImportError:
            pass
    try:
        import shutil
    except ImportError:
        shutil = None
    if shutil is not None:
        for cmd in (("afplay",), ("aplay", "-q"), ("paplay",)):
            if shutil.which(cmd[0]):
                _player = cmd
                return
    _player = False


def _build(name):
    pcm = SPECS[name]()
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="cd_")
    with os.fdopen(fd, "wb") as f:
        f.write(_wav_bytes(pcm))
    return path, len(pcm) / 2.0 / RATE


def set_enabled(on):
    global _enabled
    _enabled = bool(on) and not _env_muted


def play(name):
    global _proc, _cur
    if not _enabled or name not in SPECS:
        return
    _ensure_player()
    if not _player:
        return
    now = time.monotonic()
    _, pend, pprio = _cur
    if now < pend and PRI[name] < pprio:
        return
    try:
        if name not in _snd:
            _snd[name] = _build(name)
        payload, dur = _snd[name]
        if _player == "winsound":
            import winsound
            winsound.PlaySound(payload,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            import subprocess
            if _proc is not None and _proc.poll() is None:
                try:
                    _proc.kill()
                except OSError:
                    pass
            _proc = subprocess.Popen(list(_player) + [payload],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
    except OSError:
        return
    _cur = (name, now + dur + 0.02, PRI[name])
