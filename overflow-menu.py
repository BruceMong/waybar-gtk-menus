#!/usr/bin/env python3
"""Popup « ⋮ » : lanceur des modules cachés de la waybar.

Lit modules-hidden et liste les modules actuellement masqués. Cliquer sur une
ligne lance l'action normale du module (son menu / son on-click) — comme s'il
était dans la barre — puis ferme le popup. Les modules restent cachés de la
barre ; seul leur accès reste disponible ici.

La grille d'emoji couleur a laissé place à une liste : trois tuiles par rangée
imposaient de lire les noms en zigzag, et les emoji — seuls éléments colorés de
toute l'interface — attiraient l'œil bien au-delà de leur importance.
"""
import os
import subprocess

# menu_common fixe la version de GTK et fournit les cartes : ce popup n'a plus
# aucun widget à assembler à la main.
from menu_common import LayerPopup, apply_css, caption_label

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
AUTO_HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden-auto")
WB = CONFIG_DIR

# (ids waybar concernés, icône, label, action shell ou None si rien à lancer).
# Les actions reprennent les on-click définis dans config-full.
REGISTRY = [
    (["custom/chrome"], "\U000f02af", "Chrome", "setsid -f google-chrome-stable"),
    (["hyprland/window"], "\U000f05d0", "Fenêtre", None),
    (["mpris"], "\U000f0387", "Média", "playerctl play-pause"),
    (["clock#time"], "\U000f0954", "Heure",
     "google-chrome-stable https://calendar.google.com"),
    (["clock#date"], "\U000f00ed", "Date",
     "google-chrome-stable https://calendar.google.com"),
    (["privacy"], "\U000f0208", "Confidentialité", None),
    (["tray"], "\U000f02e3", "Tray", None),
    (["custom/dnd"], "\U000f009a", "Notifications", WB + "/notification-menu.py"),
    (["cpu"], "\U000f0322", "CPU", WB + "/system-monitor.sh"),
    (["temperature"], "\U000f050f", "Température", WB + "/system-monitor.sh"),
    (["memory"], "\U000f035b", "RAM", WB + "/system-monitor.sh"),
    (["disk"], "\U000f02ca", "Disque", None),
    (["systemd-failed-units"], "\U000f0026", "Services", None),
    (["custom/updates"], "\U000f03d7", "Mises à jour", WB + "/updates.sh --menu"),
    (["network"], "\U000f05a9", "Réseau", WB + "/network-menu.py"),
    (["bluetooth"], "\U000f00af", "Bluetooth", "blueman-manager"),
    (["pulseaudio#icon", "pulseaudio#percentage"], "\U000f057e", "Son",
     WB + "/sound-menu.py"),
    (["backlight"], "\U000f00e0", "Luminosité", WB + "/brightness-menu.py"),
    (["idle_inhibitor"], "\U000f0176", "Caféine", WB + "/caffeine-toggle.sh"),
    (["power-profiles-daemon"], "\U000f0241", "Profil énergie", None),
    (["battery"], "\U000f0079", "Batterie", WB + "/battery-menu.py"),
    (["custom/power"], "\U000f0425", "Power", WB + "/power-menu.py"),
]

def load_hidden():
    """Modules cachés à la main (œil / molette) + repliés par autofit.py."""
    hidden = set()
    for path in (HIDDEN_FILE, AUTO_HIDDEN_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                hidden |= {line.strip() for line in f if line.strip()}
        except FileNotFoundError:
            pass
    return hidden


class OverflowMenu(LayerPopup):
    def __init__(self):
        super().__init__("Modules cachés", width=300, margin_right=110)

        hidden = load_hidden()
        entries = [e for e in REGISTRY if any(i in hidden for i in e[0])]

        card = self.add_card()
        if not entries:
            card.custom(caption_label("Aucun module caché."))
            return

        for _ids, icon, name, action in entries:
            # Un module sans action (Tray, Disque…) n'est pas lançable : la
            # ligne reste, grisée, plutôt que de disparaître — sa présence
            # dans la liste dit qu'il est caché, ce qui est déjà une réponse.
            row = card.action(
                icon, name, chevron=bool(action),
                on_click=(lambda _b, a=action: self._on_launch(_b, a))
                if action else None)
            if not action:
                row.set_sensitive(False)
                row.set_tooltip_text("Ce module n'a pas d'action à lancer")

    def _on_launch(self, _btn, action):
        subprocess.Popen(action, shell=True, start_new_session=True,
                         cwd=CONFIG_DIR)
        self.close()


def main():
    apply_css()
    OverflowMenu().run()


if __name__ == "__main__":
    main()
