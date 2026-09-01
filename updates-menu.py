#!/usr/bin/env python3
"""Popup « Mises à jour » pour Waybar (style menu luminosité / power).

Remplace l'ancien terminal kitty listant brutalement 200 lignes de paquets :
la liste brute ne dit pas *ce qu'on met à jour*, ni si c'est urgent, ni s'il
faudra redémarrer. Ce menu classe les paquets en trois familles lisibles :

  - Système     : noyau, pilotes, brique de base -> redémarrage à prévoir
  - Applications: les paquets installés explicitement (pacman -Qqe)
  - Dépendances : le reste, c'est-à-dire les bibliothèques tirées par les
                  précédents (les fameux 189 rebuilds haskell)

Les paquets AUR sont comptés à part : ils se recompilent, donc plus lents.
"""
import os
import re
import signal
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATES_SH = os.path.join(CONFIG_DIR, "updates.sh")
CACHE = "/tmp/waybar-updates.cache"
PACMAN_LOG = "/var/log/pacman.log"
DEVNULL = subprocess.DEVNULL

# Briques bas niveau : leur mise à jour ne prend effet qu'après redémarrage
# (les modules du noyau courant sont supprimés du disque au passage).
# Noms exacts : `linux-api-headers` ou `mesa-utils` n'imposent aucun reboot,
# seul le paquet lui-même compte — sauf nvidia, dont toute la famille compte.
REBOOT_EXACT = frozenset((
    "linux", "linux-zen", "linux-lts", "linux-hardened", "linux-firmware",
    "systemd", "glibc", "mesa", "amd-ucode", "intel-ucode",
))
REBOOT_PREFIXES = ("nvidia",)
# Catégorie « Système » : plus large, tout ce qui tient la session debout.
SYSTEM_PKGS = tuple(REBOOT_EXACT) + REBOOT_PREFIXES + (
    "dbus", "pam", "wayland", "sddm", "hyprland", "pipewire", "wireplumber",
    "xorg-server", "vulkan-radeon", "vulkan-intel", "gcc-libs",
)

EXTRA_CSS = b"""
label.dim   { color: #68686f; font-size: 11px; }
label.warn  { color: #ff9f0a; }
label.ok    { color: #32d74b; }
label.count { color: #0a84ff; font-weight: bold; }
button.accent label { color: #ffffff; font-weight: bold; }
"""


