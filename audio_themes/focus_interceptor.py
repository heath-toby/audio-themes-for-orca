"""Monkey-patches for Orca's focus and mode-change systems.

Intercepts focus changes to play role-appropriate sounds, and intercepts
mode transitions to play focus/browse mode sounds.  All patches are
reversible via uninstall().
"""

from __future__ import annotations

import logging
import os

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

from orca import command_manager, focus_manager, document_presenter
from orca import speech_generator
from orca.scripts.web import speech_generator as web_speech_generator
from orca.scripts import default as default_script
from orca.scripts.web import script as web_script
from orca.ax_object import AXObject
from orca.ax_utilities import AXUtilities

from .config import Config, THEMES_DIR
from .role_map import ROLE_TO_SOUND, MODE_SOUNDS
from .sound_player import (get_player, get_overlay_player, get_screen_size,
                           reset_players, set_output_device, move_orca_streams)

_log = logging.getLogger("orca-audio-themes")

# Module state
_config: Config | None = None
_installed: bool = False
_sound_played_for_focus: bool = False  # True when a theme sound was just played

# Original methods (saved for uninstall)
_orig_set_locus: object = None
_orig_set_presentation_mode: object = None
_orig_enable_sticky_focus: object = None
_orig_enable_sticky_browse: object = None
_orig_generate_accessible_role: object = None
_orig_web_generate_accessible_role: object = None
_orig_set_active_window: object = None
_orig_on_text_inserted: object = None
_orig_on_text_deleted: object = None
_orig_web_on_text_inserted: object = None
_orig_web_on_text_deleted: object = None
_orig_on_showing_changed: object = None
_orig_web_on_showing_changed: object = None


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _get_screen_position(obj: Atspi.Accessible) -> tuple[int, int]:
    """Return (centre_x, centre_y) of obj in screen coordinates, or (-1, -1)."""
    if not AXObject.supports_component(obj):
        return -1, -1
    try:
        rect = Atspi.Component.get_extents(obj, Atspi.CoordType.SCREEN)
    except Exception:
        return -1, -1
    if rect is None or rect.width <= 0:
        return -1, -1
    cx = rect.x + rect.width // 2
    cy = rect.y + rect.height // 2
    return cx, cy


def _compute_position(obj: Atspi.Accessible) -> tuple[float, float]:
    """Compute (pan, elevation) from obj's screen position.

    pan: -1.0 (left) to 1.0 (right)
    elevation: -1.0 (bottom) to 1.0 (top)
    """
    cx, cy = _get_screen_position(obj)
    if cx < 0:
        return 0.0, 0.0

    sw, sh = get_screen_size()
    if sw <= 0 or sh <= 0:
        return 0.0, 0.0

    pan = max(-1.0, min(1.0, (2.0 * cx / sw) - 1.0))
    # Y axis is inverted: 0=top of screen, sh=bottom
    elevation = max(-1.0, min(1.0, 1.0 - (2.0 * cy / sh)))
    return pan, elevation


# ---------------------------------------------------------------------------
# Sound file resolution (with default theme fallback)
# ---------------------------------------------------------------------------

def _resolve_sound_path(sound_file: str) -> str | None:
    """Return the path to a sound file, falling back to the default theme."""
    if _config is None:
        return None
    # Try active theme first
    theme_dir = _config.theme_dir
    if theme_dir:
        path = os.path.join(theme_dir, sound_file)
        if os.path.isfile(path):
            return path
    # Fall back to default theme
    default_path = os.path.join(THEMES_DIR, "default", sound_file)
    if os.path.isfile(default_path):
        return default_path
    return None


# ---------------------------------------------------------------------------
# Special property detection
# ---------------------------------------------------------------------------

_CONTAINER_ROLES = frozenset([
    Atspi.Role.LIST,
    Atspi.Role.LIST_BOX,
    Atspi.Role.TREE,
    Atspi.Role.TREE_TABLE,
    Atspi.Role.COMBO_BOX,
    Atspi.Role.MENU,
    Atspi.Role.PAGE_TAB_LIST,
])


