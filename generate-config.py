#!/usr/bin/env python3
"""Génère config-active à partir de config-full en retirant les modules cachés.

Source de vérité : config-full (toutes les définitions + ordre complet des modules).
État manuel     : modules-hidden      (un id de module caché par ligne)
État automatique: modules-hidden-auto (posé par autofit.py quand la barre déborde)
Sortie          : config-active (lu par waybar via le symlink `config`).
"""
import json
import os

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(CONFIG_DIR, "config-full")
ACTIVE = os.path.join(CONFIG_DIR, "config-active")
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
AUTO_HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden-auto")

ZONES = ("modules-left", "modules-center", "modules-right")

# Mode "dizaines" (+10) : fichier témoin posé/retiré par ws-tens-toggle.sh
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
TENS_FLAG = os.path.join(RUNTIME_DIR, "waybar-ws-tens")
TENS_COLOR = "#ebebf0"  # encre primaire (barre monochrome)

# Mode "discret" : fichier témoin posé/retiré par stealth.sh
# Quand actif, toute la barre est masquée sauf l'œil (clic = ressortir).
STEALTH_FLAG = os.path.join(RUNTIME_DIR, "waybar-stealth")
EYE = "custom/toggle-info"

# Format de l'heure : drapeau posé par le menu de l'horloge (calendar-menu.py).
# Fichier plutôt que clé de config parce que c'est un réglage de machine, pas
# une décision de dépôt — au même titre que modules-hidden-auto.
CLOCK_12H_FLAG = os.path.join(CONFIG_DIR, "clock-12h")

# Groupes dont le premier module n'est qu'une poignée décorative : le groupe
# n'a plus de sens si tout son contenu réel est masqué.
GROUP_HANDLES = {"group/systray": "custom/tray-handle"}

# Module servant de point d'entrée au menu des modules cachés : reçoit un
# compteur quand autofit.py a replié des modules.
DOTS = "custom/dots"


def apply_defaults(cfg):
    """Applique les blocs communs de la clé "defaults" aux modules concernés.

    "defaults": {"clock": {...}} -> fusionné dans clock, clock#time, clock#date…
    (les clés déjà présentes dans le module gagnent). La clé est ensuite
    retirée : waybar ne doit pas la voir.
    """
    defaults = cfg.pop("defaults", None)
    if not defaults:
        return
    for base, common in defaults.items():
        for key in cfg:
            if key == base or key.startswith(base + "#"):
                for opt, val in common.items():
                    cfg[key].setdefault(opt, val)


# Capteurs CPU acceptés pour la résolution de "hwmon-path-abs": "auto",
# par ordre de préférence (AMD, Intel, puis repli ACPI).
CPU_SENSORS = ("k10temp", "coretemp", "zenpower", "cpu_thermal", "acpitz")


