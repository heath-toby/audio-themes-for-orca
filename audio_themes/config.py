"""Configuration and theme locations for Orca Audio Themes.

Values live in Orca's own per-extension settings store (dconf, under
``/org/gnome/orca/<profile>/extensions/audio-themes/settings`` -- Orca
sanitises the ``audio_themes`` namespace by turning the underscore into a
dash), which the Orca 51 extension system reads and writes. Audio Themes used to
keep them in a private GSettings schema, ``org.gnome.Orca.AudioThemes``;
settings from that schema are imported once, automatically, on first run
-- see ``migrate_legacy_settings``.

The public attributes of ``Config`` are unchanged from the pre-extension
versions, so the hand-written settings dialog in ``config_ui`` works
against either backend without modification.

Themes deliberately live OUTSIDE the extension package -- see THEMES_DIR.
"""

from __future__ import annotations

import json
import logging
import os

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_log = logging.getLogger("orca-audio-themes")

# The private schema Audio Themes used before the Orca 51 extension system.
LEGACY_SCHEMA_ID = "org.gnome.Orca.AudioThemes"

# Bumped when the shape of the stored settings changes. Its presence also
# marks the legacy import as done, so it never runs twice -- including
# when the user has deliberately reset everything back to defaults.
SETTINGS_VERSION = 2
_VERSION_KEY = "settings-version"

XDG_DATA_HOME = os.environ.get(
    "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
)
ORCA_DIR = os.path.join(XDG_DATA_HOME, "orca")

# Themes live here, NOT inside the extension package, and this is load
# bearing. Orca approves a user extension by hashing every file in its
# directory and refuses to load it if anything changed since approval.
# The settings dialog writes themes at runtime -- importing a theme,
# duplicating one, or assigning a sound to a role with the theme editor
# all copy files in. If themes sat inside the package, every one of those
# actions would silently un-approve the extension and Audio Themes would
# simply stop loading on the next Orca start. Keeping them out here means
# the package holds only code, whose hash changes only when we change it.
THEMES_DIR = os.path.join(ORCA_DIR, "audio-themes", "themes")

DEFAULT_ENABLED = True
DEFAULT_ACTIVE_THEME = "default"
DEFAULT_POSITIONAL_AUDIO = True
DEFAULT_VOLUME = 0.8
DEFAULT_PLAY_ON_FOCUS = True
DEFAULT_PLAY_ON_MODE_CHANGE = True
DEFAULT_SPEAK_ROLES = True
DEFAULT_DISABLED_SOUNDS: list[str] = []
DEFAULT_PASSWORD_TYPING_SOUND = ""
DEFAULT_AUDIO_OUTPUT = ""


def _legacy_gsettings() -> Gio.Settings | None:
    """Return the old private-schema Gio.Settings, or None if not installed."""
    user_schema_dir = os.path.join(XDG_DATA_HOME, "glib-2.0", "schemas")
    default_source = Gio.SettingsSchemaSource.get_default()
    try:
        source = Gio.SettingsSchemaSource.new_from_directory(
            user_schema_dir, default_source, False,
        )
    except GLib.Error:
        source = default_source
    if source is None:
        return None
    schema = source.lookup(LEGACY_SCHEMA_ID, True)
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, None)


def migrate_legacy_settings(settings) -> bool:
    """Import settings from the pre-extension GSettings schema, once.

    ``settings`` is the extension's ExtensionSettings. Returns True if
    values were actually imported. Writes the version marker either way,
    so a missing or already-migrated legacy schema costs one lookup at
    most once in the life of the install.
    """
    if settings.get(_VERSION_KEY) is not None:
        return False

    legacy = _legacy_gsettings()
    if legacy is None:
        _log.info(
            "AudioThemes: no legacy %s schema to import from; using defaults.",
            LEGACY_SCHEMA_ID,
        )
        settings.set(_VERSION_KEY, SETTINGS_VERSION)
        return False

    try:
        settings.set("enabled", legacy.get_boolean("enabled"))
        settings.set("active-theme", legacy.get_string("active-theme"))
        settings.set("positional-audio", legacy.get_boolean("positional-audio"))
        settings.set("volume", legacy.get_double("volume"))
        settings.set("play-on-focus", legacy.get_boolean("play-on-focus"))
        settings.set("play-on-mode-change", legacy.get_boolean("play-on-mode-change"))
        settings.set("speak-roles", legacy.get_boolean("speak-roles"))
        settings.set("disabled-sounds", list(legacy.get_strv("disabled-sounds")))
        settings.set("password-typing-sound", legacy.get_string("password-typing-sound"))
        settings.set("audio-output", legacy.get_string("audio-output"))
    except (GLib.Error, TypeError, ValueError) as error:
        # Leave the version marker unset so a later run can try again
        # rather than silently stranding the user on defaults.
        _log.error("AudioThemes: importing legacy settings failed: %s", error)
        return False

    settings.set(_VERSION_KEY, SETTINGS_VERSION)
    _log.info("AudioThemes: imported settings from the legacy %s schema.", LEGACY_SCHEMA_ID)
    return True