def _find_container_and_index(obj: Atspi.Accessible) -> tuple[Atspi.Accessible | None, int]:
    """Walk up from obj to find the nearest list/tree container and the child index within it.

    Returns (container, index) or (None, -1).
    """
    current = obj
    for _ in range(10):  # don't walk too far
        parent = AXObject.get_parent(current)
        if parent is None:
            return None, -1
        try:
            parent_role = AXObject.get_role(parent)
        except Exception:
            return None, -1
        if parent_role in _CONTAINER_ROLES:
            idx = AXObject.get_index_in_parent(current)
            return parent, idx
        current = parent
    return None, -1


def _check_first_last(obj: Atspi.Accessible) -> str | None:
    """Return 'first_item.wav' or 'last_item.wav' if obj is first/last in a container."""
    try:
        container, index = _find_container_and_index(obj)
        if container is None or index < 0:
            return None
        n_children = AXObject.get_child_count(container)
        if n_children <= 1:
            return None
        if index == 0:
            return "first_item.wav"
        if index == n_children - 1:
            return "last_item.wav"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Focus change hook
# ---------------------------------------------------------------------------

def _patched_set_locus_of_focus(self, event, obj, notify_script=True, force=False):
    """Wrapper around FocusManager.set_locus_of_focus that plays theme sounds."""
    global _sound_played_for_focus
    _sound_played_for_focus = False

    old_focus = self._focus

    # Play the sound BEFORE the original method so that the flag is set
    # when Orca's speech generator runs (which happens inside the original).
    if _config is not None and _config.enabled and _config.play_on_focus:
        # Peek at what the new focus will be (obj), but note the original
        # method may reject it.  We accept this minor race for simplicity.
        if obj is not None and obj != old_focus:
            try:
                role = AXObject.get_role(obj)
            except Exception:
                role = None

            sound_file = ROLE_TO_SOUND.get(role) if role is not None else None
            if sound_file is not None and sound_file not in _config.disabled_sounds:
                if _config.positional_audio:
                    pan, elevation = _compute_position(obj)
                else:
                    pan, elevation = 0.0, 0.0

                # For item roles, try to play first/last cue instead
                _ITEM_SOUNDS = ("list_item.wav", "tree_item.wav", "table_cell.wav")
                played_first_last = False
                if sound_file in _ITEM_SOUNDS:
                    first_last = _check_first_last(obj)
                    if first_last and first_last not in _config.disabled_sounds:
                        cue_path = _resolve_sound_path(first_last)
                        if cue_path:
                            get_player().play(
                                cue_path, pan=pan, elevation=elevation,
                                volume=_config.volume,
                            )
                            _sound_played_for_focus = True
                            played_first_last = True

                # Play the role sound if first/last didn't replace it
                if not played_first_last:
                    path = _resolve_sound_path(sound_file)
                    if path:
                        get_player().play(path, pan=pan, elevation=elevation,
                                          volume=_config.volume)
                        _sound_played_for_focus = True

    _orig_set_locus(self, event, obj, notify_script, force)


# ---------------------------------------------------------------------------
# Role speech suppression
# ---------------------------------------------------------------------------

# Roles whose speech should NOT be suppressed even when speak_roles is off,
# because Orca adds useful context (e.g. "list with 11 items", "leaving list").
_ALWAYS_SPEAK_ROLES = frozenset([
    Atspi.Role.LIST,
    Atspi.Role.LIST_BOX,
    Atspi.Role.TREE,
    Atspi.Role.TREE_TABLE,
    Atspi.Role.TABLE,
    Atspi.Role.MENU,
    Atspi.Role.MENU_BAR,
])


