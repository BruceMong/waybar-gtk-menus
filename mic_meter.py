"""VU-mètre du micro par défaut, partagé par les popups Son et Enregistrer.

Capture PCM légère via parec dans un thread ; la barre est redessinée à
~25 fps depuis la boucle GTK. La capture démarre à la création du widget et
s'arrête à sa destruction : rien ne tourne une fois le popup fermé.
"""
import array
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from menu_common import caption_label  # noqa: E402

METER_RATE = 8000          # Hz, mono : largement suffisant pour un niveau visuel
METER_CHUNK = 480          # octets lus par boucle (~30 ms de s16le mono à 8 kHz)
METER_FPS_MS = 40          # période de rafraîchissement de la barre (~25 fps)
METER_DECAY = 0.30         # vitesse de retombée (0 = figé, 1 = instantané)

METER_CSS = b"""
levelbar trough {
    background-color: rgba(255, 255, 255, 0.10);
    border-radius: 4px;
    min-height: 8px;
    padding: 0;
    border: none;
}
levelbar block { border-radius: 4px; }
levelbar block.filled, levelbar block.low { background-color: #32d74b; }
levelbar block.high { background-color: #ffd60a; }
levelbar block.full { background-color: #ff453a; }
levelbar block.empty { background-color: transparent; }
"""


class MicMeter(Gtk.Box):
    """Libellé « Niveau d'entrée » + barre de niveau, capture comprise."""

    def __init__(self, label="Niveau d'entrée"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._peak = 0.0       # dernier pic écrit par le thread de capture
        self._shown = 0.0      # valeur affichée (lissée)
        self._stop = threading.Event()
        self._proc = None
        self._timer = None

        self.pack_start(caption_label(label), False, False, 0)
        self.bar = Gtk.LevelBar.new_for_interval(0.0, 1.0)
        self.bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self.bar.add_offset_value("low", 0.55)
        self.bar.add_offset_value("high", 0.80)
        self.bar.add_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL, 1.0)
        self.bar.set_valign(Gtk.Align.CENTER)
        self.pack_start(self.bar, False, False, 0)

        provider = Gtk.CssProvider()
        provider.load_from_data(METER_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        self._start()
        self.connect("destroy", lambda *_: self.stop())

    def restart(self):
        """Recommence la capture sur la source par défaut du moment."""
        self.stop()
        self._stop.clear()
        self._peak = 0.0
        self._shown = 0.0
        self._start()

    def stop(self):
        self._stop.set()
        # Retiré tout de suite : restart() relance un timer aussitôt, et
        # l'ancien, pas encore passé par _refresh, survivrait au clear().
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _start(self):
        try:
            self._proc = subprocess.Popen(
                ["parec", "--format=s16le",
                 "--rate=%d" % METER_RATE, "--channels=1",
                 "--latency-msec=30"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception:
            self._proc = None
            return
        threading.Thread(target=self._loop, daemon=True).start()
        self._timer = GLib.timeout_add(METER_FPS_MS, self._refresh)

    def _loop(self):
        proc = self._proc
        while not self._stop.is_set() and proc.poll() is None:
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
            self._peak = max((abs(s - dc) for s in samples), default=0) / 32768.0

    def _refresh(self):
        if self._stop.is_set():
            self._timer = None
            return GLib.SOURCE_REMOVE
        target = self._peak
        if target >= self._shown:
            self._shown = target          # montée instantanée
        else:
            self._shown += (target - self._shown) * METER_DECAY
        self.bar.set_value(min(self._shown, 1.0))
        return GLib.SOURCE_CONTINUE
