#!/usr/bin/env bash
# Orca Audio Themes -- Uninstaller
#
# Removes the extension package, revokes its Orca approval, and clears
# the loader block left behind by pre-extension versions of the
# installer. Nothing else in orca-customizations.py is touched.
#
# Your themes and settings are left alone: themes live in
# ~/.local/share/orca/audio-themes/themes/ and settings under
# /org/gnome/orca/<profile>/extensions/audio-themes/, so a reinstall
# picks up where you left off. Pass --purge to remove them too.

set -euo pipefail

ADDON_NAME="audio_themes"
# Orca sanitises an extension's namespace for the settings path by turning
# underscores into dashes, so the settings live under "audio-themes", not
# "audio_themes". Resetting the wrong one leaves every setting in place.
SETTINGS_NAME="audio-themes"
# An older Orca sanitiser dropped the underscore instead of hyphenating it.
# Installs that ran under it left a second, now-orphaned settings path.
STALE_SETTINGS_NAME="audiothemes"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/extensions/$ADDON_NAME"
THEMES_ROOT="$ORCA_DIR/audio-themes"
LEGACY_DIR="$ORCA_DIR/$ADDON_NAME"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"
SCHEMA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/glib-2.0/schemas"
SCHEMA_FILE="org.gnome.Orca.AudioThemes.gschema.xml"

BEGIN_MARKER="# --- audio-themes begin ---"
END_MARKER="# --- audio-themes end ---"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

info() { echo "  [+] $*"; }

echo ""
echo "=== Orca Audio Themes -- Uninstaller ==="
echo ""

# Revoke first: once the directory is gone Orca can no longer identify it.
if orca --revoke-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Revoked Orca's approval of '$ADDON_NAME'."
else
    info "No Orca approval to revoke (already removed)."
fi

if [ -d "$ADDON_DIR" ]; then
    rm -rf "$ADDON_DIR"
    info "Removed $ADDON_DIR"
else
    info "No installed extension at $ADDON_DIR (already removed)."
fi

if [ -f "$CUSTOMIZATIONS" ] && grep -qF "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed the legacy Audio Themes block from orca-customizations.py."
fi

if [ -d "$LEGACY_DIR" ]; then
    info "A pre-extension install remains at $LEGACY_DIR; remove it by hand if unwanted."
fi

if [ "$PURGE" -eq 1 ]; then
    if [ -d "$THEMES_ROOT" ]; then
        rm -rf "$THEMES_ROOT"
        info "Removed your themes at $THEMES_ROOT."
    fi
    dconf reset -f "/org/gnome/orca/default/extensions/$SETTINGS_NAME/" 2>/dev/null \
        && info "Removed the extension settings." \
        || info "No extension settings to remove."
    if dconf list "/org/gnome/orca/default/extensions/$STALE_SETTINGS_NAME/" 2>/dev/null | grep -q .; then
        dconf reset -f "/org/gnome/orca/default/extensions/$STALE_SETTINGS_NAME/" 2>/dev/null \
            && info "Removed orphaned settings at .../extensions/$STALE_SETTINGS_NAME/."
    fi
    dconf reset -f /org/gnome/orca/audio-themes/ 2>/dev/null \
        && info "Removed the legacy org.gnome.Orca.AudioThemes settings." \
        || info "No legacy settings to remove."
    if [ -f "$SCHEMA_DIR/$SCHEMA_FILE" ]; then
        rm -f "$SCHEMA_DIR/$SCHEMA_FILE"
        command -v glib-compile-schemas >/dev/null 2>&1 && \
            glib-compile-schemas "$SCHEMA_DIR" 2>/dev/null || true
        info "Removed the legacy GSettings schema."
    fi
else
    info "Themes and settings kept. Re-run with --purge to remove them too."
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "  Restart Orca for the change to take effect:"
echo "    orca --replace &"
echo ""
