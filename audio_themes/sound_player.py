"""GStreamer-based sound player with 2D positional audio.

Pipeline:
    filesrc ! decodebin ! audioconvert ! equalizer-3bands ! audiopanorama ! volume ! autoaudiosink

Horizontal positioning uses audiopanorama (left-right panning).
Vertical positioning uses equalizer-3bands (brighter=above, warmer=below).
"""

from __future__ import annotations

import logging
import os

import gi
gi.require_version("Gst", "1.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gst, GLib, Gdk

_log = logging.getLogger("orca-audio-themes")

# Maximum EQ boost/cut in dB for vertical positioning
_MAX_EQ_DB = 4.0

# Ensure GStreamer is initialised once
_gst_initialised = False


def _ensure_gst() -> None:
    global _gst_initialised
    if not _gst_initialised:
        Gst.init(None)
        _gst_initialised = True


def get_screen_size() -> tuple[int, int]:
    """Return (width, height) of the primary monitor."""
    display = Gdk.Display.get_default()
    if display is None:
        return 1920, 1080
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return 1920, 1080
    geom = monitor.get_geometry()
    return geom.width, geom.height


class AudioThemePlayer:
    """Plays sound files with optional 2D positional audio."""

    def __init__(self, device: str = "") -> None:
        _ensure_gst()
        self._device = device
        self._pipeline: Gst.Pipeline | None = None
        self._filesrc: Gst.Element | None = None
        self._decodebin: Gst.Element | None = None
        self._audioconvert: Gst.Element | None = None
        self._equalizer: Gst.Element | None = None
        self._panorama: Gst.Element | None = None
        self._volume: Gst.Element | None = None
        self._sink: Gst.Element | None = None
        self._build_pipeline()

    def _build_pipeline(self) -> None:
        """Construct the GStreamer pipeline."""
        self._pipeline = Gst.Pipeline.new("audio-themes-player")
        if self._pipeline is None:
            _log.error("AudioThemePlayer: could not create pipeline")
            return

        self._filesrc = Gst.ElementFactory.make("filesrc", "src")
        self._decodebin = Gst.ElementFactory.make("decodebin", "decode")
        self._audioconvert = Gst.ElementFactory.make("audioconvert", "convert")
        self._equalizer = Gst.ElementFactory.make("equalizer-3bands", "eq")
        self._panorama = Gst.ElementFactory.make("audiopanorama", "pan")
        self._volume = Gst.ElementFactory.make("volume", "vol")

        # Use pulsesink with specific device, or autoaudiosink for system default
        if self._device:
            self._sink = Gst.ElementFactory.make("pulsesink", "output")
            if self._sink is not None:
                self._sink.set_property("device", self._device)
        else:
            self._sink = Gst.ElementFactory.make("autoaudiosink", "output")

        elements = [
            self._filesrc, self._decodebin, self._audioconvert,
            self._equalizer, self._panorama, self._volume, self._sink,
        ]
        if any(e is None for e in elements):
            _log.error("AudioThemePlayer: missing GStreamer elements")
            self._pipeline = None
            return

        for e in elements:
            self._pipeline.add(e)

        # filesrc -> decodebin (decodebin pads are dynamic)
        self._filesrc.link(self._decodebin)
        # audioconvert -> equalizer -> panorama -> volume -> sink (static chain)
        self._audioconvert.link(self._equalizer)
        self._equalizer.link(self._panorama)
        self._panorama.link(self._volume)
        self._volume.link(self._sink)

        # Connect decodebin's dynamic pad to audioconvert
        self._decodebin.connect("pad-added", self._on_pad_added)

        # Set panorama to psychoacoustic method
        self._panorama.set_property("method", 1)  # 1 = psychoacoustic

        # Handle bus messages
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)

    def _on_pad_added(self, _decodebin: Gst.Element, pad: Gst.Pad) -> None:
        """Link decodebin's dynamic audio pad to audioconvert."""
        caps = pad.query_caps(None)
        struct = caps.get_structure(0)
        if struct and struct.get_name().startswith("audio/"):
            sink_pad = self._audioconvert.get_static_pad("sink")
            if sink_pad and not sink_pad.is_linked():
                pad.link(sink_pad)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        # A message can still be in flight when shutdown() has already
        # dropped the pipeline, so never assume it is still there.
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        error, info = msg.parse_error()
        _log.error("AudioThemePlayer: %s (%s)", error, info)
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)

    def play(
        self,
        filepath: str,
        pan: float = 0.0,
        elevation: float = 0.0,
        volume: float = 1.0,
        interrupt: bool = True,
    ) -> None:
        """Play a sound file with positional audio.

        Args:
            filepath: Absolute path to audio file.
            pan: Horizontal position, -1.0 (left) to 1.0 (right).
            elevation: Vertical position, -1.0 (bottom) to 1.0 (top).
            volume: Playback volume, 0.0 to 1.0.
            interrupt: If True, stop any currently playing sound first.
        """
        if self._pipeline is None:
            return
        if not os.path.isfile(filepath):
            return

        if interrupt:
            self._pipeline.set_state(Gst.State.NULL)

        # Set the file to play
        self._filesrc.set_property("location", filepath)

        # Horizontal panning
        pan = max(-1.0, min(1.0, pan))
        self._panorama.set_property("panorama", pan)

        # Vertical positioning via EQ
        elevation = max(-1.0, min(1.0, elevation))
        high_gain = elevation * _MAX_EQ_DB     # top=+4dB, bottom=-4dB
        low_gain = -elevation * _MAX_EQ_DB     # top=-4dB, bottom=+4dB
        self._equalizer.set_property("band0", low_gain)   # low band
        self._equalizer.set_property("band1", 0.0)        # mid band (neutral)
        self._equalizer.set_property("band2", high_gain)   # high band

        # Volume
        self._volume.set_property("volume", max(0.0, min(1.0, volume)))

        self._pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        """Stop any currently playing sound."""
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)

    def shutdown(self) -> None:
        """Release all GStreamer resources. The player is dead afterwards.

        A shut-down player can never play again -- play() finds no
        pipeline and returns. Anything holding one must therefore drop it;
        see reset_players(), which is what uninstall() calls.
        """
        self.stop()
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            # Otherwise the watch keeps a GSource -- and this object --
            # alive for the rest of the session.
            bus = pipeline.get_bus()
            if bus is not None:
                bus.remove_signal_watch()


