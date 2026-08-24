#!/usr/bin/env python3
"""Popup Son pour Waybar (style menu luminosité / notifications).

Actions courantes faites maison :
  - volume haut-parleur (slider) + Muet
  - volume micro (slider) + Micro coupé
  - choix de la sortie audio (si plusieurs périphériques)
Et un bouton « Réglages avancés » qui ouvre pavucontrol (le menu complet).
"""
import array
import os
import re
import signal
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

DEVNULL = subprocess.DEVNULL
SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
VOL_MAX = 150  # plafond cohérent avec le scroll waybar (-l 1.5)

# -- VU-mètre micro (capture PCM légère via parec) --
METER_RATE = 8000          # Hz, mono : largement suffisant pour un niveau visuel
METER_CHUNK = 480          # octets lus par boucle (~30 ms de s16le mono à 8 kHz)
METER_FPS_MS = 40          # période de rafraîchissement de la barre (~25 fps)
METER_DECAY = 0.30         # vitesse de retombée (0 = figé, 1 = instantané)

METER_CSS = b"""
levelbar trough {
    background-color: #313244;
    border-radius: 5px;
    min-height: 10px;
    padding: 0;
}
levelbar block { border-radius: 5px; }
levelbar block.filled, levelbar block.low { background-color: #a6e3a1; }
levelbar block.high { background-color: #f9e2af; }
levelbar block.full { background-color: #f38ba8; }
levelbar block.empty { background-color: transparent; }
"""


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""


def get_volume(target):
    """Renvoie (volume_en_%, muted)."""
    out = run(["wpctl", "get-volume", target])
    m = re.search(r"Volume:\s*([0-9.]+)", out)
    vol = int(round(float(m.group(1)) * 100)) if m else 0
    return vol, "[MUTED]" in out


def source_exists():
    return bool(re.search(r"Volume:", run(["wpctl", "get-volume", SOURCE])))


def list_sinks():
    """Renvoie [(name, description, is_default)]."""
    default = run(["pactl", "get-default-sink"]).strip()
    sinks, name = [], None
    for line in run(["pactl", "list", "sinks"]).splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            name = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else ""
        elif s.startswith("Description:") and name is not None:
            desc = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else name
            sinks.append((name, desc, name == default))
            name = None
    return sinks