def _should_suppress_role(obj: Atspi.Accessible, is_web: bool = False) -> bool:
    """Check if role speech should be suppressed right now."""
    global _sound_played_for_focus
    if _sound_played_for_focus and _config is not None and not _config.speak_roles:
        # In web content, let container roles speak (e.g. "list with 11 items")
        if is_web:
            try:
                role = AXObject.get_role(obj)
            except Exception:
                role = None
            if role in _ALWAYS_SPEAK_ROLES:
                return False
        _sound_played_for_focus = False
        return True
    return False


def _patched_generate_accessible_role(self, obj, **args):
    """Suppress role speech when a theme sound just played and speak_roles is off."""
    if _should_suppress_role(obj, is_web=False):
        return []
    return _orig_generate_accessible_role(self, obj, **args)


def _patched_web_generate_accessible_role(self, obj, **args):
    """Same suppression for the web script's speech generator."""
    if _should_suppress_role(obj, is_web=True):
        return []
    return _orig_web_generate_accessible_role(self, obj, **args)


# ---------------------------------------------------------------------------
# Window change hook
# ---------------------------------------------------------------------------

def _patched_set_active_window(self, frame, app=None, set_window_as_focus=False,
                               notify_script=False):
    """Wrapper around FocusManager.set_active_window that plays a sound."""
    global _sound_played_for_focus
    old_window = self._window
    is_new_window = frame is not None and frame != old_window

    # Play sound and set flag BEFORE original so "Frame" speech is suppressed.
    # Skip transient popups (combo box dropdowns, popup menus, tooltips).
    if is_new_window and _config is not None and _config.enabled:
        skip = (
            AXUtilities.is_combo_box_popup(frame)
            or AXUtilities.is_popup_menu(frame)
            or AXUtilities.is_tool_tip(frame)
        )
        if not skip:
            sound_file = MODE_SOUNDS.get("window_activate")
            if sound_file and sound_file not in _config.disabled_sounds:
                path = _resolve_sound_path(sound_file)
                if path:
                    get_overlay_player().play(path, volume=_config.volume)
                    _sound_played_for_focus = True

    _orig_set_active_window(self, frame, app, set_window_as_focus, notify_script)


# ---------------------------------------------------------------------------
# Password typing hook
# ---------------------------------------------------------------------------

def _play_password_keystroke(event) -> None:
    """Play the password typing sound if configured.

    Uses pw-play in a fire-and-forget thread so rapid keystrokes overlap
    instead of interrupting each other.
    """
    if _config is None or not _config.enabled or not _config.password_typing_sound:
        return
    try:
        if not AXUtilities.is_password_text(event.source):
            return
    except Exception:
        return
    # Only for single character insertions (typing, not paste)
    text = getattr(event, "any_data", None) or ""
    if len(text) != 1:
        return
    path = _resolve_sound_path(_config.password_typing_sound)
    if path:
        import subprocess
        import threading
        vol = str(_config.volume)
        cmd = ["pw-play", "--volume", vol, path]
        threading.Thread(
            target=lambda: subprocess.run(cmd, timeout=5, capture_output=True),
            daemon=True,
        ).start()


def _is_password_sound_active(event) -> bool:
    """Check if password typing sound should suppress speech for this event."""
    if _config is None or not _config.enabled or not _config.password_typing_sound:
        return False
    try:
        return AXUtilities.is_password_text(event.source)
    except Exception:
        return False


def _patched_on_text_inserted(self, event):
    """Wrapper around default script _on_text_inserted for password typing sounds."""
    _play_password_keystroke(event)
    if _is_password_sound_active(event):
        with _mute_present_message(None):
            return _orig_on_text_inserted(self, event)
    return _orig_on_text_inserted(self, event)


def _patched_on_text_deleted(self, event):
    """Wrapper around default script _on_text_deleted for password backspace sounds."""
    _play_password_keystroke(event)
    if _is_password_sound_active(event):
        with _mute_present_message(None):
            return _orig_on_text_deleted(self, event)
    return _orig_on_text_deleted(self, event)