def find_cpu_hwmon():
    """Renvoie le dossier parent du hwmon CPU, ou None si aucun ne convient.

    Waybar attend dans `hwmon-path-abs` le dossier *contenant* les entrées
    hwmonN, pas l'entrée elle-même : on remonte donc d'un cran.
    """
    base = "/sys/class/hwmon"
    found = {}
    try:
        entries = os.listdir(base)
    except OSError:
        return None
    for entry in entries:
        try:
            with open(os.path.join(base, entry, "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        real = os.path.realpath(os.path.join(base, entry))
        if os.path.exists(os.path.join(real, "temp1_input")):
            found.setdefault(name, os.path.dirname(real))
    for sensor in CPU_SENSORS:
        if sensor in found:
            return found[sensor]
    return None


def apply_temperature(cfg):
    """Résout "hwmon-path-abs": "auto" vers le capteur CPU de la machine.

    Le chemin sysfs dépend du matériel : le coder en dur rendrait la config
    inutilisable ailleurs. Si aucun capteur connu n'est trouvé, les deux clés
    sont retirées et waybar retombe sur sa propre détection.
    """
    temp = cfg.get("temperature")
    if not temp or temp.get("hwmon-path-abs") != "auto":
        return
    path = find_cpu_hwmon()
    if path:
        temp["hwmon-path-abs"] = path
    else:
        temp.pop("hwmon-path-abs", None)
        temp.pop("input-filename", None)


def apply_clock_format(cfg):
    """Heure sur 12 h (AM/PM) tant que le drapeau est là, sur 24 h sinon.

    La locale bascule avec le format : en français `%p` ne rend rien du tout
    — « 06:42 » suivi d'un espace, le matin ne se distinguant plus du soir.
    Seul ce module change de locale ; sa grille de calendrier au survol passe
    en anglais avec lui, ce qui est le prix du choix, la date d'à côté restant
    française.
    """
    clock = cfg.get("clock#time")
    if not clock or not os.path.exists(CLOCK_12H_FLAG):
        return
    # %I et non %-I : le modificateur GNU qui supprime le zéro initial n'est
    # pas compris par le formateur de waybar, qui rend alors un module vide.
    clock["format"] = "{:%I:%M %p}"
    clock["locale"] = "en_US.UTF-8"


def apply_stealth(cfg):
    """Mode discret : ne garde que l'œil, barre transparente sans espace réservé."""
    cfg["modules-left"] = []
    cfg["modules-center"] = []
    cfg["modules-right"] = [EYE]
    cfg["exclusive"] = False  # ne réserve aucun espace à l'écran
    cfg["name"] = "stealth"   # -> sélecteur CSS window#waybar.stealth


def apply_ws_mode(cfg):
    """Adapte le module workspaces selon le mode dizaines (+10).

    - mode unités   : chiffres normaux
    - mode dizaines : chiffres teintés

    Le masquage de la décade d'en face a disparu avec la bascule du module sur
    `ext/workspaces` (config-full) : `hyprland/workspaces` changeait de bureau
    en envoyant `dispatch workspace <n>` sur le socket IPC, syntaxe hyprlang
    qu'Hyprland refuse depuis que la config est en Lua — le clic ne faisait
    plus rien, en silence. `ext/workspaces` passe par le protocole
    ext-workspace-v1, mais n'a pas d'option `ignore-workspaces` (waybar
    l'accepte sans rien en faire). La barre montre donc les bureaux occupés
    des deux décades ; seule la teinte dit dans laquelle tapent les touches.
    """
    ws = cfg.get("ext/workspaces")
    if not ws:
        return
    if os.path.exists(TENS_FLAG):
        ws["format"] = "<span color='%s'>{name}</span>" % TENS_COLOR
    else:
        ws["format"] = "{name}"


def apply_overflow_badge(cfg, auto_hidden):
    """Ajoute un compteur sur « ⋮ » quand autofit a replié des modules.

    Sans ce retour visuel, un module qui disparaît tout seul passe pour un bug :
    le badge indique qu'il est rangé dans le menu, pas perdu.
    """
    dots = cfg.get(DOTS)
    if not dots:
        return
    n = len(auto_hidden)
    if n:
        dots["format"] = "⋮ <span color='#ebebf0'>%d</span>" % n
        dots["tooltip"] = (
            "%d module(s) repliés faute de place — clic pour y accéder\n"
            "molette bas : cacher un module | molette haut : ressortir" % n
        )


def filter_groups(cfg, hidden):
    """Filtre les modules cachés à l'intérieur des groupes (group/*).

    Un module caché est retiré de la liste "modules" de son groupe ;
    un groupe vide — ou réduit à sa seule poignée — est retiré des zones.
    """
    for key in [k for k in cfg if k.startswith("group/")]:
        mods = cfg[key].get("modules", [])
        mods = [m for m in mods if m not in hidden]
        cfg[key]["modules"] = mods
        handle = GROUP_HANDLES.get(key)
        empty = not mods or (handle and mods == [handle])
        if empty:
            for zone in ZONES:
                if zone in cfg:
                    cfg[zone] = [m for m in cfg[zone] if m != key]


def read_lines(path):
    """Lit un fichier « un identifiant de module par ligne ».

    Les lignes vides et les commentaires sont ignorés. Le filtre sur « # » a
    son importance : modules-priority, le fichier voisin lu par autofit.py, est
    abondamment commenté, ce qui invite à commenter modules-hidden de la même
    façon. Sans ce filtre, la ligne de commentaire devenait un identifiant de
    module fantôme — silencieusement, puisqu'un id inconnu ne masque rien et ne
    lève aucune erreur.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return {
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            }
    except FileNotFoundError:
        return set()


def main():
    with open(TEMPLATE, encoding="utf-8") as f:
        cfg = json.load(f)

    apply_defaults(cfg)
    apply_clock_format(cfg)     # après apply_defaults : il en surcharge la locale
    apply_temperature(cfg)

    manual = read_lines(HIDDEN_FILE)
    auto = read_lines(AUTO_HIDDEN_FILE) - manual
    hidden = manual | auto

    for zone in ZONES:
        if zone in cfg:
            cfg[zone] = [m for m in cfg[zone] if m not in hidden]

    filter_groups(cfg, hidden)
    apply_ws_mode(cfg)
    apply_overflow_badge(cfg, auto)

    if os.path.exists(STEALTH_FLAG):
        apply_stealth(cfg)

    with open(ACTIVE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
        f.write("\n")


if __name__ == "__main__":
    main()
