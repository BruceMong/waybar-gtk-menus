#!/usr/bin/env python3
"""Popup Raccourcis clavier pour Waybar (style menu luminosité / power).

Liste tous les binds Hyprland (hyprland.lua) groupés par section, avec
recherche, et permet de réassigner une combinaison en la capturant au
clavier. La ligne du fichier source est réécrite en place (après backup
.orig / .bak) puis `hyprctl reload` est déclenché.

La config est en Lua depuis la migration du 2026-09-02 (le format .conf
disparaît en Hyprland 0.57) : un bind s'écrit désormais
`hl.bind(mainMod .. " + SHIFT + R", hl.dsp.exec_cmd("..."))`. C'est cette
forme que le parseur lit et réécrit — une ligne par bind, ce pour quoi les
boucles `for` sont volontairement déroulées dans hyprland.lua.

Les binds souris (option `mouse`) sont affichés en lecture seule : leur
« touche » (mouse:272) n'est pas capturable au clavier.

Une dernière section, « À attribuer », liste des actions utiles restées sans
raccourci avec une combinaison libre proposée. Elle est purement indicative :
rien n'est écrit dans la config tant que la ligne de l'infobulle n'a pas été
collée à la main dans hyprland.lua.
"""
import os
import re
import shutil
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from menu_common import (Card, LayerPopup,  # noqa: E402
                         caption_label, section_label)

HYPR_DIR = os.path.expanduser("~/.config/hypr")
# plugins.lua ne contient plus de bind : les raccourcis des dispatchers de
# plugins (hyprexpo, Hypr-DarkWindow) vivent dans hyprland.lua sous leur forme
# `exec hyprctl dispatch`, qui se résout à l'appui sur la touche.
SOURCES = [os.path.join(HYPR_DIR, "hyprland.lua")]
DEVNULL = subprocess.DEVNULL

# `hl.bind(<touches>, <dispatcher>[, <options>])` sur une seule ligne.
BIND_RE = re.compile(r"^(?P<indent>\s*)hl\.bind\((?P<body>.*)\)\s*$")

