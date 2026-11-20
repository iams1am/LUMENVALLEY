"""Browser save adapter: the same four methods as the desktop store.

The simulation never touches ``window`` itself; it only sees ``SaveStore``.
Under Emscripten ``platform`` is pygbag's JS bridge and ``platform.window``
is the real window; under CPython this module falls back to the desktop
store so ``python web/main.py`` stays usable for debugging.
"""

import json

from lumenvalley.store import SAVE_SCHEMA_VERSION

PROGRESS_KEY = "lumenvalley.progress"
CUSTOMS_KEY = "lumenvalley.customs"


def _window():
    try:
        import platform as py_platform  # pygbag's Emscripten bridge
    except ImportError:
        return None
    return getattr(py_platform, "window", None)


class BrowserStore:
    """``localStorage``-backed save store with explicit failure reporting."""

    schema_version = SAVE_SCHEMA_VERSION

    def __init__(self, window=None):
        self.window = window if window is not None else _window()
        self.save_error = None
        self.storage_ok = self.window is not None

    # ------------------------------------------------------------ helpers --
    def _get(self, key):
        if not self.storage_ok:
            return None
        try:
            value = self.window.localStorage.getItem(key)
        except Exception as exc:                      # noqa: BLE001 - JS error
            self.save_error = "browser storage unavailable: %s" % exc
            return None
        if value is None:
            return None
        try:
            return json.loads(value)
        except ValueError:
            self.save_error = "stored save is not valid JSON; ignored"
            return None

    def _set(self, key, payload):
        if not self.storage_ok:
            self.save_error = "browser storage unavailable"
            return False
        try:
            self.window.localStorage.setItem(key, json.dumps(payload))
        except Exception as exc:                      # noqa: BLE001 - JS error
            # Usually QuotaExceededError (private mode / full disk).
            self.save_error = "could not save in this browser: %s" % exc
            return False
        self.save_error = None
        return True

    # ----------------------------------------------------------- protocol --
    def load_progress(self):
        return self._get(PROGRESS_KEY)

    def save_progress(self, payload):
        return self._set(PROGRESS_KEY, payload)

    def load_customs(self):
        return self._get(CUSTOMS_KEY)

    def save_customs(self, levels):
        return self._set(CUSTOMS_KEY, {
            "schema_version": SAVE_SCHEMA_VERSION,
            "levels": levels,
        })

    # ------------------------------------------------------------- import --
    def export_text(self):
        """One JSON document holding both progress and custom levels."""
        return json.dumps({
            "format": "lumenvalley-save",
            "schema_version": SAVE_SCHEMA_VERSION,
            "progress": self.load_progress(),
            "customs": self.load_customs(),
        }, sort_keys=True)

    def import_text(self, text):
        """Replace both records from an exported document.

        Returns ``(ok, message)``.  Nothing is written unless the document
        parses and carries the expected format marker, so a bad paste cannot
        destroy a working save.
        """
        try:
            data = json.loads(text)
        except ValueError:
            return False, "That is not a Lumenvalley save file."
        if not isinstance(data, dict) or data.get("format") != "lumenvalley-save":
            return False, "Unrecognised save format."
        if data.get("schema_version", 0) > SAVE_SCHEMA_VERSION:
            return False, "That save comes from a newer version."
        if not self._set(PROGRESS_KEY, data.get("progress")):
            return False, self.save_error or "Could not write progress."
        if data.get("customs") is not None:
            if not self._set(CUSTOMS_KEY, data.get("customs")):
                return False, self.save_error or "Could not write levels."
        return True, "Save imported."


def make_store():
    """Browser storage when available, desktop files otherwise."""
    window = _window()
    if window is not None:
        return BrowserStore(window)
    from storage import DesktopStore
    return DesktopStore()