def _patched_web_on_text_inserted(self, event):
    """Wrapper around web script _on_text_inserted for password typing sounds."""
    _play_password_keystroke(event)
    if _is_password_sound_active(event):
        with _mute_present_message(None):
            return _orig_web_on_text_inserted(self, event)
    return _orig_web_on_text_inserted(self, event)


def _patched_web_on_text_deleted(self, event):
    """Wrapper around web script _on_text_deleted for password backspace sounds."""
    _play_password_keystroke(event)
    if _is_password_sound_active(event):
        with _mute_present_message(None):
            return _orig_web_on_text_deleted(self, event)
    return _orig_web_on_text_deleted(self, event)


# ---------------------------------------------------------------------------
# Notification hook (fires on object:state-changed:showing for NOTIFICATION)
# ---------------------------------------------------------------------------

def _play_notification_sound_if_applicable(event) -> bool:
    """Play notification.wav for NOTIFICATION appearance events. Returns True if played."""
    if _config is None or not _config.enabled:
        return False
    try:
        obj = event.source
        if not AXUtilities.is_notification(obj):
            return False
        # detail1 == 1 means "shown"; 0 means "hidden"
        if not event.detail1:
            return False
    except Exception:
        return False
    sound_file = "notification.wav"
    if sound_file in _config.disabled_sounds:
        return False
    path = _resolve_sound_path(sound_file)
    if path:
        get_overlay_player().play(path, volume=_config.volume)
        return True
    return False


def _mute_speak_message():
    """Temporarily replace only speak_message with a no-op (keeps content speech)."""
    from orca import presentation_manager

    class _Ctx:
        def __enter__(self_ctx):
            mgr = presentation_manager.get_manager()
            self_ctx._orig = getattr(mgr, "speak_message", None)
            if self_ctx._orig is not None:
                mgr.speak_message = lambda *a, **kw: None
            return self_ctx

        def __exit__(self_ctx, *exc):
            mgr = presentation_manager.get_manager()
            if self_ctx._orig is not None:
                mgr.speak_message = self_ctx._orig

    return _Ctx()


def _patched_on_showing_changed(self, event):
    """Wrapper around default script _on_showing_changed for notification sounds."""
    played = _play_notification_sound_if_applicable(event)
    # Suppress only the role-name speech ("Notification") — keep content speech
    if played and _config is not None and not _config.speak_roles:
        with _mute_speak_message():
            return _orig_on_showing_changed(self, event)
    return _orig_on_showing_changed(self, event)


def _patched_web_on_showing_changed(self, event):
    """Wrapper around web script _on_showing_changed for notification sounds."""
    played = _play_notification_sound_if_applicable(event)
    if played and _config is not None and not _config.speak_roles:
        with _mute_speak_message():
            return _orig_web_on_showing_changed(self, event)
    return _orig_web_on_showing_changed(self, event)


# ---------------------------------------------------------------------------
# Mode change hooks
# ---------------------------------------------------------------------------

def _should_suppress_mode_speech() -> bool:
    """True when mode-change speech should be suppressed (sound replaces it)."""
    return (
        _config is not None
        and _config.enabled
        and _config.play_on_mode_change
        and not _config.speak_roles
    )


def _mute_present_message(func):
    """Temporarily replace present_message and speak_character with no-ops."""
    from orca import presentation_manager

    class _Ctx:
        def __enter__(self_ctx):
            mgr = presentation_manager.get_manager()
            self_ctx._orig_msg = mgr.present_message
            self_ctx._orig_char = getattr(mgr, "speak_character", None)
            self_ctx._orig_text = getattr(mgr, "speak_accessible_text", None)
            mgr.present_message = lambda *a, **kw: None
            if self_ctx._orig_char is not None:
                mgr.speak_character = lambda *a, **kw: None
            if self_ctx._orig_text is not None:
                mgr.speak_accessible_text = lambda *a, **kw: None
            return self_ctx

        def __exit__(self_ctx, *exc):
            mgr = presentation_manager.get_manager()
            mgr.present_message = self_ctx._orig_msg
            if self_ctx._orig_char is not None:
                mgr.speak_character = self_ctx._orig_char
            if self_ctx._orig_text is not None:
                mgr.speak_accessible_text = self_ctx._orig_text

    return _Ctx()