# Une chaîne littérale Lua : "…", '…' ou [[…]].
# Ordre canonique d'affichage des modificateurs.
MOD_ORDER = ["SUPER", "CTRL", "ALT", "SHIFT"]
MOD_LABEL = {"SUPER": "Super", "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}

# Noms de touches Hyprland -> libellé lisible.
KEY_LABEL = {
    "left": "←", "right": "→", "up": "↑", "down": "↓",
    "print": "Impr", "return": "Entrée", "space": "Espace",
    "escape": "Échap", "tab": "Tab", "delete": "Suppr",
    "minus": "-", "underscore": "_", "apostrophe": "'", "quotedbl": '"',
    "ampersand": "&", "parenleft": "(", "parenright": ")",
    "eacute": "é", "egrave": "è", "agrave": "à", "ccedilla": "ç",
    "twosuperior": "²",
    "period": ".", "comma": ",", "semicolon": ";", "equal": "=",
    "prior": "Page ↑", "next": "Page ↓",
}

# Dispatchers Lua (hl.dsp.*) -> libellé lisible. `exec_cmd` est traité à part :
# pour lui, l'information utile est la commande, pas le nom du dispatcher.
DISPATCHER_LABEL = {
    "window.close": "Fermer la fenêtre",
    "window.kill": "Tuer la fenêtre",
    "window.float": "Basculer flottant",
    "window.fullscreen": "Plein écran",
    "window.pseudo": "Pseudo-tiling",
    "window.pin": "Épingler",
    "window.center": "Centrer la fenêtre",
    "window.move": "Déplacer la fenêtre",
    "window.resize": "Redimensionner",
    "window.drag": "Déplacer à la souris",
    "window.cycle_next": "Fenêtre suivante",
    "window.bring_to_top": "Ramener au premier plan",
    "window.swap": "Échanger les fenêtres",
    "window.tag": "Étiqueter la fenêtre",
    "focus": "Focus",
    "layout": "Layout",
    "exit": "Quitter Hyprland",
    "force_renderer_reload": "Recharger le rendu",
    "group.toggle": "Grouper / dégrouper",
    "group.next": "Fenêtre suivante du groupe",
    "group.active": "Onglet du groupe",
    "group.lock": "Verrouiller le groupe",
    "workspace.move": "Workspace vers l'écran",
    "workspace.toggle_special": "Workspace spécial",
    "workspace.rename": "Renommer le workspace",
    "cursor.move_to_corner": "Curseur vers un coin",
}

# Certains arguments sont des mots-clés, pas des valeurs : « Aller au workspace
# previous » ne se lit pas. La table remplace le couple entier quand il existe.
ARG_LABEL = {
    ("focus", 'workspace="previous"'): "Bureau précédent",
    ("focus", 'workspace="e+1"'): "Bureau suivant",
    ("focus", 'workspace="e-1"'): "Bureau précédent",
    ("window.cycle_next", "next=false"): "Fenêtre précédente",
    ("group.next", "forward=false"): "Fenêtre précédente du groupe",
    ("window.move", "out_of_group=true"): "Sortir du groupe",
}

# Actions courantes qui n'ont pas encore de raccourci, avec une combinaison
# restée libre qui leur irait. Rien n'est attribué ici : la section « À
# attribuer » les affiche en lecture seule et met la ligne Lua prête à coller
# dans l'infobulle. Une entrée disparaît d'elle-même dès que sa combinaison est
# prise dans hyprland.lua — c'est le signal qu'elle a été adoptée.
#
# `probe` sert quand la touche affichée n'est pas une vraie touche (« 1…0 ») :
# c'est elle qu'on cherche dans la config pour savoir si la suggestion tient
# toujours.
SUGGESTIONS_SECTION = "À attribuer (suggestions)"
SUGGESTIONS = [
    # -- fenêtres
    {"mods": ["SUPER", "CTRL"], "key": "C",
     "label": "Forcer la fermeture d'une fenêtre bloquée",
     "line": 'hl.bind(mainMod .. " + CTRL + C", hl.dsp.window.kill())'},
    {"mods": ["SUPER"], "key": "K",
     "label": "Centrer la fenêtre flottante",
     "line": 'hl.bind(mainMod .. " + K", hl.dsp.window.center())'},
    {"mods": ["SUPER"], "key": "O",
     "label": "Revenir à la fenêtre précédente",
     "line": 'hl.bind(mainMod .. " + O", hl.dsp.focus({ window = "last" }))'},
    {"mods": ["SUPER", "SHIFT"], "key": "Tab",
     "label": "Sélecteur de fenêtres (Walker)",
     "line": 'hl.bind(mainMod .. " + SHIFT + Tab", hl.dsp.exec_cmd("walker -m windows"))'},
    # -- workspaces
    {"mods": ["SUPER"], "key": "next",
     "label": "Workspace suivant (au clavier)",
     "line": 'hl.bind(mainMod .. " + next", hl.dsp.focus({ workspace = "e+1" }))'},
    {"mods": ["SUPER"], "key": "prior",
     "label": "Workspace précédent (au clavier)",
     "line": 'hl.bind(mainMod .. " + prior", hl.dsp.focus({ workspace = "e-1" }))'},
    {"mods": ["SUPER", "SHIFT"], "key": "next",
     "label": "Envoyer la fenêtre au workspace suivant",
     "line": 'hl.bind(mainMod .. " + SHIFT + next", hl.dsp.window.move({ workspace = "e+1" }))'},
    {"mods": ["SUPER", "SHIFT"], "key": "prior",
     "label": "Envoyer la fenêtre au workspace précédent",
     "line": 'hl.bind(mainMod .. " + SHIFT + prior", hl.dsp.window.move({ workspace = "e-1" }))'},
    {"mods": ["SUPER", "CTRL"], "key": "1…0", "probe": "ampersand",
     "label": "Envoyer au workspace N sans le suivre",
     "line": 'hl.bind(mainMod .. " + CTRL + ampersand", hl.dsp.exec_cmd('
             '"~/.config/hypr/scripts/ws-key.sh movetoworkspacesilent 1"))\n'
             "(idem pour les neuf autres chiffres)"},
    {"mods": ["SUPER"], "key": "comma",
     "label": "Focus sur l'écran de gauche",
     "line": 'hl.bind(mainMod .. " + comma", hl.dsp.focus({ monitor = "l" }))'},
    {"mods": ["SUPER"], "key": "semicolon",
     "label": "Focus sur l'écran de droite",
     "line": 'hl.bind(mainMod .. " + semicolon", hl.dsp.focus({ monitor = "r" }))'},
    # -- lancement
    {"mods": ["SUPER", "SHIFT"], "key": "M",
     "label": "Menu d'extinction (au lieu de quitter sec)",
     "line": 'hl.bind(mainMod .. " + SHIFT + M", hl.dsp.exec_cmd("~/.config/waybar/power-menu.py"))'},
    {"mods": ["SUPER"], "key": "period",
     "label": "Emojis et symboles",
     "line": 'hl.bind(mainMod .. " + period", hl.dsp.exec_cmd("walker -m symbols"))'},
    {"mods": ["SUPER", "SHIFT"], "key": "Escape",
     "label": "Moniteur système (btop)",
     "line": 'hl.bind(mainMod .. " + SHIFT + Escape", hl.dsp.exec_cmd("kitty -e btop"))'},
    # -- système
    {"mods": ["SUPER", "CTRL"], "key": "Print",
     "label": "Capture d'une région vers le presse-papiers",
     "line": 'hl.bind(mainMod .. " + CTRL + Print", hl.dsp.exec_cmd('
             '"hyprshot -m region --clipboard-only"))'},
    {"mods": ["SUPER", "SHIFT"], "key": "B",
     "label": "Masquer / afficher la barre Waybar",
     "line": 'hl.bind(mainMod .. " + SHIFT + B", hl.dsp.exec_cmd("killall -SIGUSR1 waybar"))'},
    {"mods": ["SUPER", "SHIFT"], "key": "A",
     "label": "Caféine : bloquer la mise en veille",
     "line": 'hl.bind(mainMod .. " + SHIFT + A", hl.dsp.exec_cmd('
             '"~/.config/waybar/caffeine-toggle.sh"))'},
]

EXTRA_CSS = """
/* La combinaison est une pastille posée à droite de la ligne : fond
   translucide, chiffres en monospace pour que Super + Shift + F ne danse pas
   d'une ligne à l'autre. Les largeurs restent inégales — c'est la longueur
   réelle des raccourcis — mais le fond commun les fait lire comme une colonne
   plutôt que comme des créneaux. */
.combo {
    background-color: rgba(255, 255, 255, 0.10);
    color: #5e9cff;
    font-family: "JetBrainsMono Nerd Font", monospace;
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 6px;
}
.row:hover .combo { background-color: rgba(255, 255, 255, 0.18); }
/* Pendant la capture, la ligne entière attend une touche : la pastille passe
   en bleu plein, c'est le seul endroit de l'interface qui change. */
.combo.capturing { background-color: #0a84ff; color: #ffffff; }
.combo.readonly { color: rgba(235, 235, 245, 0.35); }
.caption.err { color: #ff453a; }
.caption.ok { color: #32d74b; }
/* Les lignes n'ayant pas de colonne d'icône, les filets se recalent sur le
   texte : 12 px, soit le seul padding de la ligne. */
.card separator { margin-left: 12px; }
"""

def apply_extra_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(EXTRA_CSS.encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


# ---------------------------------------------------------------- parsing


def split_lua_args(body):
    """Découpe les arguments d'un appel Lua, en ignorant les virgules internes.

    `hl.bind(mainMod .. " + Q", hl.dsp.exec_cmd("a, b"))` a deux arguments,
    pas trois : on ne coupe qu'au niveau zéro de parenthèses / accolades, et
    jamais à l'intérieur d'une chaîne — y compris un littéral long [[…]], que
    les commandes shell utilisent pour éviter d'échapper les guillemets.
    """
    args, depth, i, start = [], 0, 0, 0
    quote = None          # guillemet ouvert (" ou ') ou "[[" pour un long
    while i < len(body):
        c = body[i]
        if quote == "[[":
            if body.startswith("]]", i):
                quote, i = None, i + 2
                continue
        elif quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif body.startswith("[[", i):
            quote, i = "[[", i + 2
            continue
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(body[start:i].strip())
            start = i + 1
        i += 1
    args.append(body[start:].strip())
    return args


def lua_str_value(text):
    """Valeur d'un littéral Lua ("…", '…' ou [[…]]), ou None si autre chose."""
    t = text.strip()
    for opener, closer in (('"', '"'), ("'", "'"), ("[[", "]]")):
        if t.startswith(opener) and t.endswith(closer) and len(t) > len(opener):
            return t[len(opener):-len(closer)]
    return None


def keys_expr_to_combo(expr, variables):
    """« mainMod .. " + SHIFT + R" » -> « SUPER + SHIFT + R ».

    L'expression est une concaténation Lua de littéraux et de variables
    locales (mainMod). On la reconstitue à plat pour l'affichage et pour
    savoir quels modificateurs sont en jeu.
    """
    out = []
    for piece in expr.split(".."):
        piece = piece.strip()
        value = lua_str_value(piece)
        if value is not None:
            out.append(value)
        elif piece in variables:
            out.append(variables[piece])
        else:
            out.append(piece)
    return "".join(out)


def normalize_mods(combo):
    """Modificateurs d'une combinaison à plat, dans l'ordre canonique."""
    found = []
    for token in combo.split("+"):
        tok = token.strip().upper()
        if tok in ("SUPER", "MOD4", "WIN"):
            tok = "SUPER"
        elif tok in ("CTRL", "CONTROL"):
            tok = "CTRL"
        elif tok in ("ALT", "MOD1"):
            tok = "ALT"
        if tok in MOD_ORDER:
            found.append(tok)
    return [m for m in MOD_ORDER if m in found]


def combo_key(combo):
    """Touche d'une combinaison à plat : le dernier jeton non-modificateur."""
    tokens = [t.strip() for t in combo.split("+") if t.strip()]
    keys = [t for t in tokens if t.upper() not in
            ("SUPER", "MOD4", "WIN", "CTRL", "CONTROL", "ALT", "MOD1", "SHIFT")]
    return keys[-1] if keys else (tokens[-1] if tokens else "")


def strip_lua_comment(text):
    """Retire un « -- commentaire » de fin de ligne, jamais dans une chaîne."""
    depth, i, quote = 0, 0, None
    while i < len(text):
        c = text[i]
        if quote == "[[":
            if text.startswith("]]", i):
                quote, i = None, i + 2
                continue
        elif quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif text.startswith("[[", i):
            quote, i = "[[", i + 2
            continue
        elif text.startswith("--", i):
            return text[:i].rstrip()
        i += 1
    return text


def shorten_section(text):
    """Titre de section court : on coupe à la première explication.

    « Scratchpads pyprland : terminal, Spotify… » -> « Scratchpads pyprland »
    """
    for sep in (" : ", " (", " — ", ", "):
        if len(text) > 44 and sep in text:
            text = text.split(sep)[0].strip()
            break
    return text if len(text) <= 60 else text[:57] + "…"


def parse_variables(lines):
    """Récupère les « local nom = "valeur" » d'un fichier de config Lua."""
    variables = {}
    for line in lines:
        m = re.match(r'^\s*local\s+(\w+)\s*=\s*(.+?)\s*$', line)
        if m:
            value = lua_str_value(strip_lua_comment(m.group(2)))
            if value is not None:
                variables[m.group(1)] = value
    return variables


def section_from_block(block):
    """Titre de section porté par un bloc de commentaires, ou "" s'il n'en porte pas.

    Un commentaire qui précède des binds n'est pas forcément leur titre, et le
    prendre systématiquement donnait des en-têtes faux — d'autant qu'un titre
    retenu restait collé à TOUS les binds suivants jusqu'au commentaire
    d'après. Constaté sur la capture screenshots/keybinds.png : « Example
    per-device config », qui commente un `hl.device` de la section INPUT,
    chapeautait Super+Q ; et l'explication de Super+N, longue de trois lignes,
    chapeautait les huit raccourcis suivants.

    Deux formes seulement font un titre :

    · un bloc d'UNE ligne (« Fullscreen », « Clipboard history ») — au-delà,
      c'est une explication du bind qui suit, pas un intitulé de groupe ;
    · un marqueur explicite « ── Titre ── », qui reste un titre même suivi de
      son explication.

    Tout le reste — bandeaux `--### SECTION ####`, URL, blocs de plusieurs
    lignes — ne titre rien et REMET la section à zéro, pour qu'un intitulé
    périmé ne déborde pas sur la suite du fichier.
    """
    for raw in block:
        text = raw.lstrip("-").strip()
        if "──" in text:
            text = text.strip("─").strip()
            if text and "http" not in text:
                return shorten_section(text)

    if len(block) != 1:
        return ""

    text = block[0].lstrip("-").strip().strip("─").strip()
    if not text or text.startswith("#") or "http" in text:
        return ""
    return shorten_section(text)


def parse_binds():
    """Renvoie la liste des binds de tous les fichiers source."""
    binds = []
    for path in SOURCES:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            continue

        variables = parse_variables(lines)
        # Variable valant SUPER (mainMod) : on la réutilise à la réécriture
        # pour ne pas mélanger « mainMod » et « "SUPER" » dans le fichier.
        super_var = next((name for name, value in variables.items()
                          if value.strip().upper() in ("SUPER", "MOD4")), None)
        section = ""
        block = []          # lignes du bloc de commentaires en cours
        for lineno, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("--"):
                block.append(stripped)
                continue
            # Fin d'un bloc de commentaires : on décide seulement maintenant
            # s'il titrait les binds qui suivent, car cela dépend de sa taille.
            if block:
                section = section_from_block(block)
                block = []
            if not stripped:
                continue
            m = BIND_RE.match(strip_lua_comment(line))
            if not m:
                continue
            args = split_lua_args(m.group("body"))
            if len(args) < 2:
                continue
            keys_expr, dispatcher = args[0], args[1]
            opts = args[2] if len(args) > 2 else ""
            combo = keys_expr_to_combo(keys_expr, variables)
            key = combo_key(combo)
            # Un bind souris est marqué par l'option `mouse` (ancien `bindm`),
            # ou porte directement une pseudo-touche mouse:272.
            is_mouse = key.lower().startswith("mouse") or "mouse" in opts
            binds.append({
                "file": path,
                "lineno": lineno,
                "indent": m.group("indent"),
                "keys_expr": keys_expr,
                "dispatcher": dispatcher,
                "opts": opts,
                "combo": combo,
                "mods": normalize_mods(combo),
                "key": key,
                "super_var": super_var,
                "variables": variables,
                "section": section,
                "editable": not is_mouse,
            })
    return binds


def combo_label(mods, key):
    """« Super + Shift + R » à partir des modificateurs et de la touche."""
    parts = [MOD_LABEL[m] for m in mods]
    parts.append(KEY_LABEL.get(key.lower(), key.upper() if len(key) == 1 else key))
    return " + ".join(parts)


def action_label(bind):
    """Libellé lisible de ce que fait le bind.

    Pour `hl.dsp.exec_cmd` on montre la commande (c'est l'information utile) ;
    pour les autres dispatchers on traduit le nom et on garde l'argument
    éventuel — `hl.dsp.group.active({ index = 3 })` donne « Onglet du groupe 3 ».
    """
    text = bind["dispatcher"].strip()
    m = re.match(r"^hl\.dsp\.([\w.]+)\s*\((?P<args>.*)\)\s*$", text, re.S)
    if not m:
        # Une fonction Lua anonyme, ou une forme qu'on ne sait pas lire :
        # mieux vaut la montrer telle quelle que de mentir sur son effet.
        return text
    name, args = m.group(1), m.group("args").strip()

    if name == "exec_cmd":
        cmd = lua_str_value(args)
        if cmd is None:
            # `hl.dsp.exec_cmd(terminal)` : la variable locale porte la
            # commande réelle, c'est elle qui intéresse le lecteur.
            cmd = bind.get("variables", {}).get(args.strip(), args)
        return cmd or "exec"

    # Arguments : soit un littéral ("magic"), soit une table ({ index = 3 }).
    literal = lua_str_value(args)
    if literal is not None:
        arg = literal
    else:
        arg = re.sub(r"\s*=\s*", "=", args.strip("{}").strip())

    special = ARG_LABEL.get((name, arg))
    if special:
        return special

    label = DISPATCHER_LABEL.get(name, name)
    if not arg:
        return label
    # « direction=left » ou « index=3 » : seule la valeur est parlante.
    value = arg.split("=", 1)[1].strip() if "=" in arg and "," not in arg else arg
    return "%s %s" % (label, value.strip('"\''))


# ---------------------------------------------------------------- écriture


def backup(path):
    """Sauvegarde le fichier avant réécriture.

    Deux niveaux : `.orig` fige l'état d'avant la toute première modification
    et n'est plus jamais touché, `.bak` garde l'état d'avant la dernière. Sans
    le `.orig`, quelques réassignations suffisaient à perdre le fichier initial.
    """
    orig = path + ".orig"
    if not os.path.exists(orig):
        shutil.copy2(path, orig)
    shutil.copy2(path, path + ".bak")


def rewrite_bind(bind, mods, key):
    """Réécrit la ligne du bind avec la nouvelle combinaison.

    Conserve `mainMod` si le bind l'utilisait et que Super fait toujours
    partie de la combinaison, pour ne pas dénaturer le style du fichier :
    `hl.bind(mainMod .. " + SHIFT + R", …)` plutôt que `"SUPER + SHIFT + R"`.
    """
    path = bind["file"]
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)

    var = bind["super_var"]
    uses_var = var is not None and var in bind["keys_expr"]
    tokens = [m for m in mods] + [key]
    if "SUPER" in mods and uses_var:
        rest = " + ".join(t for t in tokens if t != "SUPER")
        keys_expr = '%s .. " + %s"' % (var, rest) if rest else var
    else:
        keys_expr = '"%s"' % " + ".join(tokens)

    parts = [keys_expr, bind["dispatcher"]]
    if bind["opts"]:
        parts.append(bind["opts"])
    new_line = "%shl.bind(%s)\n" % (bind["indent"], ", ".join(parts))

    backup(path)
    lines[bind["lineno"]] = new_line
    # Écriture atomique : une coupure en plein write() laisserait sinon une
    # config Hyprland tronquée, donc une session sans aucun raccourci.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)

    bind["keys_expr"] = keys_expr
    bind["combo"] = " + ".join(tokens)
    bind["mods"] = mods
    bind["key"] = key


def hypr_reload():
    subprocess.run(["hyprctl", "reload"], stdout=DEVNULL, stderr=DEVNULL)
    # Un reload seul perd la config des plugins : load-plugins.sh la réapplique
    # (`hyprctl eval dofile(plugins.lua)`), comme le faisait le
    # `hyprctl keyword source plugins.conf` de l'ancien format.
    loader = os.path.join(HYPR_DIR, "scripts", "load-plugins.sh")
    if os.path.exists(loader):
        subprocess.run([loader], stdout=DEVNULL, stderr=DEVNULL)


# ---------------------------------------------------------------- UI


class KeybindsPopup(LayerPopup):
    """Les binds Hyprland, groupés par section, en cartes filtrables.

    La ligne entière est cliquable : plus besoin de viser la pastille pour
    réassigner un raccourci. C'est aussi ce qui permet aux combinaisons de
    s'aligner à droite sur un fond commun, là où des boutons isolés donnaient
    une colonne en dents de scie.
    """

    def __init__(self):
        super().__init__("Raccourcis", width=560, margin_right=10)
        apply_extra_css()

        self.binds = parse_binds()
        self.capturing = None   # bind en cours de réassignation
        self.rows = []          # (bind, ligne, carte, texte de recherche)
        self.sections = []      # (nom, conteneur titre+carte, carte)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Rechercher une action ou une touche…")
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.box.pack_start(self.search, False, False, 0)

        self.status = caption_label("")
        self.box.pack_start(self.status, False, False, 0)
        self._set_status("Clic sur une ligne pour réassigner son raccourci.",
                         None)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(560)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=12)
        scroller.add(self.list_box)
        self.box.pack_start(scroller, True, True, 0)

        self._build_rows()
        self._build_suggestions()

    # ---- construction de la liste ----

    def _build_rows(self):
        current_section = None
        card = None
        for bind in self.binds:
            if bind["section"] != current_section or card is None:
                current_section = bind["section"]
                card = Card()
                wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                if current_section:
                    wrap.pack_start(section_label(current_section),
                                    False, False, 0)
                wrap.pack_start(card, False, False, 0)
                self.list_box.pack_start(wrap, False, False, 0)
                self.sections.append((current_section, wrap, card))

            combo = combo_label(bind["mods"], bind["key"])
            # Aucun de ces raccourcis n'a d'icône : la colonne est supprimée,
            # pas seulement vidée — 44 px de retrait sur du vide décalaient
            # tous les libellés vers la droite.
            row = card.action(None, action_label(bind), value=combo,
                              tooltip="%s\n%s:%d" % (
                                  bind["dispatcher"],
                                  os.path.basename(bind["file"]),
                                  bind["lineno"] + 1))
            row.value_label.get_style_context().add_class("combo")
            if bind["editable"]:
                row.connect("clicked", self._start_capture, bind)
            else:
                row.value_label.get_style_context().add_class("readonly")
                row.set_sensitive(False)
                row.set_tooltip_text("Bind souris : non réassignable ici")

            haystack = " ".join([
                action_label(bind), combo, bind["section"], bind["key"],
            ]).lower()
            self.rows.append((bind, row, card, haystack))

    def _build_suggestions(self):
        """Section finale : les actions utiles restées sans raccourci.

        Rien n'est attribué — les lignes sont inertes, la combinaison est
        grisée comme celle des binds souris, et l'infobulle donne la ligne
        `hl.bind` à coller dans hyprland.lua. Une suggestion dont la
        combinaison est déjà prise n'est pas affichée : soit elle a été
        adoptée, soit elle entrerait en conflit.
        """
        taken = {(tuple(b["mods"]), b["key"].lower()) for b in self.binds}
        pending = [s for s in SUGGESTIONS
                   if (tuple(s["mods"]),
                       s.get("probe", s["key"]).lower()) not in taken]
        if not pending:
            return

        card = Card()
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.pack_start(section_label(SUGGESTIONS_SECTION), False, False, 0)
        wrap.pack_start(card, False, False, 0)
        wrap.pack_start(caption_label(
            "Combinaisons libres, non attribuées : la ligne à coller dans "
            "hyprland.lua est dans l'infobulle."), False, False, 0)
        self.list_box.pack_start(wrap, False, False, 0)
        self.sections.append((SUGGESTIONS_SECTION, wrap, card))

        for sug in pending:
            combo = combo_label(sug["mods"], sug["key"])
            row = card.action(None, sug["label"], value=combo,
                              tooltip=sug["line"])
            ctx = row.value_label.get_style_context()
            ctx.add_class("combo")
            ctx.add_class("readonly")
            row.set_sensitive(False)
            self.rows.append((None, row, card, " ".join([
                sug["label"], combo, SUGGESTIONS_SECTION, sug["key"],
            ]).lower()))

    def _refilter(self):
        needle = self.search.get_text().strip().lower()
        touched = set()
        for _bind, row, card, haystack in self.rows:
            row.set_visible(needle in haystack if needle else True)
            touched.add(card)

        # Une carte vide n'a pas à laisser son intertitre flotter seul, et les
        # filets internes doivent se recaler sur les lignes qui restent.
        for card in touched:
            card.sync_separators()
        for _name, wrap, card in self.sections:
            visible = any(r.get_visible() for _b, r, c, _h in self.rows
                          if c is card)
            wrap.set_visible(visible)

    # ---- capture d'une nouvelle combinaison ----

    def _start_capture(self, row, bind):
        if self.capturing is not None:
            self._cancel_capture()
        self.capturing = (bind, row)
        row.value_label.get_style_context().add_class("capturing")
        row.value_label.set_text("Appuyez sur la combinaison…")
        self._set_status("Échap pour annuler.", None)

    def _cancel_capture(self):
        if self.capturing is None:
            return
        bind, row = self.capturing
        row.value_label.get_style_context().remove_class("capturing")
        row.value_label.set_text(combo_label(bind["mods"], bind["key"]))
        self.capturing = None

    def _set_status(self, text, kind):
        ctx = self.status.get_style_context()
        for cls in ("err", "ok"):
            ctx.remove_class(cls)
        if kind:
            ctx.add_class(kind)
        self.status.set_text(text)

    def _conflict(self, bind, mods, key):
        """Renvoie le bind en conflit avec (mods, key), s'il existe."""
        target = (tuple(mods), key.lower())
        for other in self.binds:
            if other is bind:
                continue
            if (tuple(other["mods"]), other["key"].lower()) == target:
                return other
        return None

    def _on_key(self, widget, event):
        # Hors capture : comportement standard (Échap ferme, flèches naviguent).
        if self.capturing is None:
            return super()._on_key(widget, event)

        if event.keyval == Gdk.KEY_Escape:
            self._cancel_capture()
            self._set_status("Réassignation annulée.", None)
            return True

        # Un modificateur seul ne valide pas la combinaison.
        if Gdk.keyval_name(event.keyval) in (
                "Super_L", "Super_R", "Shift_L", "Shift_R", "Control_L",
                "Control_R", "Alt_L", "Alt_R", "ISO_Level3_Shift"):
            return True

        mods = []
        state = event.state
        if state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK):
            mods.append("SUPER")
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.append("CTRL")
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.append("ALT")
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.append("SHIFT")
        mods = [m for m in MOD_ORDER if m in mods]

        key = self._base_key_name(event)
        if not key:
            return True

        bind, _row = self.capturing
        clash = self._conflict(bind, mods, key)
        if clash is not None:
            self._set_status(
                "%s est déjà pris par : %s" % (
                    combo_label(mods, key), action_label(clash)[:48]),
                "err")
            self._cancel_capture()
            return True

        self._apply(bind, mods, key)
        return True

    @staticmethod
    def _base_key_name(event):
        """Nom de la touche sans l'effet des modificateurs.

        Sans ça, Shift+A renverrait « A » et Shift+2 le caractère shifté du
        layout, alors qu'Hyprland attend le nom de base de la touche.
        """
        keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        ok, keyval, _grp, _lvl, _cons = keymap.translate_keyboard_state(
            event.hardware_keycode, Gdk.ModifierType(0), event.group)
        if not ok:
            keyval = event.keyval
        name = Gdk.keyval_name(keyval)
        if not name:
            return None
        # Hyprland est insensible à la casse ; on suit le style du fichier :
        # lettre seule en majuscule, noms symboliques tels quels.
        return name.upper() if len(name) == 1 else name

    def _apply(self, bind, mods, key):
        _b, row = self.capturing
        try:
            rewrite_bind(bind, mods, key)
        except OSError as exc:
            self._set_status("Écriture impossible : %s" % exc, "err")
            self._cancel_capture()
            return

        row.value_label.get_style_context().remove_class("capturing")
        row.value_label.set_text(combo_label(bind["mods"], bind["key"]))
        self.capturing = None

        hypr_reload()
        self._set_status("%s → %s (rechargé)" % (
            combo_label(mods, key), action_label(bind)[:40]), "ok")
        # Rafraîchit le texte de recherche associé à la ligne modifiée.
        for idx, (b, widget, card, _hay) in enumerate(self.rows):
            if b is bind:
                self.rows[idx] = (b, widget, card, " ".join([
                    action_label(b), combo_label(b["mods"], b["key"]),
                    b["section"], b["key"]]).lower())
                break

    def run(self):
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        self.search.grab_focus()
        Gtk.main()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    KeybindsPopup().run()


if __name__ == "__main__":
    main()