class Config:
    """Audio Themes configuration, backed by Orca's per-extension settings."""

    def __init__(self, settings=None):
        self._settings = settings
        self.enabled: bool = DEFAULT_ENABLED
        self.active_theme: str = DEFAULT_ACTIVE_THEME
        self.positional_audio: bool = DEFAULT_POSITIONAL_AUDIO
        self.volume: float = DEFAULT_VOLUME
        self.play_on_focus: bool = DEFAULT_PLAY_ON_FOCUS
        self.play_on_mode_change: bool = DEFAULT_PLAY_ON_MODE_CHANGE
        self.speak_roles: bool = DEFAULT_SPEAK_ROLES
        self.disabled_sounds: list[str] = list(DEFAULT_DISABLED_SOUNDS)
        self.password_typing_sound: str = DEFAULT_PASSWORD_TYPING_SOUND
        self.audio_output: str = DEFAULT_AUDIO_OUTPUT

    @classmethod
    def load(cls, settings=None) -> Config:
        """Read the current settings into a new Config."""
        cfg = cls(settings)
        cfg.reload()
        return cfg

    def reload(self) -> None:
        """Re-read every value from the settings store."""
        settings = self._settings
        if settings is None:
            _log.warning("AudioThemes: no settings store; using defaults.")
            return

        self.enabled = bool(settings.get("enabled", DEFAULT_ENABLED))
        self.active_theme = str(settings.get("active-theme", DEFAULT_ACTIVE_THEME))
        self.positional_audio = bool(settings.get("positional-audio", DEFAULT_POSITIONAL_AUDIO))
        self.volume = float(settings.get("volume", DEFAULT_VOLUME))
        self.play_on_focus = bool(settings.get("play-on-focus", DEFAULT_PLAY_ON_FOCUS))
        self.play_on_mode_change = bool(
            settings.get("play-on-mode-change", DEFAULT_PLAY_ON_MODE_CHANGE)
        )
        self.speak_roles = bool(settings.get("speak-roles", DEFAULT_SPEAK_ROLES))
        self.disabled_sounds = [
            str(name) for name in settings.get("disabled-sounds", DEFAULT_DISABLED_SOUNDS) or []
        ]
        self.password_typing_sound = str(
            settings.get("password-typing-sound", DEFAULT_PASSWORD_TYPING_SOUND)
        )
        self.audio_output = str(settings.get("audio-output", DEFAULT_AUDIO_OUTPUT))

        self.volume = min(max(self.volume, 0.0), 1.0)

    def save(self) -> None:
        """Write every value back to the settings store."""
        settings = self._settings
        if settings is None:
            _log.error("AudioThemes: cannot save, no settings store.")
            return

        settings.set("enabled", bool(self.enabled))
        settings.set("active-theme", str(self.active_theme))
        settings.set("positional-audio", bool(self.positional_audio))
        settings.set("volume", float(self.volume))
        settings.set("play-on-focus", bool(self.play_on_focus))
        settings.set("play-on-mode-change", bool(self.play_on_mode_change))
        settings.set("speak-roles", bool(self.speak_roles))
        settings.set("disabled-sounds", [str(name) for name in self.disabled_sounds])
        settings.set("password-typing-sound", str(self.password_typing_sound))
        settings.set("audio-output", str(self.audio_output))

    @property
    def theme_dir(self) -> str:
        """Absolute path to the active theme directory."""
        return os.path.join(THEMES_DIR, self.active_theme)

    def list_themes(self) -> list[dict]:
        """Return a list of installed themes with metadata."""
        themes = []
        if not os.path.isdir(THEMES_DIR):
            return themes
        for name in sorted(os.listdir(THEMES_DIR)):
            theme_path = os.path.join(THEMES_DIR, name)
            if not os.path.isdir(theme_path):
                continue
            info_path = os.path.join(theme_path, "info.json")
            info = {"name": name, "directory": name, "summary": "", "author": ""}
            if os.path.isfile(info_path):
                try:
                    with open(info_path) as f:
                        data = json.load(f)
                    info.update(data)
                    info["directory"] = name
                except (json.JSONDecodeError, OSError):
                    pass
            themes.append(info)
        return themes

    def list_theme_sounds(self, theme_name: str | None = None) -> list[str]:
        """Return sorted list of sound files in a theme."""
        theme = theme_name or self.active_theme
        theme_path = os.path.join(THEMES_DIR, theme)
        if not os.path.isdir(theme_path):
            return []
        return sorted(
            f for f in os.listdir(theme_path)
            if f.lower().endswith((".wav", ".ogg"))
        )
