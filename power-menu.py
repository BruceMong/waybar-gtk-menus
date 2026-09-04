#!/usr/bin/env python3
"""Popup Power pour Waybar (vocabulaire commun : cartes + lignes).

Deux cartes, parce qu'il y a deux natures d'action :
  - la session : verrouiller, mode Remote, mise en veille — réversible d'un
    geste,
  - le courant : redémarrer, éteindre — on perd ce qui n'est pas enregistré.

Le regroupement fait le travail que faisaient les couleurs : plus besoin d'un
aplat rouge pleine largeur pour dire « attention », la carte du bas suffit à
séparer les deux mondes et le libellé rouge confirme.
"""
import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_SH = os.path.join(CONFIG_DIR, "remote-mode.sh")
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
REMOTE_FLAG = os.path.join(RUNTIME_DIR, "remote-mode-active")
DEVNULL = subprocess.DEVNULL


class PowerPopup(LayerPopup):
    def __init__(self):
        super().__init__("Power", width=280, margin_right=10)

        session = self.add_card()
        session.action("\U000f033e", "Verrouiller", on_click=self._lock)
        self.sw = session.toggle(
            "\ueb2f", "Mode Remote",
            os.path.exists(REMOTE_FLAG), self._toggle_remote)
        session.action("⏾", "Mise en veille",
                       on_click=lambda *_: self._power(["systemctl", "suspend"]))

        power = self.add_card()
        power.action("\U000f0709", "Redémarrer", destructive=True,
                     on_click=lambda *_: self._power(["systemctl", "reboot"]))
        power.action("⏻", "Éteindre", destructive=True,
                     on_click=lambda *_: self._power(["systemctl", "poweroff"]))

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