def _play_mode_sound(mode_key: str) -> None:
    """Play a mode-change sound if enabled."""
    if _config is None or not _config.enabled or not _config.play_on_mode_change:
        return
    sound_file = MODE_SOUNDS.get(mode_key)
    if sound_file is None or sound_file in _config.disabled_sounds:
        return
    path = _resolve_sound_path(sound_file)
    if path:
        # Use overlay player so mode sounds don't interrupt role sounds
        get_overlay_player().play(path, volume=_config.volume)


def _patched_set_presentation_mode(self, script, use_focus_mode, obj=None,
                                   document=None, notify_user=True):
    """Wrapper around DocumentPresenter._set_presentation_mode."""
    if _should_suppress_mode_speech():
        with _mute_present_message(None):
            result = _orig_set_presentation_mode(
                self, script, use_focus_mode, obj, document, notify_user,
            )
    else:
        result = _orig_set_presentation_mode(
            self, script, use_focus_mode, obj, document, notify_user,
        )

    if result:
        if use_focus_mode:
            _play_mode_sound("focus_mode")
        else:
            _play_mode_sound("browse_mode")

    return result


# Sticky mode has to be hooked in two places, and a guard keeps them from
# both firing for one keypress. See _wrap_sticky_commands for why.
_sticky_announcing = False

_STICKY_COMMANDS = (
    ("enable_sticky_focus_mode", "focus_mode_sticky"),
    ("enable_sticky_browse_mode", "browse_mode_sticky"),
)

# [(command, original_function)] for uninstall.
_wrapped_sticky_commands: list = []


def _announce_sticky(call, sound_key):
    """Run `call`, muting Orca's own message if configured, then play a sound."""
    global _sticky_announcing
    if _sticky_announcing:
        return call()
    _sticky_announcing = True
    try:
        if _should_suppress_mode_speech():
            with _mute_present_message(None):
                result = call()
        else:
            result = call()
    finally:
        _sticky_announcing = False
    _play_mode_sound(sound_key)
    return result


def _sticky_command_wrapper(original, sound_key):
    """Return a wrapper around a sticky-mode command's stored function."""

    def wrapper(*args, **kwargs):
        return _announce_sticky(lambda: original(*args, **kwargs), sound_key)

    wrapper._audio_themes_sticky = True
    return wrapper


def _wrap_sticky_commands() -> None:
    """Wrap Orca's sticky-mode commands so pressing the key plays a sound.

    Patching DocumentPresenter is not enough on its own. Orca builds these
    commands as
    ``KeyboardCommand("enable_sticky_focus_mode", owner.enable_sticky_focus_mode, ...)``,
    which captures a *bound method* when commands are set up -- and that
    happens during script setup, before user extensions are sent on_ready().
    The stored callable therefore never sees a class-level patch applied
    later, so the keypress path produced no sound at all. Nor does
    enable_sticky_*_mode call _set_presentation_mode, so the other hook
    could not stand in for it.

    The class-level patches are still needed: Orca's own automatic
    sticky-focus calls go through the class and so only hit those.
    """
    manager = command_manager.get_manager()
    for name, sound_key in _STICKY_COMMANDS:
        command = manager.get_keyboard_command(name)
        if command is None:
            _log.warning(
                "AudioThemes: command %s is not registered; sticky mode will be silent", name
            )
            continue
        original = command.get_function()
        if getattr(original, "_audio_themes_sticky", False):
            continue
        command.set_function(_sticky_command_wrapper(original, sound_key))
        _wrapped_sticky_commands.append((command, original))
        _log.info("AudioThemes: wrapped the %s command for sticky mode sounds", name)


