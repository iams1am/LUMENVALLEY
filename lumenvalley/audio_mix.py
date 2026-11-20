"""Mix policy: who gets a voice, how loud, and how music changes station.

The desktop mixer is pygame's, but the *decisions* live here so they can be
tested without a sound device and cannot drift between the desktop shell and
the browser (both drive the same ``pygame_view.Audio``).

Three rules this module owns:

1. **The music owns its channels.** Two channels are reserved for music so an
   sfx can never steal the track; the rest carry sfx.
2. **A quiet sound never cuts a loud one.** Voices are allocated by priority
   and a new sound only displaces a lower-priority one.
3. **A cue ducks the music.** Death, fanfare and victory pull the music down
   for their duration instead of talking over it, and a hard theme change is
   a short crossfade rather than a cut.

Nothing here imports pygame or touches a clock: callers pass ``now`` in
seconds, which also makes the timing rules testable.
"""

MUSIC_CHANNELS = 2          # mixer channel 0 and 1 are music; sfx start after
MAX_VOICES = 6              # 8 mixer channels total (see Audio.init)
TOTAL_CHANNELS = MUSIC_CHANNELS + MAX_VOICES

DEDUPE = 0.045              # s: the same sound twice this fast is one sound
FADE_IN = 0.45              # s: incoming track
FADE_OUT = 0.30             # s: outgoing track

# Priority 1 = flavour (jump, coin), 2 = notable (gem, check), 3 = cue.
VOICE_GAIN = {1: 0.50, 2: 0.72, 3: 0.80}
DUCK_GAIN = {1: 1.00, 2: 0.75, 3: 0.45}


def voice_gain(priority):
    """Bus gain for a sounding sfx of this priority."""
    return VOICE_GAIN.get(int(priority), VOICE_GAIN[1])


def duck_gain(priority):
    """Music multiplier while an sfx of this priority is sounding."""
    return DUCK_GAIN.get(int(priority), 1.0)


def smoothstep(t):
    """0..1 with zero slope at both ends - no click on either side of a fade."""
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def crossfade(t):
    """Return ``(outgoing, incoming)`` gains for fade progress ``t`` in 0..1."""
    k = smoothstep(t)
    return 1.0 - k, k


class Voices:
    """The set of sfx currently sounding, with priority-based allocation."""

    def __init__(self, size=MAX_VOICES):
        self.size = int(size)
        self.slots = [None] * self.size      # (name, priority, start, end)

    # -- bookkeeping -------------------------------------------------------
    def prune(self, now):
        for i, slot in enumerate(self.slots):
            if slot is not None and now >= slot[3]:
                self.slots[i] = None
        return self

    def sounding(self, now):
        self.prune(now)
        return [s for s in self.slots if s is not None]

    def clear(self):
        self.slots = [None] * self.size

    # -- policy ------------------------------------------------------------
    def accepts(self, name, priority, now):
        """Can this sound start without cutting something more important?"""
        self.prune(now)
        for slot in self.slots:
            if slot is None:
                continue
            if slot[0] == name and now - slot[2] < DEDUPE:
                return False
        free = any(s is None for s in self.slots)
        if free:
            return True
        return int(priority) > min(s[1] for s in self.slots)

    def slot_for(self, priority):
        """Index to use: a free voice, else the lowest-priority occupied one."""
        free = [i for i, s in enumerate(self.slots) if s is None]
        if free:
            return free[0]
        return min(range(self.size), key=lambda i: (self.slots[i][1], i))

    def start(self, name, priority, now, duration):
        if not self.accepts(name, priority, now):
            return None
        i = self.slot_for(priority)
        self.slots[i] = (name, int(priority), float(now), float(now) + duration)
        return i

    def duck(self, now):
        """Music multiplier for the loudest thing currently sounding."""
        live = self.sounding(now)
        if not live:
            return 1.0
        return min(duck_gain(s[1]) for s in live)
