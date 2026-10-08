#!/usr/bin/env python3
"""Smoke tests for Orca Audio Themes.

Run from the repository root:

    ./tests-audio-themes.py

Covers the parts that can be exercised without a running Orca: the
settings round-trip and its legacy import, the sound-file inventory
against the shipped default theme, the player singletons' teardown and
rebuild, and the settings path the uninstaller resets. Every row here
exists because something was once wrong; the player-rebuild rows and the
uninstaller row in particular are regression guards -- see CHANGELOG 2.0.0.

The modules are loaded directly rather than by importing the audio_themes
package, because the package's __init__ pulls in orca.extension and the
whole focus-interceptor patch set, neither of which belongs in a unit test.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(ROOT, "audio_themes")

PASSED = 0
FAILED: list[str] = []


def check(label: str, got, want) -> None:
    global PASSED
    if got == want:
        PASSED += 1
    else:
        FAILED.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


def load(name: str):
    """Load audio_themes.<name> without executing the package __init__."""
    pkg_name = "audio_themes"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [PKG_DIR]
        sys.modules[pkg_name] = pkg
    full = f"{pkg_name}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(PKG_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# A stand-in for Orca's ExtensionSettings
# ---------------------------------------------------------------------------

class FakeSettings:
    """Mimics orca.extension.ExtensionSettings closely enough to test against."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

config = load("config")

print("Settings")

settings = FakeSettings()
cfg = config.Config.load(settings)
check("defaults: active theme", cfg.active_theme, config.DEFAULT_ACTIVE_THEME)
check("defaults: volume", cfg.volume, config.DEFAULT_VOLUME)
check("defaults: positional audio", cfg.positional_audio, config.DEFAULT_POSITIONAL_AUDIO)
check("defaults: disabled sounds", cfg.disabled_sounds, [])

cfg.active_theme = "mine"
cfg.volume = 0.25
cfg.positional_audio = False
cfg.disabled_sounds = ["button.wav", "link.wav"]
cfg.password_typing_sound = "entry.wav"
cfg.save()
again = config.Config.load(settings)
check("round-trip: active theme", again.active_theme, "mine")
check("round-trip: volume", again.volume, 0.25)
check("round-trip: positional audio", again.positional_audio, False)
check("round-trip: disabled sounds", again.disabled_sounds, ["button.wav", "link.wav"])
check("round-trip: password sound", again.password_typing_sound, "entry.wav")

# Volume is clamped on read, so a value written by hand into dconf -- or by
# an older version with a wider range -- cannot blow the sink up.
check("volume clamps above 1.0", config.Config.load(FakeSettings({"volume": 7.5})).volume, 1.0)
check("volume clamps below 0.0", config.Config.load(FakeSettings({"volume": -3.0})).volume, 0.0)

# A missing settings store must degrade to defaults, not raise: Config is
# built before the extension is marked up, and in the test rig above all.
bare = config.Config.load(None)
check("no settings store falls back to defaults", bare.volume, config.DEFAULT_VOLUME)

print("Legacy settings import")

# Whether there is anything to import depends on the machine -- the old
# private schema may or may not still be installed -- so what is asserted
# here is what must hold either way: the marker is written, so the lookup
# happens at most once in the life of an install.
fresh = FakeSettings()
imported = config.migrate_legacy_settings(fresh)
if imported:
    print("  (the legacy schema is installed here; values were imported)")
    check("an import brings the volume across", "volume" in fresh.values, True)
    check("an import brings the theme across", "active-theme" in fresh.values, True)
else:
    print("  (no legacy schema installed here; defaults kept)")
check(
    "version marker written whether or not anything was imported",
    fresh.get("settings-version"),
    config.SETTINGS_VERSION,
)
check("import does not run twice", config.migrate_legacy_settings(fresh), False)

# An install that has already migrated must be left alone entirely.
migrated = FakeSettings({"settings-version": config.SETTINGS_VERSION, "volume": 0.1})
config.migrate_legacy_settings(migrated)
check("an already-migrated store is untouched", migrated.get("volume"), 0.1)

print("Theme locations")

# Themes must live outside the extension package: Orca approves by hashing
# every file in the package directory, and the dialog writes themes at
# runtime. Themes inside the package would un-approve the extension.
check_true("THEMES_DIR is outside the package", PKG_DIR not in config.THEMES_DIR)
check(
    "THEMES_DIR is under the Orca data directory",
    config.THEMES_DIR.startswith(config.ORCA_DIR + os.sep),
    True,
)
check(
    "THEMES_DIR uses the dashed directory name",
    os.path.basename(os.path.dirname(config.THEMES_DIR)),
    "audio-themes",
)

# ---------------------------------------------------------------------------
# Sound inventory
# ---------------------------------------------------------------------------

role_map = load("role_map")

print("Sound inventory")

shipped_dir = os.path.join(PKG_DIR, "themes", "default")
shipped = {f for f in os.listdir(shipped_dir) if f.lower().endswith((".wav", ".ogg"))}

missing = sorted(f for f in role_map.ALL_SOUND_FILES if f not in shipped)
check("every mapped sound ships in the default theme", missing, [])

mode_missing = sorted(f for f in role_map.MODE_SOUNDS.values() if f not in shipped)
check("every mode-change sound ships", mode_missing, [])

unlabelled = sorted(f for f in role_map.ALL_SOUND_FILES if f not in role_map.SOUND_LABELS)
check("every sound has a dialog label", unlabelled, [])

nvda_bad = sorted(
    name for name in role_map.NVDA_ID_TO_FILENAME.values()
    if not name.lower().endswith(".wav")
)
check("NVDA id translation yields .wav names", nvda_bad, [])

