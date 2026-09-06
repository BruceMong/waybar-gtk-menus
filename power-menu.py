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
import subprocess

from menu_common import LayerPopup, run_popup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_SH = os.path.join(CONFIG_DIR, "remote-mode.sh")
# L'unité transitoire qui porte l'inhibiteur du mode remote.
REMOTE_UNIT = "remote-mode-inhibit.service"
DEVNULL = subprocess.DEVNULL


def remote_is_active():
    """Le mode remote inhibe-t-il vraiment la veille ?

    On interroge l'unité, et non le fichier témoin de $XDG_RUNTIME_DIR : le
    2026-09-06, ce témoin a survécu à la mort de l'inhibiteur et l'interrupteur
    est resté sur « activé » toute une nuit pendant que la machine dormait
    capot fermé. Un état affiché doit se déduire de ce qui agit.
    """
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", REMOTE_UNIT],
        stdout=DEVNULL, stderr=DEVNULL).returncode == 0


class PowerPopup(LayerPopup):
    def __init__(self):
        super().__init__("Power", width=280, margin_right=10)

        session = self.add_card()
        session.action("\U000f033e", "Verrouiller", on_click=self._lock)
        self.sw = session.toggle(
            "\ueb2f", "Mode Remote",
            remote_is_active(), self._toggle_remote)
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
    run_popup(PowerPopup, "waybar-power-menu")


if __name__ == "__main__":
    main()
