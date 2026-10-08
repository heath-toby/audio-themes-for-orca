# Changelog

## 2.0.0

Audio Themes is now a **first-class Orca 51 user extension** rather than an add-on that
loads itself from `orca-customizations.py`. This needs **Orca 51 or later**; version 1.1.1
remains the one to use on Orca 50 and earlier.

### Changed

- Packaged as an extension. `install.sh` installs to
  `~/.local/share/orca/extensions/audio_themes/` and approves it with
  `orca --approve-extension audio_themes`. Orca approves by content hash and refuses to
  load a package whose files changed since approval, so re-run `./install.sh` after
  editing anything in `audio_themes/` — it re-approves as part of installing.
- Orca+Ctrl+A is now a declared extension command instead of a keybinding registered by
  hand on an idle callback.
- Settings moved from the private `org.gnome.Orca.AudioThemes` GSettings schema to Orca's
  own per-extension store, which `dconf` keeps under
  `/org/gnome/orca/<profile>/extensions/audio-themes/`. **Nothing to do on upgrade:** the
  first run imports every value from the old schema and writes a marker so it never runs
  twice. The old schema and its values are left in place; `./uninstall.sh --purge` removes
  them.
- Themes moved out of the add-on folder to `~/.local/share/orca/audio-themes/themes/`,
  carried across by the installer the first time. This is load-bearing rather than tidy:
  importing a theme, duplicating one or assigning a sound in the theme editor all write
  files at runtime, and doing that inside the package would silently un-approve the
  extension, after which Audio Themes would simply stop loading.
- Teardown is honest about which thread it runs on. `on_disabled` removes the patches and
  the players; `on_shutdown` deliberately does nothing, because Orca runs shutdown hooks
  in a daemon thread and GLib source removal, GStreamer state changes and un-patching are
  all main-thread work.

### Fixed

- **Sticky browse and focus mode made no sound when triggered by their keys.** Orca builds
  those commands around a *bound method* captured during script setup — before user
  extensions are sent `on_ready` — so the stored callable never saw the class-level patch
  applied afterwards, and `enable_sticky_*_mode` does not route through
  `_set_presentation_mode`, so the other hook could not stand in for it. The commands are
  now wrapped as well as the class, with a guard so one keypress cannot produce two sounds.
- **Disabling Audio Themes and switching it back on killed every sound for the rest of the
  session.** Teardown shut the two GStreamer players down but left the module singletons
  pointing at them; a shut-down player has no pipeline, so `get_player()` kept handing back
  a corpse whose `play()` silently returned. Teardown now drops the singletons so the next
  use rebuilds. Nothing could hit this before 2.0.0 — nothing ever tore the patches down.
- **`uninstall.sh --purge` left every setting behind.** Orca sanitises the `audio_themes`
  namespace into `audio-themes` for the settings path, and the script reset
  `.../extensions/audio_themes/`, which does not exist — so it reported success and removed
  nothing. It now resets the sanitised path, and also clears
  `.../extensions/audiothemes/`, an orphan left by an older Orca whose sanitiser dropped
  the underscore instead of hyphenating it.
- Teardown no longer builds two GStreamer pipelines purely in order to destroy them when
  nothing had ever played a sound.
- A GStreamer bus message arriving after teardown no longer raises inside the callback, and
  shutting a player down now removes its bus signal watch instead of leaking a GSource and
  keeping the player alive for the session.
- The deferred "move Orca's streams to the chosen output device" callback binds the device
  name when it is scheduled, instead of reading a module global two seconds later that may
  by then belong to a different config object or to none.

### Added

- `tests-audio-themes.py` — smoke tests for the settings round-trip and legacy import, the
  sound inventory against the shipped default theme, the player teardown-and-rebuild
  behaviour, and the settings path `--purge` resets. The player rows and the `--purge` row
  are regression guards for the two bugs above.
- README sections on where themes live and why, upgrading from 1.x, and why the Settings
  button under Orca Preferences → User Extensions is deliberately inactive.

### Note on the extension preferences dialog

Orca 51 can generate a settings dialog for an extension that declares its preferences.
Audio Themes declares none on purpose, so that button stays inactive. The generated dialog
renders only simple declarative controls and cannot host the theme editor's per-role
Preview buttons, its sound choosers, or theme import and export, and there is no hook to
supply a custom dialog in its place. Orca+Ctrl+A remains the single place Audio Themes is
configured.

## 1.1.1

- Ship pre-generated mode-change sounds, so `sox` is no longer needed at install time.

## 1.1.0

- Password typing sound, audio output device selection, and bug fixes.

## 1.0.1

- Fix the window sound firing on combo box popups.

## 1.0.0

- First release: focus sounds by role, 2D positional audio, theme support with a built-in
  editor, NVDA `.atp` theme import, and the Orca+Ctrl+A settings dialog.
