"""Tiny storage adapter for Lumenvalley's desktop save files.

The desktop build keeps using ``save.json`` and ``custom.json`` next to the
game, exactly as before.  Tests can point ``LV_SAVE_PATH`` and
``LV_CUSTOM_PATH`` at a temporary directory so they never touch real player
data.  All writes go through a temporary file followed by ``os.replace`` so a
crash or full disk cannot leave a half-written JSON document behind.
"""

import json
import os
import sys
import tempfile

from lumenvalley.store import SAVE_SCHEMA_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))


def _env_path(name, default):
    value = os.environ.get(name)
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(HERE, default)


SAVE_PATH = _env_path("LV_SAVE_PATH", "save.json")
CUSTOM_PATH = _env_path("LV_CUSTOM_PATH", "custom.json")


def read_json(path, default=None):
    """Read *path* and return parsed JSON, or *default* when unavailable.

    Missing files are normal (first run).  Corrupt/truncated JSON is moved to
    ``<path>.corrupt`` before returning the default, so the damaged bytes are
    preserved for inspection rather than silently overwritten by the next
    save.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        _warn("cannot parse %s: %s" % (path, exc))
        _quarantine(path)
        return default
    except (OSError, TypeError) as exc:
        _warn("cannot read %s: %s" % (path, exc))
        return default


def write_json(path, data):
    """Atomically write *data* as JSON to *path*.

    Raises ``OSError`` / ``TypeError`` / ``ValueError`` so callers can surface
    a real save failure instead of pretending the write succeeded.
    """
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                               suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _warn(message):
    print("lumenvalley: " + message, file=sys.stderr)


def _quarantine(path):
    target = path + ".corrupt"
    try:
        if os.path.exists(path) and not os.path.exists(target):
            os.replace(path, target)
            _warn("preserved damaged file as %s" % target)
    except OSError as exc:
        _warn("could not preserve %s: %s" % (path, exc))


class DesktopStore:
    """SaveStore implementation backed by the two JSON files.

    Paths are resolved on every call so tests (and future launchers) can
    redirect ``storage.SAVE_PATH`` / ``storage.CUSTOM_PATH`` after import
    without rebuilding the game.  Pass explicit paths to pin a store to a
    directory regardless of module globals.
    """

    schema_version = SAVE_SCHEMA_VERSION

    def __init__(self, save_path=None, custom_path=None):
        self.save_path = save_path
        self.custom_path = custom_path

    def _progress_path(self):
        return self.save_path or SAVE_PATH

    def _customs_path(self):
        return self.custom_path or CUSTOM_PATH

    def load_progress(self):
        return read_json(self._progress_path())

    def save_progress(self, payload):
        write_json(self._progress_path(), payload)
        return True

    def load_customs(self):
        return read_json(self._customs_path(), {})

    def save_customs(self, levels):
        write_json(self._customs_path(), {
            "schema_version": SAVE_SCHEMA_VERSION,
            "levels": levels,
        })
        return True