def _unwrap_sticky_commands() -> None:
    """Restore the original functions on Orca's sticky-mode commands."""
    while _wrapped_sticky_commands:
        command, original = _wrapped_sticky_commands.pop()
        try:
            command.set_function(original)
        except Exception as error:  # pylint: disable=broad-exception-caught
            _log.warning("AudioThemes: could not restore a sticky command: %s", error)


def _patched_enable_sticky_focus(self, script, event=None, notify_user=True):
    """Wrapper around DocumentPresenter.enable_sticky_focus_mode."""
    return _announce_sticky(
        lambda: _orig_enable_sticky_focus(self, script, event, notify_user),
        "focus_mode_sticky",
    )


def _patched_enable_sticky_browse(self, script, event=None, notify_user=True):
    """Wrapper around DocumentPresenter.enable_sticky_browse_mode."""
    return _announce_sticky(
        lambda: _orig_enable_sticky_browse(self, script, event, notify_user),
        "browse_mode_sticky",
    )


# ---------------------------------------------------------------------------
# Settings GUI keybinding
# ---------------------------------------------------------------------------

def open_settings() -> bool:
    """Command handler for Orca+Ctrl+A. Opens the hand-written dialog."""
    GLib.idle_add(_show_settings_ui)
    return True


def _show_settings_ui() -> bool:
    """Open the settings dialog on the main thread."""
    if _config is None:
        _log.error("AudioThemes: settings requested before the extension was started.")
        return False
    try:
        from .config_ui import show_settings_dialog
        show_settings_dialog(_config, on_save=_on_settings_saved)
    except Exception as e:
        _log.error("AudioThemes: could not open settings: %s", e, exc_info=True)
        try:
            from orca import presentation_manager
            presentation_manager.get_manager().present_message(
                f"Error opening Audio Themes settings: {e}"
            )
        except Exception:
            pass
    return False


def _on_settings_saved(config: Config) -> None:
    """Callback when settings are saved from the UI."""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install(config: Config) -> None:
    """Apply all monkey-patches. Called from the Extension's lifecycle hooks."""
    global _config, _installed
    global _orig_set_locus, _orig_set_presentation_mode
    global _orig_enable_sticky_focus, _orig_enable_sticky_browse
    global _orig_generate_accessible_role, _orig_web_generate_accessible_role
    global _orig_set_active_window
    global _orig_on_text_inserted, _orig_on_text_deleted
    global _orig_web_on_text_inserted, _orig_web_on_text_deleted
    global _orig_on_showing_changed, _orig_web_on_showing_changed

    if _installed:
        return

    _config = config

    # Apply saved audio output device. The stream move waits for Speech
    # Dispatcher to have a stream to move; bind the device name now rather
    # than reading the global later, which by then may be a different
    # config object or None.
    if _config.audio_output:
        device = _config.audio_output
        set_output_device(device)

        def _move_streams_once() -> bool:
            if _installed:
                move_orca_streams(device)
            return False

        GLib.timeout_add(2000, _move_streams_once)

    # Patch focus changes
    _orig_set_locus = focus_manager.FocusManager.set_locus_of_focus
    focus_manager.FocusManager.set_locus_of_focus = _patched_set_locus_of_focus

    # Patch role speech suppression (base + web subclass)
    _orig_generate_accessible_role = speech_generator.SpeechGenerator._generate_accessible_role
    speech_generator.SpeechGenerator._generate_accessible_role = _patched_generate_accessible_role

    _orig_web_generate_accessible_role = web_speech_generator.SpeechGenerator._generate_accessible_role
    web_speech_generator.SpeechGenerator._generate_accessible_role = _patched_web_generate_accessible_role

    # Patch window changes
    _orig_set_active_window = focus_manager.FocusManager.set_active_window
    focus_manager.FocusManager.set_active_window = _patched_set_active_window

    # Patch password typing (base + web script)
    _orig_on_text_inserted = default_script.Script._on_text_inserted
    default_script.Script._on_text_inserted = _patched_on_text_inserted

    _orig_on_text_deleted = default_script.Script._on_text_deleted
    default_script.Script._on_text_deleted = _patched_on_text_deleted

    _orig_web_on_text_inserted = web_script.Script._on_text_inserted
    web_script.Script._on_text_inserted = _patched_web_on_text_inserted

    _orig_web_on_text_deleted = web_script.Script._on_text_deleted
    web_script.Script._on_text_deleted = _patched_web_on_text_deleted

    # Patch notification showing events
    _orig_on_showing_changed = default_script.Script._on_showing_changed
    default_script.Script._on_showing_changed = _patched_on_showing_changed

    _orig_web_on_showing_changed = web_script.Script._on_showing_changed
    web_script.Script._on_showing_changed = _patched_web_on_showing_changed

    # Patch mode transitions
    _orig_set_presentation_mode = document_presenter.DocumentPresenter._set_presentation_mode
    document_presenter.DocumentPresenter._set_presentation_mode = _patched_set_presentation_mode

    _orig_enable_sticky_focus = document_presenter.DocumentPresenter.enable_sticky_focus_mode
    document_presenter.DocumentPresenter.enable_sticky_focus_mode = _patched_enable_sticky_focus

    _orig_enable_sticky_browse = document_presenter.DocumentPresenter.enable_sticky_browse_mode
    document_presenter.DocumentPresenter.enable_sticky_browse_mode = _patched_enable_sticky_browse

    # The class patches above only catch Orca's internal auto-sticky calls;
    # the keyboard commands hold bound methods captured before we loaded.
    _wrap_sticky_commands()

    _installed = True
    _log.info(
        "AudioThemes: installed (enabled=%s, theme=%s, positional=%s)",
        _config.enabled, _config.active_theme, _config.positional_audio,
    )