def apply_extra_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(EXTRA_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


def matches(pkg, patterns):
    """`linux` matche linux et linux-headers, mais pas linux-firmware-tools."""
    return any(pkg == p or pkg.startswith(p + "-") for p in patterns)


def needs_reboot(pkg):
    return pkg in REBOOT_EXACT or matches(pkg, REBOOT_PREFIXES)


def read_cache():
    """Renvoie (paquets_dépôts, paquets_aur) depuis le cache d'updates.sh."""
    if not os.path.exists(CACHE):
        subprocess.run([UPDATES_SH], stdout=DEVNULL, stderr=DEVNULL, timeout=120)
    try:
        with open(CACHE) as fh:
            raw = fh.read()
    except OSError:
        return [], []
    head, _, tail = raw.partition("\n---\n")
    parse = lambda block: [l.split()[0] for l in block.splitlines() if l.strip()]
    return parse(head), parse(tail)


def explicit_packages():
    try:
        out = subprocess.run(["pacman", "-Qqe"], capture_output=True, text=True,
                             timeout=15).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def last_upgrade():
    """Date du dernier `pacman -Syu`, lue dans le log."""
    try:
        with open(PACMAN_LOG, errors="ignore") as fh:
            stamp = None
            for line in fh:
                if "starting full system upgrade" in line:
                    stamp = line[1:line.index("]")]
        if not stamp:
            return None
        when = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except (OSError, ValueError):
        return None
    days = int((time.time() - when) // 86400)
    if days == 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return "il y a %d jours" % days


def download_size(pkgs):
    """Taille restant à télécharger, en Mo — 0 si tout est déjà en cache.

    On interroge la base temporaire de checkupdates (déjà synchronisée), pour
    ne pas avoir besoin de root.
    """
    db = os.environ.get("CHECKUPDATES_DB", "/tmp/checkup-db-%d" % os.getuid())
    if not pkgs or not os.path.isdir(db):
        return None
    try:
        out = subprocess.run(
            ["pacman", "-Sp", "--dbpath", db, "--print-format", "%s"] + pkgs,
            capture_output=True, text=True, timeout=30).stdout
        total = sum(int(n) for n in out.split() if n.isdigit())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return total / 1024 / 1024


class UpdatesPopup(LayerPopup):
    def __init__(self):
        super().__init__("Mises à jour", width=380, margin_right=10)
        apply_extra_css()
        self._build()

    # ---- Construction ----

    def _build(self):
        repo, aur = read_cache()
        explicit = explicit_packages()

        system = [p for p in repo if matches(p, SYSTEM_PKGS)]
        apps = [p for p in repo if p not in system and p in explicit]
        deps = [p for p in repo if p not in system and p not in explicit]
        total = len(repo) + len(aur)

        if total == 0:
            self._row_label("  Système à jour", css="ok")
            since = last_upgrade()
            if since:
                self._row_label("Dernière mise à jour : %s" % since, css="dim")
            self._add_button("Vérifier maintenant", self._refresh)
            return

        # -- Résumé --
        size = download_size(repo)
        self._row_markup("<b>%d paquet%s en attente</b>"
                         % (total, "s" if total > 1 else ""))

        sub = []
        if size is not None and size >= 1:
            sub.append("%.0f Mo à télécharger" % size)
        since = last_upgrade()
        if since:
            sub.append("dernière mise à jour %s" % since)
        if sub:
            self._row_label(" · ".join(sub), css="dim")

        # -- Avertissement redémarrage --
        if any(needs_reboot(p) for p in repo):
            self._row_markup(
                "  <b>Redémarrage nécessaire ensuite</b>", css="warn")

        self.box.pack_start(Gtk.Separator(), False, False, 0)

        # -- Répartition par famille --
        self._category("󰒓", "Système", system,
                       "noyau et pilotes")
        self._category("󰏗", "Applications", apps,
                       "tes logiciels")
        self._category("󰆧", "Dépendances", deps,
                       "bibliothèques internes")
        self._category("󰣇", "AUR", aur,
                       "à recompiler")

        # -- Détail repliable --
        exp = Gtk.Expander(label="Voir la liste complète")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(220)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(self._detail_markup(system, apps, deps, aur))
        lbl.set_selectable(True)
        # Sans repli, les ~200 noms de paquets joints par ", " forment une
        # ligne unique de plusieurs dizaines de milliers de pixels. Le
        # ScrolledWindow est en NEVER à l'horizontale : il propage cette
        # largeur à la fenêtre, Cairo refuse une surface aussi large et GDK
        # segfaute au premier dessin. max_width_chars est indispensable —
        # sans lui un label replié réclame quand même sa largeur d'une ligne.
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(2)   # Pango.WrapMode.WORD_CHAR
        lbl.set_max_width_chars(42)
        scroll.add(lbl)
        exp.add(scroll)
        self.box.pack_start(exp, False, False, 0)

        self.box.pack_start(Gtk.Separator(), False, False, 0)

        # -- Actions --
        self._add_button("  Tout mettre à jour", self._upgrade, accent=True)
        self._add_button("  Actualiser", self._refresh)

    def _category(self, icon, name, pkgs, hint):
        if not pkgs:
            return
        row = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("%s  %s" % (icon, GLib.markup_escape_text(name)))
        row.pack_start(lbl, False, False, 0)
        hint_lbl = Gtk.Label(xalign=0, label=hint)
        hint_lbl.get_style_context().add_class("dim")
        hint_lbl.set_ellipsize(3)   # Pango.EllipsizeMode.END
        hint_lbl.set_max_width_chars(1)
        row.pack_start(hint_lbl, True, True, 0)
        count = Gtk.Label(label=str(len(pkgs)))
        count.get_style_context().add_class("count")
        row.pack_end(count, False, False, 0)
        self.box.pack_start(row, False, False, 0)

    def _detail_markup(self, system, apps, deps, aur):
        parts = []
        for title, pkgs in (("Système", system), ("Applications", apps),
                            ("Dépendances", deps), ("AUR", aur)):
            if not pkgs:
                continue
            body = GLib.markup_escape_text(", ".join(sorted(pkgs)))
            parts.append("<b>%s</b>\n<span size='small'>%s</span>" % (title, body))
        return "\n\n".join(parts)

    def _row_label(self, text, css=None):
        lbl = Gtk.Label(xalign=0, label=text)
        if css:
            lbl.get_style_context().add_class(css)
        self.box.pack_start(lbl, False, False, 0)

    def _row_markup(self, markup, css=None):
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(markup)
        if css:
            lbl.get_style_context().add_class(css)
        self.box.pack_start(lbl, False, False, 0)

    def _add_button(self, label, handler, accent=False):
        btn = Gtk.Button(label=label)
        if accent:
            btn.get_style_context().add_class("accent")
        btn.connect("clicked", handler)
        self.box.pack_start(btn, False, False, 0)

    # ---- Handlers ----

    def _upgrade(self, _btn):
        # Terminal interactif : yay demande le mot de passe sudo et les
        # confirmations de remplacement de paquets.
        subprocess.Popen(
            ["kitty", "--class", "waybar.modules", "-T", "Mise à jour du système",
             "bash", "-c",
             "yay -Syu; printf '\\nTerminé — Entrée pour fermer.'; read -r _; "
             "%s --refresh" % UPDATES_SH],
            stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _refresh(self, _btn):
        subprocess.Popen([UPDATES_SH, "--refresh"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    UpdatesPopup().run()


if __name__ == "__main__":
    main()