class SoundPopup(LayerPopup):
    def __init__(self):
        super().__init__("Son", width=360, margin_right=70)
        self._timeouts = {}
        self.sink_buttons = []
        self._meter_peak = 0.0       # dernier pic écrit par le thread de capture
        self._meter_shown = 0.0      # valeur affichée (lissée)
        self._meter_stop = threading.Event()
        self._meter_proc = None

        vol, muted = get_volume(SINK)

        # -- Volume haut-parleur --
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>🔊  Haut-parleur</b>")
        self.box.pack_start(lbl, False, False, 0)
        self.scale_out = self._make_scale(vol, SINK)
        self.box.pack_start(self.scale_out, False, False, 0)

        self.box.pack_start(self._switch_row(
            "  Muet", muted, lambda sw, _p: self._set_mute(SINK, sw)),
            False, False, 0)

        # -- Volume micro --
        if source_exists():
            mvol, mmuted = get_volume(SOURCE)
            lbl = Gtk.Label(xalign=0)
            if mmuted:
                lbl.set_markup(
                    "<b>🎤  Micro  "
                    "<span color='#f38ba8'>(coupé)</span></b>")
            else:
                lbl.set_markup("<b>🎤  Micro</b>")
            self.box.pack_start(lbl, False, False, 0)
            self.scale_mic = self._make_scale(mvol, SOURCE)
            self.box.pack_start(self.scale_mic, False, False, 0)
            self.box.pack_start(self._switch_row(
                "  Micro coupé", mmuted,
                lambda sw, _p: self._set_mute(SOURCE, sw)),
                False, False, 0)

            # VU-mètre : niveau d'entrée en temps réel.
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(
                "<span size='small' color='#a6adc8'>Niveau d'entrée</span>")
            self.box.pack_start(lbl, False, False, 0)
            self.meter = Gtk.LevelBar.new_for_interval(0.0, 1.0)
            self.meter.set_mode(Gtk.LevelBarMode.CONTINUOUS)
            self.meter.add_offset_value("low", 0.55)
            self.meter.add_offset_value("high", 0.80)
            self.meter.add_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL, 1.0)
            self._apply_meter_css()
            self.box.pack_start(self.meter, False, False, 0)

            self._start_meter()
            GLib.timeout_add(METER_FPS_MS, self._refresh_meter)
            self.connect("destroy", lambda *_: self._stop_meter())

        # -- Choix de la sortie --
        sinks = list_sinks()
        if len(sinks) > 1:
            self.box.pack_start(Gtk.Separator(), False, False, 0)
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<b>󰓃  Sortie</b>")
            self.box.pack_start(lbl, False, False, 0)
            for name, desc, is_def in sinks:
                btn = Gtk.Button(label=("󰄬  " if is_def else "") + desc)
                if is_def:
                    btn.get_style_context().add_class("accent")
                btn.connect("clicked", self._select_sink, name)
                self.sink_buttons.append((btn, name))
                self.box.pack_start(btn, False, False, 0)

        # -- Réglages avancés --
        self.box.pack_start(Gtk.Separator(), False, False, 0)
        if source_exists():
            btn = Gtk.Button(label="󰜉  Réparer le micro (démute + reset)")
            btn.connect("clicked", self._fix_mic)
            self.box.pack_start(btn, False, False, 0)
        btn = Gtk.Button(label="󰒓  Réglages avancés (pavucontrol)")
        btn.connect("clicked", self._open_pavucontrol)
        self.box.pack_start(btn, False, False, 0)

    # ---- Construction ----

    def _make_scale(self, value, target):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, VOL_MAX, 5)
        scale.set_value(value)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_digits(0)
        scale.add_mark(100, Gtk.PositionType.BOTTOM, None)
        scale.connect("value-changed", self._on_volume_changed, target)
        return scale

    def _switch_row(self, title, active, handler):
        row = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b>" % title)
        row.pack_start(lbl, True, True, 0)
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(active)
        sw.connect("notify::active", handler)
        row.pack_end(sw, False, False, 0)
        return row

    # ---- Handlers ----

    def _on_volume_changed(self, scale, target):
        # Debounce : éviter un appel wpctl à chaque pixel de drag.
        if target in self._timeouts:
            GLib.source_remove(self._timeouts[target])
        self._timeouts[target] = GLib.timeout_add(
            60, self._apply_volume, target, int(scale.get_value()))

    def _apply_volume(self, target, percent):
        self._timeouts.pop(target, None)
        subprocess.Popen(["wpctl", "set-volume", target, "%d%%" % percent],
                         stdout=DEVNULL, stderr=DEVNULL)
        return False

    def _set_mute(self, target, switch):
        subprocess.Popen(["wpctl", "set-mute", target,
                          "1" if switch.get_active() else "0"],
                         stdout=DEVNULL, stderr=DEVNULL)

    def _select_sink(self, _btn, name):
        subprocess.run(["pactl", "set-default-sink", name],
                       stdout=DEVNULL, stderr=DEVNULL)
        # Déplacer les flux en cours vers la nouvelle sortie.
        for line in run(["pactl", "list", "short", "sink-inputs"]).splitlines():
            idx = line.split("\t", 1)[0].strip()
            if idx:
                subprocess.run(["pactl", "move-sink-input", idx, name],
                               stdout=DEVNULL, stderr=DEVNULL)
        # Mettre à jour l'accent sur les boutons.
        for btn, bname in self.sink_buttons:
            ctx = btn.get_style_context()
            label = btn.get_label().lstrip("󰄬 ").strip()
            if bname == name:
                ctx.add_class("accent")
                btn.set_label("󰄬  " + label)
            else:
                ctx.remove_class("accent")
                btn.set_label(label)

    def _open_pavucontrol(self, _btn):
        subprocess.Popen(["pavucontrol"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _fix_mic(self, _btn):
        # Lance le reset à chaud en arrière-plan (le résultat arrive en
        # notification swaync), puis ferme le popup.
        fixmic = os.path.expanduser("~/.local/bin/fix-mic")
        subprocess.Popen([fixmic], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    # ---- VU-mètre micro ----

    def _apply_meter_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(METER_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def _start_meter(self):
        try:
            self._meter_proc = subprocess.Popen(
                ["parec", "--format=s16le",
                 "--rate=%d" % METER_RATE, "--channels=1",
                 "--latency-msec=30"],
                stdout=subprocess.PIPE, stderr=DEVNULL)
        except Exception:
            self._meter_proc = None
            return
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _meter_loop(self):
        proc = self._meter_proc
        while not self._meter_stop.is_set() and proc.poll() is None:
            data = proc.stdout.read(METER_CHUNK)
            if not data:
                break
            samples = array.array("h")
            try:
                samples.frombytes(data)
            except (ValueError, EOFError):
                continue
            if not samples:
                continue
            # Retire l'offset DC (certains DMIC ont un décalage constant) pour
            # que le niveau retombe bien à zéro dans le silence.
            dc = sum(samples) / len(samples)
            peak = max((abs(s - dc) for s in samples), default=0) / 32768.0
            self._meter_peak = peak

    def _refresh_meter(self):
        if self._meter_stop.is_set():
            return GLib.SOURCE_REMOVE
        target = self._meter_peak
        if target >= self._meter_shown:
            self._meter_shown = target          # montée instantanée
        else:
            self._meter_shown += (target - self._meter_shown) * METER_DECAY
        self.meter.set_value(min(self._meter_shown, 1.0))
        return GLib.SOURCE_CONTINUE

    def _stop_meter(self):
        self._meter_stop.set()
        if self._meter_proc and self._meter_proc.poll() is None:
            self._meter_proc.terminate()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    SoundPopup().run()


if __name__ == "__main__":
    main()
