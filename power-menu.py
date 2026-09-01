#!/usr/bin/env python3
"""Popup Power pour Waybar (style menu luminosité / notifications).

Fenêtre overlay ancrée en haut à droite avec :
  - Verrouiller (hyprlock)
  - Mode Remote (interrupteur, bascule via remote-mode.sh)
  - Mise en veille
  - Redémarrer / Éteindre (boutons rouges)
"""
import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_SH = os.path.join(CONFIG_DIR, "remote-mode.sh")
REMOTE_FLAG = "/tmp/remote-mode-active"
DEVNULL = subprocess.DEVNULL

# CSS local : boutons « danger » rouges (en plus du thème commun).
EXTRA_CSS = b"""
button.danger { background-color: #45293a; color: #ff453a; }
button.danger:hover { background-color: #ff453a; color: #ffffff; }
"""


def apply_extra_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(EXTRA_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


class PowerPopup(LayerPopup):
    def __init__(self):
        super().__init__("Power", width=280, margin_right=10)
        apply_extra_css()

        # -- Verrouiller --
        btn = Gtk.Button(label="  Verrouiller")
        btn.connect("clicked", self._lock)
        self.box.pack_start(btn, False, False, 0)

        # -- Mode Remote (interrupteur) --
        row = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>  Mode Remote</b>")
        row.pack_start(lbl, True, True, 0)
        self.sw = Gtk.Switch()
        self.sw.set_valign(Gtk.Align.CENTER)
        self.sw.set_active(os.path.exists(REMOTE_FLAG))
        self.sw.connect("notify::active", self._toggle_remote)
        row.pack_end(self.sw, False, False, 0)
        self.box.pack_start(row, False, False, 0)

        # -- Mise en veille --
        btn = Gtk.Button(label="⏾  Mise en veille")
        btn.connect("clicked", lambda *_: self._power(["systemctl", "suspend"]))
        self.box.pack_start(btn, False, False, 0)

        self.box.pack_start(Gtk.Separator(), False, False, 0)

        # -- Redémarrer / Éteindre --
        btn = Gtk.Button(label="  Redémarrer")
        btn.get_style_context().add_class("danger")
        btn.connect("clicked", lambda *_: self._power(["systemctl", "reboot"]))
        self.box.pack_start(btn, False, False, 0)

        btn = Gtk.Button(label="⏻  Éteindre")
        btn.get_style_context().add_class("danger")
        btn.connect("clicked", lambda *_: self._power(["systemctl", "poweroff"]))
        self.box.pack_start(btn, False, False, 0)

    # ---- Handlers ----

    def _lock(self, _btn):
        subprocess.Popen(["hyprlock"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _toggle_remote(self, _switch, _param):
        # remote-mode.sh bascule selon l'état du drapeau : un appel suffit.
        subprocess.Popen([REMOTE_SH], stdout=DEVNULL, stderr=DEVNULL)

    def _power(self, cmd):
        subprocess.Popen(cmd, stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    PowerPopup().run()


if __name__ == "__main__":
    main()
