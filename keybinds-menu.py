#!/usr/bin/env python3
"""Popup Raccourcis clavier pour Waybar (style menu luminosité / power).

Liste tous les binds Hyprland (hyprland.conf + plugins.conf) groupés par
section, avec recherche, et permet de réassigner une combinaison en la
capturant au clavier. La ligne du fichier source est réécrite en place
(après backup .orig / .bak) puis `hyprctl reload` est déclenché.

Les binds souris (bindm) sont affichés en lecture seule : leur « touche »
(mouse:272) n'est pas capturable au clavier.
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

from menu_common import LayerPopup  # noqa: E402

HYPR_DIR = os.path.expanduser("~/.config/hypr")
SOURCES = [os.path.join(HYPR_DIR, "hyprland.conf"),
           os.path.join(HYPR_DIR, "plugins.conf")]
DEVNULL = subprocess.DEVNULL

# `bind`, `bindm`, `bindel`… suivis de « = mods, touche, reste »
BIND_RE = re.compile(r"^(?P<indent>\s*)(?P<flags>bind[a-z]*)\s*=\s*(?P<body>.*?)\s*$")

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
}

# Dispatchers Hyprland natifs -> libellé lisible (exec est traité à part).
DISPATCHER_LABEL = {
    "killactive": "Fermer la fenêtre",
    "closewindow": "Fermer la fenêtre",
    "togglefloating": "Basculer flottant",
    "fullscreen": "Plein écran",
    "fakefullscreen": "Faux plein écran",
    "pseudo": "Pseudo-tiling",
    "pin": "Épingler",
    "movefocus": "Focus →",
    "movewindow": "Déplacer la fenêtre →",
    "resizeactive": "Redimensionner",
    "workspace": "Aller au workspace",
    "movetoworkspace": "Envoyer au workspace",
    "movetoworkspacesilent": "Envoyer au workspace (silencieux)",
    "togglespecialworkspace": "Workspace spécial",
    "togglegroup": "Grouper / dégrouper",
    "changegroupactive": "Fenêtre suivante du groupe",
    "layoutmsg": "Layout",
    "exit": "Quitter Hyprland",
    "forcerendererreload": "Recharger le rendu",
    "centerwindow": "Centrer la fenêtre",
    "splitratio": "Ratio de split",
    "invertactivewindow": "Inverser les couleurs",
    "easymotion": "Easymotion",
}

EXTRA_CSS = b"""
entry {
    background-color: rgba(255, 255, 255, 0.09);
    color: #ebebf0;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
}
entry:focus { outline: 2px solid rgba(10, 132, 255, 0.75); outline-offset: -2px; }
label.section {
    color: #ff9f0a;
    font-size: 11px;
    font-weight: bold;
    margin-top: 6px;
}
label.desc { color: #ebebf0; }
label.cmd { color: #68686f; font-size: 10px; }
button.combo {
    background-color: rgba(255, 255, 255, 0.09);
    color: #0a84ff;
    font-family: monospace;
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 6px;
}
button.combo:hover { background-color: rgba(255, 255, 255, 0.16); color: #ebebf0; }
button.combo.capturing { background-color: #0a84ff; color: #ffffff; }
button.combo.readonly { color: #68686f; }
label.status { font-size: 11px; }
label.status.err { color: #ff453a; }
label.status.ok { color: #32d74b; }
scrolledwindow { border-radius: 8px; }
"""


def apply_extra_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(EXTRA_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


# ---------------------------------------------------------------- parsing


def split_bind(body):
    """Découpe « mods, touche, dispatcher, args » en 3 parties.

    Les args d'un dispatcher contiennent souvent des virgules (exec …), donc
    on ne découpe que sur les deux premières.
    """
    parts = body.split(",", 2)
    if len(parts) < 2:
        return None
    mods = parts[0].strip()
    key = parts[1].strip()
    rest = parts[2].strip() if len(parts) > 2 else ""
    return mods, key, rest


def normalize_mods(mods_raw, variables):
    """Résout les variables ($mainMod) et renvoie la liste des modificateurs."""
    text = mods_raw
    for name, value in variables.items():
        text = text.replace("$" + name, value)
    found = []
    for token in text.replace("+", " ").split():
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


def strip_inline_comment(text):
    """Retire un « # commentaire » de fin de ligne (jamais dans une commande).

    Les binds `exec` peuvent légitimement contenir un #, on ne touche donc
    qu'aux dispatchers natifs, où le # est toujours un commentaire.
    """
    if text.startswith("exec"):
        return text
    return text.split("#")[0].strip()


def expand_vars(text, variables):
    """Remplace les $variables par leur valeur, pour l'affichage seulement."""
    for name, value in sorted(variables.items(), key=lambda kv: -len(kv[0])):
        text = text.replace("$" + name, value)
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
    """Récupère les « $nom = valeur » d'un fichier de config."""
    variables = {}
    for line in lines:
        m = re.match(r"^\s*\$(\w+)\s*=\s*([^#]*)", line)
        if m:
            variables[m.group(1)] = m.group(2).strip()
    return variables


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
        # Variable valant SUPER ($mainMod) : on la réutilise à la réécriture
        # pour ne pas mélanger « $mainMod » et « SUPER » dans le fichier.
        super_var = next((name for name, value in variables.items()
                          if value.strip().upper() in ("SUPER", "MOD4")), None)
        section = ""
        in_comment_block = False
        for lineno, line in enumerate(lines):
            stripped = line.strip()
            # Un commentaire seul sert de titre de section pour les binds
            # suivants. On écarte les séparateurs, les URL et les phrases
            # trop longues, qui ne font pas des titres lisibles.
            #
            # Seule la PREMIÈRE ligne d'un bloc de commentaires contigu est
            # retenue : les suivantes prolongent la phrase et donnent des
            # titres absurdes (« côté écran, d'où la lettre voisine. »).
            if stripped.startswith("#"):
                if not in_comment_block:
                    text = stripped.strip("#").strip()
                    if text and not text.startswith("-") and "http" not in text:
                        section = shorten_section(text)
                in_comment_block = True
                continue
            in_comment_block = False
            if not stripped:
                continue
            m = BIND_RE.match(line)
            if not m:
                continue
            parts = split_bind(m.group("body"))
            if not parts:
                continue
            mods_raw, key, rest = parts
            binds.append({
                "file": path,
                "lineno": lineno,
                "indent": m.group("indent"),
                "flags": m.group("flags"),
                "mods_raw": mods_raw,
                "mods": normalize_mods(mods_raw, variables),
                "key": key,
                "rest": rest,
                "rest_display": expand_vars(strip_inline_comment(rest), variables),
                "super_var": super_var,
                "section": section,
                "editable": not key.lower().startswith("mouse"),
            })
    return binds


def combo_label(mods, key):
    """« Super + Shift + R » à partir des modificateurs et de la touche."""
    parts = [MOD_LABEL[m] for m in mods]
    parts.append(KEY_LABEL.get(key.lower(), key.upper() if len(key) == 1 else key))
    return " + ".join(parts)


def action_label(bind):
    """Libellé lisible de ce que fait le bind.

    Pour `exec` on montre la commande (c'est l'information utile) ; pour les
    dispatchers natifs on traduit le nom et on garde l'argument éventuel.
    """
    rest = bind["rest_display"].strip().rstrip(",").strip()
    if not rest:
        return bind["flags"]
    head, _, tail = rest.partition(",")
    head = head.strip()
    tail = tail.strip().rstrip(",").strip()
    if head == "exec":
        return tail or "exec"
    label = DISPATCHER_LABEL.get(head, head)
    return "%s %s" % (label, tail) if tail else label


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

    Conserve `$mainMod` si le bind l'utilisait et que Super fait toujours
    partie de la combinaison, pour ne pas dénaturer le style du fichier.
    """
    path = bind["file"]
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)

    var = bind["super_var"]
    tokens = ["$" + var if (mod == "SUPER" and var) else mod for mod in mods]
    mods_text = " ".join(tokens)

    body = "%s, %s" % (mods_text, key)
    if bind["rest"]:
        body += ", " + bind["rest"]
    new_line = "%s%s = %s\n" % (bind["indent"], bind["flags"], body)

    backup(path)
    lines[bind["lineno"]] = new_line
    # Écriture atomique : une coupure en plein write() laisserait sinon une
    # config Hyprland tronquée, donc une session sans aucun raccourci.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)

    bind["mods_raw"] = mods_text
    bind["mods"] = mods
    bind["key"] = key


def hypr_reload():
    subprocess.run(["hyprctl", "reload"], stdout=DEVNULL, stderr=DEVNULL)
    plugins = os.path.join(HYPR_DIR, "plugins.conf")
    if os.path.exists(plugins):
        # Un reload seul perd la config des plugins : on la re-source.
        subprocess.run(["hyprctl", "keyword", "source", plugins],
                       stdout=DEVNULL, stderr=DEVNULL)


# ---------------------------------------------------------------- UI


class KeybindsPopup(LayerPopup):
    def __init__(self):
        super().__init__("Raccourcis", width=560, margin_right=10)
        apply_extra_css()

        self.binds = parse_binds()
        self.capturing = None   # bind en cours de réassignation
        self.rows = []          # (bind, ligne_widget, bouton_combo, texte_recherche)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Rechercher une action ou une touche…")
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.box.pack_start(self.search, False, False, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("status")
        self.box.pack_start(self.status, False, False, 0)
        self._set_status("Clic sur une combinaison pour la réassigner.", None)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(560)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroller.add(self.list_box)
        self.box.pack_start(scroller, True, True, 0)

        self._build_rows()

    # ---- construction de la liste ----

    def _build_rows(self):
        current_section = None
        for bind in self.binds:
            if bind["section"] != current_section:
                current_section = bind["section"]
                if current_section:
                    lbl = Gtk.Label(xalign=0)
                    lbl.get_style_context().add_class("section")
                    lbl.set_text(current_section)
                    self.list_box.pack_start(lbl, False, False, 0)
                    self.rows.append((None, lbl, None, ""))

            row = Gtk.Box(spacing=8)

            desc = Gtk.Label(xalign=0)
            desc.get_style_context().add_class("desc")
            desc.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            desc.set_max_width_chars(44)
            desc.set_text(action_label(bind))
            desc.set_tooltip_text("%s\n%s:%d" % (
                bind["rest"], os.path.basename(bind["file"]), bind["lineno"] + 1))
            row.pack_start(desc, True, True, 0)

            btn = Gtk.Button(label=combo_label(bind["mods"], bind["key"]))
            btn.get_style_context().add_class("combo")
            if bind["editable"]:
                btn.connect("clicked", self._start_capture, bind)
            else:
                btn.get_style_context().add_class("readonly")
                btn.set_sensitive(False)
                btn.set_tooltip_text("Bind souris : non réassignable ici")
            row.pack_end(btn, False, False, 0)

            self.list_box.pack_start(row, False, False, 0)
            haystack = " ".join([
                action_label(bind), combo_label(bind["mods"], bind["key"]),
                bind["section"], bind["key"],
            ]).lower()
            self.rows.append((bind, row, btn, haystack))

    def _refilter(self):
        needle = self.search.get_text().strip().lower()
        section_visible = {}
        # Une section reste visible si au moins un de ses binds correspond.
        for bind, widget, _btn, haystack in self.rows:
            if bind is None:
                continue
            match = needle in haystack if needle else True
            widget.set_visible(match)
            section_visible.setdefault(bind["section"], False)
            if match:
                section_visible[bind["section"]] = True

        current = None
        for bind, widget, _btn, _hay in self.rows:
            if bind is None:
                current = widget.get_text()
                widget.set_visible(section_visible.get(current, False))

    # ---- capture d'une nouvelle combinaison ----

    def _start_capture(self, button, bind):
        if self.capturing is not None:
            self._cancel_capture()
        self.capturing = (bind, button)
        button.get_style_context().add_class("capturing")
        button.set_label("Appuyez sur la combinaison…")
        self._set_status("Échap pour annuler.", None)

    def _cancel_capture(self):
        if self.capturing is None:
            return
        bind, button = self.capturing
        button.get_style_context().remove_class("capturing")
        button.set_label(combo_label(bind["mods"], bind["key"]))
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

        bind, _button = self.capturing
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
        _b, button = self.capturing
        try:
            rewrite_bind(bind, mods, key)
        except OSError as exc:
            self._set_status("Écriture impossible : %s" % exc, "err")
            self._cancel_capture()
            return

        button.get_style_context().remove_class("capturing")
        button.set_label(combo_label(bind["mods"], bind["key"]))
        self.capturing = None

        hypr_reload()
        self._set_status("%s → %s (rechargé)" % (
            combo_label(mods, key), action_label(bind)[:40]), "ok")
        # Rafraîchit le texte de recherche associé à la ligne modifiée.
        for idx, (b, widget, btn, _hay) in enumerate(self.rows):
            if b is bind:
                self.rows[idx] = (b, widget, btn, " ".join([
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