def uninstall() -> None:
    """Remove all monkey-patches and restore originals."""
    global _installed, _config

    if not _installed:
        return

    if _orig_set_locus is not None:
        focus_manager.FocusManager.set_locus_of_focus = _orig_set_locus
    if _orig_set_active_window is not None:
        focus_manager.FocusManager.set_active_window = _orig_set_active_window
    if _orig_on_text_inserted is not None:
        default_script.Script._on_text_inserted = _orig_on_text_inserted
    if _orig_on_text_deleted is not None:
        default_script.Script._on_text_deleted = _orig_on_text_deleted
    if _orig_web_on_text_inserted is not None:
        web_script.Script._on_text_inserted = _orig_web_on_text_inserted
    if _orig_web_on_text_deleted is not None:
        web_script.Script._on_text_deleted = _orig_web_on_text_deleted
    if _orig_on_showing_changed is not None:
        default_script.Script._on_showing_changed = _orig_on_showing_changed
    if _orig_web_on_showing_changed is not None:
        web_script.Script._on_showing_changed = _orig_web_on_showing_changed
    if _orig_generate_accessible_role is not None:
        speech_generator.SpeechGenerator._generate_accessible_role = _orig_generate_accessible_role
    if _orig_web_generate_accessible_role is not None:
        web_speech_generator.SpeechGenerator._generate_accessible_role = _orig_web_generate_accessible_role
    if _orig_set_presentation_mode is not None:
        document_presenter.DocumentPresenter._set_presentation_mode = _orig_set_presentation_mode
    if _orig_enable_sticky_focus is not None:
        document_presenter.DocumentPresenter.enable_sticky_focus_mode = _orig_enable_sticky_focus
    if _orig_enable_sticky_browse is not None:
        document_presenter.DocumentPresenter.enable_sticky_browse_mode = _orig_enable_sticky_browse

    _unwrap_sticky_commands()

    # reset_players(), not get_player().shutdown(): the latter would build
    # a pipeline just to tear it down if nothing had played yet, and would
    # leave the singletons pointing at dead players.
    reset_players()
    _installed = False
    _config = None
    _log.info("AudioThemes: uninstalled")
