#!/usr/bin/env python3
"""Inventaire des modules de la barre : source unique de vérité.

Deux listes décrivaient les mêmes modules et devaient rester alignées sans que
rien ne les y oblige : le REGISTRY d'overflow-menu.py (menu « ⋮ », qui donne
accès aux modules cachés) et le MODULES de module-menu.py (menu de l'œil, qui
décide lesquels sont cachés). Elles avaient divergé — `custom/ws-tens` pouvait
être caché par l'œil mais n'existait pas dans le ⋮, si bien qu'une fois masqué
il n'était plus atteignable nulle part ; et `systemd-failed-units` y figurait
sans action alors que systemd-menu.py existe.

Un module se décrit ici par :

  ids       identifiants waybar concernés (plusieurs quand un même réglage se
            présente en deux modules, comme l'icône et le pourcentage du son) ;
  icon      glyphe Material Design de la Nerd Font, pour le menu ⋮ ;
  label     nom affiché, le même des deux côtés ;
  action    commande à lancer depuis le ⋮ — reprise du on-click de config-full,
            ou None pour un module qui n'a rien à ouvrir (Tray, Disque…) ;
  hideable  False pour ce qui doit rester dans la barre en toutes circonstances
            (les poignées des tiroirs, l'œil lui-même) ;
  transient True pour un module qui se masque tout seul quand il n'a rien à
            dire (texte vide, `hide-on-ok`). Ceux-là ne sont pas « cachés » au
            sens de modules-hidden, mais leur menu doit rester joignable : sans
            cela, on ne peut plus forcer une vérification des mises à jour dès
            lors qu'il n'y en a aucune.
"""

import os

WB = os.path.dirname(os.path.abspath(__file__))


class Module:
    __slots__ = ("ids", "icon", "label", "action", "hideable", "transient")

    def __init__(self, ids, icon, label, action=None, hideable=True,
                 transient=False):
        self.ids = ids
        self.icon = icon
        self.label = label
        self.action = action
        self.hideable = hideable
        self.transient = transient

    def __repr__(self):                                   # debug
        return "<Module %s>" % self.ids[0]


def _script(name):
    return os.path.join(WB, name)


# L'ordre est celui de la barre, de gauche à droite : c'est lui que reprennent
# le menu de l'œil et le menu ⋮, pour qu'on retrouve un module là où on
# l'attend.
MODULES = [
    Module(["custom/ws-tens"], "\U000f0b35", "Bouton +10",
           _script("ws-tens-toggle.sh")),
    Module(["custom/chrome"], "\U000f02af", "Chrome",
           "setsid -f google-chrome-stable"),
    Module(["hyprland/window"], "\U000f05d0", "Fenêtre"),
    # transient : le module s'efface de lui-même dès qu'aucun lecteur ne
    # tourne (`format-stopped` vide dans config-full), et son menu doit
    # rester joignable pour autant.
    Module(["mpris"], "\U000f0387", "Média",
           _script("media-menu.py"), transient=True),
    Module(["clock#time"], "\U000f0954", "Heure",
           _script("calendar-menu.py") + " 60"),
    Module(["clock#date"], "\U000f00ed", "Date",
           _script("calendar-menu.py") + " 60"),
    Module(["privacy"], "\U000f0208", "Confidentialité"),
    Module(["tray"], "\U000f02e3", "Tray"),
    Module(["custom/calendar"], "\U000f00f0", "Agenda",
           _script("calendar-menu.py"), transient=True),
    Module(["custom/claude"], "\U000f09d1", "Sessions Claude",
           _script("claude-menu.py"), transient=True),
    Module(["custom/dnd"], "\U000f009a", "Notifications",
           _script("notification-menu.py")),
    Module(["custom/updates"], "\U000f03d7", "Mises à jour",
           _script("updates.sh") + " --menu", transient=True),
    Module(["systemd-failed-units"], "\U000f0026", "Services en échec",
           _script("systemd-menu.py"), transient=True),
    Module(["cpu"], "\U000f0322", "CPU", _script("system-monitor.sh")),
    Module(["temperature"], "\U000f050f", "Température",
           _script("system-monitor.sh")),
    Module(["memory"], "\U000f035b", "RAM", _script("system-monitor.sh")),
    Module(["disk"], "\U000f02ca", "Disque"),
    Module(["network"], "\U000f05a9", "Réseau", _script("network-menu.py")),
    Module(["bluetooth"], "\U000f00af", "Bluetooth",
           _script("bluetooth-menu.py")),
    Module(["pulseaudio#mic"], "\U000f036c", "Micro",
           _script("sound-menu.py")),
    Module(["pulseaudio#icon", "pulseaudio#percentage"], "\U000f057e", "Son",
           _script("sound-menu.py")),
    Module(["backlight"], "\U000f00e0", "Luminosité",
           _script("brightness-menu.py")),
    Module(["idle_inhibitor"], "\U000f0176", "Caféine",
           _script("caffeine-toggle.sh")),
    Module(["power-profiles-daemon"], "\U000f0241", "Profil énergie",
           "powerprofilesctl set balanced"),
    Module(["battery"], "\U000f0079", "Batterie", _script("battery-menu.py")),
    Module(["custom/power"], "\U000f0425", "Power", _script("power-menu.py")),

    # Témoins d'enregistrement : ils n'apparaissent que pendant une capture et
    # s'effacent seuls le reste du temps. Les masquer n'aurait pas de sens (on
    # ne cache pas un voyant qui ne s'allume qu'à propos), et le ⋮ n'a rien à
    # leur proposer — « arrêter l'enregistrement » quand aucun ne tourne. Ils
    # figurent ici pour que l'inventaire couvre bien toute la barre.
    Module(["custom/voice-rec"], "\U000f036c", "Enregistrement vocal",
           hideable=False),
    Module(["custom/recorder"], "\U000f0567", "Capture d'écran",
           hideable=False),

    # Poignées et points d'entrée : les masquer reviendrait à se priver du
    # moyen de les faire revenir.
    Module(["custom/tray-handle"], "\U000f02e3", "Poignée du tray",
           hideable=False),
    Module(["custom/dots"], "⋮", "Modules cachés",
           _script("overflow-menu.py"), hideable=False),
    Module(["custom/toggle-info"], "\U000f0208", "Œil",
           _script("eye.sh"), hideable=False),
    Module(["custom/keybinds"], "\U000f030c", "Raccourcis",
           _script("keybinds-menu.py"), hideable=False),
]


def hideable():
    """Modules que l'œil peut masquer, dans l'ordre de la barre."""
    return [m for m in MODULES if m.hideable]


def by_id(module_id):
    return next((m for m in MODULES if module_id in m.ids), None)