# Module-level singletons
_player: AudioThemePlayer | None = None
_overlay_player: AudioThemePlayer | None = None
_current_device: str = ""


def get_player() -> AudioThemePlayer:
    """Return the primary AudioThemePlayer singleton (for role sounds)."""
    global _player
    if _player is None:
        _player = AudioThemePlayer(_current_device)
    return _player


def get_overlay_player() -> AudioThemePlayer:
    """Return a secondary player for sounds that should overlay the primary (mode sounds)."""
    global _overlay_player
    if _overlay_player is None:
        _overlay_player = AudioThemePlayer(_current_device)
    return _overlay_player


def set_output_device(device: str) -> None:
    """Change the audio output device. Rebuilds players on next use."""
    global _current_device
    if device == _current_device:
        return
    _current_device = device
    reset_players()


def reset_players() -> None:
    """Shut down both players and forget them, so the next use rebuilds.

    Shutting a player down without this leaves the singleton pointing at a
    corpse: get_player() hands back the same object, its pipeline is None,
    and play() silently does nothing for the rest of the session. That
    matters now that Orca can disable and re-enable an extension at will --
    before the extension system there was no path back from a teardown.
    """
    global _player, _overlay_player
    for player in (_player, _overlay_player):
        if player is not None:
            player.shutdown()
    _player = None
    _overlay_player = None


def move_orca_streams(sink_name: str) -> None:
    """Move Orca and Speech Dispatcher audio streams to the given PulseAudio sink."""
    import subprocess
    if not sink_name:
        return
    try:
        result = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse sink-input IDs and their application names
        current_id = None
        current_app = ""
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Sink Input #"):
                # Move the previous entry if it matched
                if current_id and _is_orca_stream(current_app):
                    subprocess.run(
                        ["pactl", "move-sink-input", current_id, sink_name],
                        timeout=5,
                    )
                current_id = line.split("#")[1]
                current_app = ""
            elif "application.name" in line:
                current_app = line.split("=", 1)[1].strip().strip('"')
        # Handle the last entry
        if current_id and _is_orca_stream(current_app):
            subprocess.run(
                ["pactl", "move-sink-input", current_id, sink_name],
                timeout=5,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def _is_orca_stream(app_name: str) -> bool:
    """Check if a PulseAudio stream belongs to Orca or Speech Dispatcher."""
    app_lower = app_name.lower()
    return "orca" in app_lower or "speech-dispatcher" in app_lower
