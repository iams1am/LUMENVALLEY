"""Save-store interface and the in-memory implementation.

The simulation talks to a store, never to a filesystem or browser storage.
The desktop adapter lives in ``storage.py``; a future browser adapter only has
to implement the same four methods.
"""

import copy
from typing import Protocol

# Version 1 is the original, implicit schema (no schema_version field).
# Version 2 (Phase 11) adds mastery, secrets and achievements, and stores
# ghosts as versioned records instead of bare frame lists.  Every v1 field is
# still read; missing v2 fields default without blocking the save.
SAVE_SCHEMA_VERSION = 2


class SaveStore(Protocol):
    """The only save API the simulation is allowed to use."""

    schema_version: int

    def load_progress(self):
        """Return parsed progress data (a dict, a bare number, or None)."""

    def save_progress(self, payload):
        """Persist a progress payload; raise or return False on failure."""

    def load_customs(self):
        """Return parsed custom-level data (a dict or None)."""

    def save_customs(self, levels):
        """Persist the list of custom-level entries."""


class MemoryStore:
    """Headless default: keeps saves in memory, never touches disk.

    This is what makes ``lumenvalley.sim.Game()`` safe for tests and for any
    non-desktop host.  Values are copied in and out so callers cannot mutate
    stored state by accident.
    """

    schema_version = SAVE_SCHEMA_VERSION

    def __init__(self, progress=None, customs=None):
        self.progress = copy.deepcopy(progress)
        self.customs = copy.deepcopy(customs)
        self.save_error = None

    def load_progress(self):
        return copy.deepcopy(self.progress)

    def save_progress(self, payload):
        self.progress = copy.deepcopy(payload)
        self.save_error = None
        return True

    def load_customs(self):
        return copy.deepcopy(self.customs)

    def save_customs(self, levels):
        self.customs = {
            "schema_version": SAVE_SCHEMA_VERSION,
            "levels": copy.deepcopy(levels),
        }
        self.save_error = None
        return True
