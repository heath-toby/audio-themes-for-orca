#!/usr/bin/env bash
# Orca Audio Themes -- Installer
#
# Installs Audio Themes as an Orca 51 user extension, into
# ~/.local/share/orca/extensions/, and approves it so Orca will load it.
#
# Themes are installed OUTSIDE the extension package, to
# ~/.local/share/orca/audio-themes/themes/. This is not cosmetic: Orca
# approves an extension by hashing every file in its directory, and the
# settings dialog writes themes at runtime (import, duplicate, per-role
# sound assignment). Themes inside the package would silently un-approve
# the extension the first time you edited one, and Audio Themes would
# stop loading on the next Orca start.
#
# Existing themes are never overwritten, so edits and imported themes
# survive a reinstall. Themes from a pre-extension install are moved
# across the first time.
#
# Re-running is safe. Because approval is by content hash, the script
# re-approves on every run -- which is what you want after editing.

set -euo pipefail

ADDON_NAME="audio_themes"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
EXTENSIONS_DIR="$ORCA_DIR/extensions"
ADDON_DIR="$EXTENSIONS_DIR/$ADDON_NAME"
THEMES_DIR="$ORCA_DIR/audio-themes/themes"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/$ADDON_NAME"

BEGIN_MARKER="# --- audio-themes begin ---"
END_MARKER="# --- audio-themes end ---"

# Pre-extension install locations whose themes should be carried over.
LEGACY_THEME_DIRS=(
    "$ORCA_DIR/audio_themes/themes"
    "$ORCA_DIR/audio_themes_v51/themes"
    "$ORCA_DIR/audio_themes_v50/themes"
)

info()  { echo "  [+] $*"; }
warn()  { echo "  [!] $*"; }
error() { echo "  [ERROR] $*" >&2; exit 1; }

echo ""
echo "=== Orca Audio Themes -- Installer ==="
echo ""

# Probe via extension_loader, not orca.extension: importing orca.extension
# first hits a circular import inside Orca itself (live_region_presenter
# imports it mid-initialisation). extension_loader pulls in command_manager
# ahead of it, so this import order is the one that works.
if ! python3 -c "import orca.extension_loader" 2>/dev/null; then
    error "Orca 51 or later with extension support not found."
fi
info "Orca with extension support found."

[ -d "$SOURCE_DIR" ] || error "Source directory '$SOURCE_DIR' not found."

rm -rf "$SOURCE_DIR/__pycache__"

# --- Themes, before the package, so a fresh install has them ready ---

mkdir -p "$THEMES_DIR"

copy_theme_if_absent() {
    local src="$1" name
    name="$(basename "$src")"
    [ -d "$src" ] || return 0
    if [ -d "$THEMES_DIR/$name" ]; then
        return 1
    fi
    cp -r "$src" "$THEMES_DIR/$name"
    return 0
}

carried=0
for legacy in "${LEGACY_THEME_DIRS[@]}"; do
    [ -d "$legacy" ] || continue
    for theme in "$legacy"/*/; do
        [ -d "$theme" ] || continue
        if copy_theme_if_absent "${theme%/}"; then
            info "Carried over theme '$(basename "${theme%/}")' from $(dirname "$legacy")."
            carried=$((carried + 1))
        fi
    done
done
[ "$carried" -eq 0 ] || info "Carried over $carried theme(s) from a previous install."

if [ -d "$SOURCE_DIR/themes" ]; then
    for theme in "$SOURCE_DIR/themes"/*/; do
        [ -d "$theme" ] || continue
        name="$(basename "${theme%/}")"
        if copy_theme_if_absent "${theme%/}"; then
            info "Installed theme '$name'."
        else
            info "Kept your existing '$name' theme (not overwritten)."
        fi
    done
else
    warn "No themes directory at $SOURCE_DIR/themes."
fi

THEME_COUNT=$(find "$THEMES_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
info "$THEME_COUNT theme(s) in $THEMES_DIR"

# --- The extension package itself: code only ---

mkdir -p "$ADDON_DIR"
# Remove files that no longer exist in the source, so the installed
# package -- and therefore its approval hash -- matches the source.
find "$ADDON_DIR" -maxdepth 1 -name '*.py' -delete
rm -rf "$ADDON_DIR/__pycache__" "$ADDON_DIR/themes"
cp "$SOURCE_DIR"/*.py "$ADDON_DIR/"
info "Installed extension package to $ADDON_DIR"

if orca --approve-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Approved '$ADDON_NAME' with Orca."
else
    error "Could not approve the extension. Run: orca --approve-extension $ADDON_NAME"
fi

# Clean up after the pre-extension installer, if its block is still there.
if [ -f "$CUSTOMIZATIONS" ] && grep -qF "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed the obsolete Audio Themes block from orca-customizations.py."
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "  Restart Orca to activate:"
echo "    orca --replace &"
echo ""
echo "  Settings: Orca+Ctrl+A. Themes: $THEMES_DIR"
echo "  Settings from the old org.gnome.Orca.AudioThemes schema are imported"
echo "  on first run; nothing to do by hand."
echo ""
