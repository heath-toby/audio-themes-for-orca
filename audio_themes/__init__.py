"""Orca Audio Themes -- sounds on focus changes and mode transitions.

Orca 51 user extension. Install to $XDG_DATA_HOME/orca/extensions/ and
approve with ``orca --approve-extension audio_themes``; Orca re-checks
the hash of every file in the package on each start, so re-approve after
editing. Sound themes live outside the package for exactly that reason --
see ``config.THEMES_DIR``.
"""

__version__ = "2.0.0"

import logging

from orca import gsettings_registry
from orca.extension import Extension

# Import submodules as `from .module import name`, never as
# `from . import module`. The loader imports us as
# orca_user_extension.<name> but never creates `orca_user_extension`
# itself, and `from . import module` goes through __import__ with the
# full dotted path, which insists on importing that absent grandparent.
from .config import Config, migrate_legacy_settings
from .focus_interceptor import install as _install_patches
from .focus_interceptor import open_settings as _open_settings
from .focus_interceptor import uninstall as _uninstall_patches

_log = logging.getLogger("orca-audio-themes")

# Where Orca keeps per-extension settings: schema "extensions", one
# relocatable path per extension namespace, all under the single key
# "settings". The namespace is the extension's source id -- the name of
# this package -- which is the last component of __name__ once the
# loader has imported us as orca_user_extension.<namespace>.
_SETTINGS_SCHEMA = "extensions"
_SETTINGS_KEY = "settings"
_NAMESPACE = gsettings_registry.GSettingsRegistry.sanitize_gsettings_path(
    __name__.rsplit(".", 1)[-1]
)


class AudioThemes(Extension):
    """Plays a distinct sound as focus moves between kinds of control."""

    GROUP_LABEL = "Audio Themes"
    DESCRIPTION = "Plays a distinct sound when focus moves between kinds of control, and on browse and focus mode changes, with optional 2D positional audio."
    VERSION = "2.0.0"  # keep in sync with __version__; read by AST, must be a literal
    AUTHOR = "Toby"

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        # Kept alive deliberately: the change signal stops firing once
        # the Gio.Settings object is garbage-collected.
        self._watch = None

    def _get_commands(self) -> list:
        """Returns this extension's keyboard commands."""

        from orca import command_manager, keybindings

        keybinding = keybindings.KeyBinding("a", keybindings.ORCA_CTRL_MODIFIER_MASK)
        return [
            command_manager.KeyboardCommand(
                name="audioThemesSettings",
                function=_open_settings,
                group_label=self.GROUP_LABEL,
                description="Open Audio Themes settings",
                desktop_keybinding=keybinding,
                laptop_keybinding=keybinding,
            )
        ]

    # get_preferences() is deliberately not overridden. Orca's generated
    # preferences dialog can only render the declarative kinds -- it
    # cannot host the Preview buttons the theme editor is built around,
    # nor the theme import/export and per-role sound choosers, and there
    # is no hook to substitute a custom dialog for it. Declaring
    # preferences would produce a second, poorer settings dialog beside
    # the real one. By declaring none, the Settings button in Orca
    # Preferences -> User Extensions stays inactive and Orca+Ctrl+A
    # remains the single place Audio Themes is configured.

    # --- Lifecycle ------------------------------------------------------

    def on_ready(self) -> None:
        """Imports legacy settings if needed, then applies the patches."""

        self._start()

    def on_enabled(self) -> None:
        """Applies the patches after a reload."""

        self._start()

    def on_disabled(self) -> None:
        """Removes the patches and stops the audio players."""

        self._stop()

    def on_shutdown(self) -> None:
        """Deliberately does nothing.

        Orca runs shutdown hooks in a daemon thread with a timeout, but
        tearing this extension down means GLib source removal, GStreamer
        state changes and un-monkey-patching, all of which are main-thread
        work. Doing it off-thread risks a warning, an assertion or a hung
        exit -- and buys nothing, because the process is exiting anyway.
        on_disabled, which Orca does call on the main thread, still does
        the real teardown when the extension is disabled or reloaded.
        """

    def _start(self) -> None:
        if self._config is not None:
            return
        migrate_legacy_settings(self.settings)
        self._config = Config.load(self.settings)
        self._watch_settings()
        _install_patches(self._config)

    def _stop(self) -> None:
        _uninstall_patches()
        self._watch = None
        self._config = None

    # --- Reacting to settings changes -----------------------------------

    def _watch_settings(self) -> None:
        """Re-read settings whenever the store changes.

        The dialog applies its own changes as it saves, but a write made
        any other way -- dconf, a second profile -- would otherwise not
        be noticed until Orca restarts.
        """
        if self._watch is not None:
            return
        try:
            registry = gsettings_registry.get_registry()
            gs = registry.get_settings(
                _SETTINGS_SCHEMA,
                registry.get_active_profile(),
                f"extensions/{_NAMESPACE}",
            )
            if gs is None:
                _log.warning("AudioThemes: no settings object to watch; changes need a reload.")
                return
            gs.connect(f"changed::{_SETTINGS_KEY}", self._on_settings_key_changed)
            self._watch = gs
        except Exception as error:  # pylint: disable=broad-exception-caught
            _log.warning(
                "AudioThemes: could not watch settings (%s); changes need a reload.", error
            )

    def _on_settings_key_changed(self, _settings, _key) -> None:
        if self._config is not None:
            self._config.reload()
