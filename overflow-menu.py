#!/usr/bin/env python3
"""Popup « ⋮ » : lanceur des modules cachés de la waybar.

Lit modules-hidden et affiche une grille d'icônes, une par module
actuellement masqué. Cliquer sur une icône lance l'action normale du
module (son menu / son on-click) — comme s'il était dans la barre —
puis ferme le popup. Les modules restent cachés de la barre ; seul leur
accès reste disponible ici.
"""
import os
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from menu_common import LayerPopup, apply_css  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
AUTO_HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden-auto")
WB = CONFIG_DIR

# (ids waybar concernés, icône, label, action shell ou None si rien à lancer).
# Les actions reprennent les on-click définis dans config-full.
REGISTRY = [
    (["custom/chrome"], "󰊯", "Chrome", "setsid -f google-chrome-stable"),
    (["hyprland/window"], "🪟", "Fenêtre", None),
    (["mpris"], "🎵", "Média", "playerctl play-pause"),
    (["clock#time"], "🕐", "Heure",
     "google-chrome-stable https://calendar.google.com"),
    (["clock#date"], "📅", "Date",
     "google-chrome-stable https://calendar.google.com"),
    (["privacy"], "🕵️", "Confidentialité", None),
    (["tray"], "📥", "Tray", None),
    (["custom/dnd"], "🔔", "Notifications", WB + "/notification-menu.py"),
    (["cpu"], "⚙️", "CPU", WB + "/system-monitor.sh"),
    (["temperature"], "🌡️", "Température", WB + "/system-monitor.sh"),
    (["memory"], "💾", "RAM", WB + "/system-monitor.sh"),
    (["disk"], "💽", "Disque", None),
    (["systemd-failed-units"], "🚨", "Services", None),
    (["custom/updates"], "📦", "Mises à jour", WB + "/updates.sh --menu"),
    (["network"], "📶", "Réseau", WB + "/network-menu.py"),
    (["bluetooth"], "🔵", "Bluetooth", "blueman-manager"),
    (["pulseaudio#icon", "pulseaudio#percentage"], "🔊", "Son",
     WB + "/sound-menu.py"),
    (["backlight"], "☀️", "Luminosité", WB + "/brightness-menu.py"),
    (["idle_inhibitor"], "☕", "Caféine", WB + "/caffeine-toggle.sh"),
    (["power-profiles-daemon"], "⚡", "Profil énergie", None),
    (["battery"], "🔋", "Batterie", WB + "/battery-menu.py"),
    (["custom/power"], "⏻", "Power", WB + "/power-menu.py"),
]

EXTRA_CSS = b"""
button.tile {
    padding: 12px 8px;
    min-width: 84px;
    border-radius: 10px;
}
.tile-icon { font-size: 24px; }
.tile-name { font-size: 12px; color: #9a9aa2; margin-top: 4px; }
.empty { color: #68686f; font-size: 13px; margin: 8px 0; }
"""


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
        super().__init__("Modules cachés", width=320, margin_right=110)

        provider = Gtk.CssProvider()
        provider.load_from_data(EXTRA_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        hidden = load_hidden()
        entries = [e for e in REGISTRY if any(i in hidden for i in e[0])]

        if not entries:
            lbl = Gtk.Label(label="Aucun module caché.")
            lbl.get_style_context().add_class("empty")
            self.box.pack_start(lbl, False, False, 0)
            return

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(3)
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_homogeneous(True)
        self.box.pack_start(flow, True, True, 0)

        for _ids, icon, name, action in entries:
            flow.add(self._make_tile(icon, name, action))

    def _make_tile(self, icon, name, action):
        btn = Gtk.Button()
        btn.get_style_context().add_class("tile")
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        ic = Gtk.Label(label=icon)
        ic.get_style_context().add_class("tile-icon")
        nm = Gtk.Label(label=name)
        nm.get_style_context().add_class("tile-name")
        vbox.pack_start(ic, False, False, 0)
        vbox.pack_start(nm, False, False, 0)
        btn.add(vbox)
        if action:
            btn.connect("clicked", self._on_launch, action)
        else:
            btn.set_sensitive(False)
        return btn

    def _on_launch(self, _btn, action):
        subprocess.Popen(action, shell=True, start_new_session=True,
                         cwd=CONFIG_DIR)
        self.close()


def main():
    apply_css()
    OverflowMenu().run()


if __name__ == "__main__":
    main()