check_true("the default theme has an info.json", os.path.isfile(os.path.join(shipped_dir, "info.json")))

# ---------------------------------------------------------------------------
# Player lifecycle
# ---------------------------------------------------------------------------

print("Player lifecycle")

sound_player = load("sound_player")

first = sound_player.get_player()
check("get_player is a singleton", sound_player.get_player() is first, True)
overlay = sound_player.get_overlay_player()
check("the overlay player is a separate object", overlay is first, False)
check("get_overlay_player is a singleton", sound_player.get_overlay_player() is overlay, True)

# THE REGRESSION THIS FILE EXISTS FOR. A player that has been shut down can
# never play again, so shutting one down without dropping the singleton left
# get_player() handing back a corpse: play() found no pipeline and returned,
# and Audio Themes went silent for the rest of the session. Harmless before
# the extension system, because nothing ever tore the patches down; with
# Orca able to disable and re-enable an extension, disabling it once and
# switching it back on killed every sound until the next Orca restart.
first.shutdown()
check("a shut-down player drops its pipeline", first._pipeline, None)
check_true("play() on a dead player is a harmless no-op", first.play("/nonexistent.wav") is None)

sound_player.reset_players()
rebuilt = sound_player.get_player()
check("reset_players forces a rebuild", rebuilt is first, False)
check_true("the rebuilt player has a live pipeline", rebuilt._pipeline is not None)
rebuilt_overlay = sound_player.get_overlay_player()
check("reset_players rebuilds the overlay player too", rebuilt_overlay is overlay, False)

# reset_players must cope with players that were never built, because
# uninstall() calls it whether or not a sound ever played.
sound_player.reset_players()
sound_player.reset_players()
check("reset_players is idempotent", sound_player._player, None)
check_true("a fresh player builds after a bare reset", sound_player.get_player()._pipeline is not None)
sound_player.reset_players()

# Switching device goes through the same reset, so it cannot strand a corpse.
before = sound_player.get_player()
sound_player.set_output_device("some.sink.name")
after = sound_player.get_player()
check("changing the output device rebuilds the player", after is before, False)
check_true("the device-specific player has a live pipeline", after._pipeline is not None)
sound_player.set_output_device("")
sound_player.reset_players()

check("an Orca stream is recognised", sound_player._is_orca_stream("ORCA"), True)
check("a Speech Dispatcher stream is recognised", sound_player._is_orca_stream("speech-dispatcher"), True)
check("an unrelated stream is not moved", sound_player._is_orca_stream("Firefox"), False)

# ---------------------------------------------------------------------------
# The installer and uninstaller
# ---------------------------------------------------------------------------

print("Installer and uninstaller")

uninstall_sh = open(os.path.join(ROOT, "uninstall.sh")).read()
install_sh = open(os.path.join(ROOT, "install.sh")).read()

# Orca turns the underscore in the "audio_themes" namespace into a dash for
# the settings path. --purge once reset ".../extensions/audio_themes/",
# which does not exist, so it reported success and removed nothing.
check(
    "--purge resets the sanitised settings path",
    bool(re.search(r"dconf reset -f .*extensions/\$SETTINGS_NAME/", uninstall_sh)),
    True,
)
check(
    "SETTINGS_NAME is the dashed form",
    bool(re.search(r'^SETTINGS_NAME="audio-themes"$', uninstall_sh, re.M)),
    True,
)
check(
    "--purge does not reset the underscored path",
    "extensions/audio_themes/" in uninstall_sh,
    False,
)
check(
    "--purge also clears the orphaned dashless path",
    bool(re.search(r'^STALE_SETTINGS_NAME="audiothemes"$', uninstall_sh, re.M)),
    True,
)
check(
    "--purge clears the legacy private schema's path",
    "/org/gnome/orca/audio-themes/" in uninstall_sh,
    True,
)

# The installer must put code, and only code, in the package directory, and
# re-approve every run, because approval is by content hash.
check("the installer copies only Python files", 'cp "$SOURCE_DIR"/*.py' in install_sh, True)
check("the installer keeps themes out of the package", 'rm -rf "$ADDON_DIR/__pycache__" "$ADDON_DIR/themes"' in install_sh, True)
check("the installer re-approves every run", "orca --approve-extension" in install_sh, True)
check("the installer puts themes in the dashed directory", 'THEMES_DIR="$ORCA_DIR/audio-themes/themes"' in install_sh, True)

# ---------------------------------------------------------------------------
# Version consistency
# ---------------------------------------------------------------------------

print("Version")

init_src = open(os.path.join(PKG_DIR, "__init__.py")).read()
dunder = re.search(r'^__version__ = "([^"]+)"', init_src, re.M)
classvar = re.search(r'^    VERSION = "([^"]+)"', init_src, re.M)
check_true("__version__ is declared", dunder is not None)
check_true("VERSION is declared", classvar is not None)
if dunder and classvar:
    check("__version__ and VERSION agree", dunder.group(1), classvar.group(1))

changelog = os.path.join(ROOT, "CHANGELOG.md")
check_true("there is a changelog", os.path.isfile(changelog))
if os.path.isfile(changelog) and dunder:
    check(
        "the changelog has an entry for this version",
        f"## {dunder.group(1)}" in open(changelog).read(),
        True,
    )

# ---------------------------------------------------------------------------

print()
total = PASSED + len(FAILED)
if FAILED:
    print(f"{PASSED}/{total} passed. Failures:")
    for failure in FAILED:
        print(f"  [FAIL] {failure}")
    sys.exit(1)
print(f"{PASSED}/{total} passed.")
