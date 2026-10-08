# Audio Themes for Orca

Sound theme support for the [Orca screen reader](https://wiki.gnome.org/Projects/Orca) on Linux. Plays distinct sounds when different UI elements receive focus, when Orca switches between focus/browse modes, and when windows are activated — equivalent to the [Audio Themes add-on](https://github.com/mush42/NVDA-Audio-Themes) for NVDA on Windows.

## Features

- **Role-based focus sounds** — each UI control type (button, checkbox, link, slider, etc.) plays a unique sound when it receives focus
- **Mode-change sounds** — audible feedback when switching between focus and browse modes (including sticky variants)
- **Window activation sound** — plays when a new window comes to the foreground
- **First/last item cues** — distinct sounds when reaching the first or last item in a text list or tree
- **2D positional audio** — sounds pan left/right and shift tone based on the focused element's screen position
- **Role speech suppression** — optionally replaces Orca's spoken role names with sounds only (web list announcements like "list with 11 items" are preserved)
- **Per-sound enable/disable** — toggle individual sounds on or off via checkboxes in the theme editor
- **Theme support** — installable sound themes with a built-in editor for creating custom themes
- **NVDA compatibility** — import NVDA `.atp` theme packages directly; numeric filenames are automatically translated
- **Settings GUI** — full configuration dialog accessible via Orca+Ctrl+A
- **Non-invasive** — an Orca 51 user extension; no Orca source code is modified

## Requirements

- Orca **51 or later** — this is a user extension, and the extension system did not
  exist before 51. (Version 1.x loaded itself from `orca-customizations.py` and works on
  Orca 50 and earlier.)
- GStreamer 1.0 with `gst-plugins-good` (for `audiopanorama` and `equalizer-3bands`)
- PipeWire (for `pw-play` in sound preview)
- `sox` (optional, for generating mode-change sounds during install)

## Installation

```bash
git clone https://github.com/heath-toby/audio-themes-for-orca.git
cd audio-themes-for-orca
./install.sh
```

Then restart Orca:

```bash
orca --replace &
```

The installer puts the code in `~/.local/share/orca/extensions/audio_themes/` and approves
it with `orca --approve-extension audio_themes`. Themes go somewhere separate —
`~/.local/share/orca/audio-themes/themes/` — for reasons explained under
[Where themes live](#where-themes-live). Existing themes are never overwritten, so edits
and imported themes survive a reinstall; themes from a version 1.x install are carried
across the first time.

Orca approves extensions **by content hash** and refuses to load one whose files have
changed since approval, so after editing anything under `audio_themes/` re-run
`./install.sh` — it re-approves as part of installing.

### Upgrading from version 1.x

Nothing to do. Settings used to live in a private GSettings schema,
`org.gnome.Orca.AudioThemes`; they now use Orca's own per-extension settings store. The
first time the extension runs it imports every value from the old schema and marks the
import done so it never runs twice. Your themes are moved out of the old add-on folder by
the installer. The old schema and its values are left in place;
`./uninstall.sh --purge` removes them along with your themes.

## Uninstallation

```bash
./uninstall.sh
orca --replace &
```

## Usage

After installation, sounds play automatically as you navigate UI elements:

- **Tab** through a GTK app (e.g., GNOME Settings) — each button, checkbox, combo box, etc. plays a distinct sound
- **Navigate a web page** in Firefox — hear the difference between links, headings, form controls
- **Toggle focus/browse mode** with Insert+A — mode-change sounds play
- **Switch windows** — a chime plays when a new window comes to the foreground
- **Reach the start or end of a list** — first/last item cues play for text lists

### Keybinding

| Shortcut | Action |
|----------|--------|
| Orca+Ctrl+A | Open Audio Themes settings |

### Settings

The settings dialog (Orca+Ctrl+A) has two pages:

**General:**
- Enable/disable audio themes
- Choose active sound theme
- Volume control
- Toggle 2D positional audio
- Toggle focus-change and mode-change sounds
- Toggle whether Orca still speaks role names (when off, web list context like "list with 11 items" is still spoken)

**Theme Editor:**
- Enable/disable individual sounds via checkboxes
- Preview, change, or reset sounds for each role
- Create new themes (duplicates current theme)
- Import themes from NVDA `.atp` packages or ZIP files (numeric NVDA filenames are automatically renamed)
- Export themes as ZIP packages

If an imported or custom theme is missing sounds for certain roles, the default theme's sounds are used as a fallback.

## How It Works

The add-on monkey-patches several Orca internal methods without modifying any source files:

- **`FocusManager.set_locus_of_focus`** — plays role-appropriate sounds on focus changes
- **`FocusManager.set_active_window`** — plays a sound on window activation
- **`DocumentPresenter._set_presentation_mode`** (and sticky variants) — plays mode-change sounds
- **`SpeechGenerator._generate_accessible_role`** (base + web subclass) — optionally suppresses role speech

Sound playback uses two custom GStreamer pipelines (primary for role sounds, overlay for simultaneous mode/window/first-last sounds):

```
filesrc -> decodebin -> audioconvert -> equalizer-3bands -> audiopanorama -> volume -> autoaudiosink
```

- **Horizontal panning** (`audiopanorama`): maps X screen position to stereo pan
- **Vertical tone shift** (`equalizer-3bands`): objects near the top sound brighter, objects near the bottom sound warmer

Configuration is stored in Orca's own per-extension settings store, which `dconf` keeps
under `/org/gnome/orca/<profile>/extensions/audio-themes/` — Orca sanitises the
`audio_themes` namespace by turning the underscore into a dash, so the settings path and
the package name are spelled differently.

### Why there is no entry under Orca's extension preferences

Orca 51 lets an extension declare its settings and get a dialog generated for free. Audio
Themes declares none, on purpose, so the **Settings button in Orca Preferences → User
Extensions is deliberately inactive**. That generated dialog can only render simple
declarative controls — booleans, strings, enums, numbers. It cannot host the theme
editor's per-role **Preview** buttons, its sound choosers, or theme import and export, and
there is no hook for an extension to supply its own dialog in place of it. Declaring
preferences would produce a second, poorer settings dialog beside the real one.
**Orca+Ctrl+A is the single place Audio Themes is configured.**

### Where themes live

Themes are installed to `~/.local/share/orca/audio-themes/themes/`, *outside* the
extension package. This matters more than it looks.

Orca approves a user extension by hashing every file in its directory, and refuses to load
it if anything changed since approval. The settings dialog writes themes at runtime —
importing a theme, duplicating one, or assigning a sound to a role in the theme editor all
copy files in. If themes sat inside the package, the first time you edited one the
extension would silently un-approve itself, and Audio Themes would simply stop loading the
next time Orca started, with nothing but a debug-log line to say why. Keeping themes
outside means the package holds only code, whose hash changes only when the code does.

## Creating Custom Themes

A theme is a directory containing WAV files named after UI roles. Place your theme in `~/.local/share/orca/audio-themes/themes/<your-theme>/` with an `info.json`:

```json
{
    "name": "My Theme",
    "summary": "A custom audio theme",
    "author": "Your Name"
}
```

Sound files should be short (50-200ms) WAV files. See the `default` theme for the complete list of filenames.

You can also use the Theme Editor (Orca+Ctrl+A, Theme Editor page) to change individual sounds without manual file management. Any sounds missing from your theme will automatically fall back to the default theme.

## Credits

- **Default theme sounds**: sourced from the [NVDA Audio Themes](https://github.com/mush42/NVDA-Audio-Themes) add-on (GPL v2+), originally from the Unspoken add-on by Austin Hicks and Bryan Smart, and TWBlue
- **Original NVDA add-on**: Musharraf Omer
- **Concept**: Inspired by NVDA's audio themes / Unspoken 3D Audio

## License

GNU General Public License v2.0 — see [COPYING](COPYING).
