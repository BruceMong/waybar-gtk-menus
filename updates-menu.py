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
import hashlib
import os
import shlex
import shutil
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from menu_common import Card, LayerPopup, run_popup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATES_SH = os.path.join(CONFIG_DIR, "updates.sh")
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
CACHE = os.path.join(RUNTIME_DIR, "waybar-updates.cache")
META = os.path.join(RUNTIME_DIR, "waybar-updates.meta")
PACMAN_LOG = "/var/log/pacman.log"
DEVNULL = subprocess.DEVNULL

# Session Claude qui mène la mise à jour. `yay -Syu` installe, mais ne dit pas
# ce qu'il laisse derrière : .pacnew à fusionner, unité qui ne redémarre plus,
# annonce Arch demandant une intervention. C'est ce travail-là qu'on délègue,
# avec la resynchronisation du dépôt de config qui le suit naturellement.
CLAUDE_BIN = shutil.which("claude")
# Dépôt de config à resynchroniser ensuite. Absent : la session s'en tient au
# système, et l'étape 3 du prompt tombe d'elle-même.
CONFIG_REPO = os.path.expanduser("~/projects/arch-config")

CLAUDE_PROMPT = """%s

1. Regarde d'abord ce qui va être installé (`checkupdates`, `yay -Qua`). Si
   l'opération retire ou remplace un paquet, ou touche le noyau, nvidia, glibc
   ou systemd, dis-le-moi avant de lancer quoi que ce soit.
2. Sinon lance `yay -Syu --noconfirm` en arrière-plan — sudo ne demande pas de
   mot de passe sur cette machine, et la recompilation AUR est longue. Suis-la
   jusqu'au bout et rends compte de ce qui a échoué.
3. Vérifie ensuite ce que la mise à jour laisse à faire : fichiers .pacnew à
   fusionner (`pacdiff -o`), unités en échec (`systemctl --failed`,
   `systemctl --user --failed`), paquets orphelins (`pacman -Qtdq`), et les
   avertissements laissés par les hooks à la fin de /var/log/pacman.log. Lis les
   annonces récentes d'Arch Linux si une intervention manuelle paraît nécessaire.
4. Applique enfin le workflow de mise à jour des dépôts décrit dans le CLAUDE.md
   de ce dépôt : ce qui a changé hors Stow, régénération des listes de paquets et
   d'APPS.md, contrôle des secrets, commit et push, puis la sync du dépôt public
   si le package waybar a bougé.

Demande-moi avant toute action destructive ou tout commit inhabituel."""

PROMPT_WITH_UPDATES = "Mets le système à jour, puis fais le suivi."
PROMPT_NOTHING_TODO = ("Aucun paquet n'est en attente : les points 1 à 3 seront"
                       " sans doute vides, va au point 4.")

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

EXTRA_CSS = """
/* Le compteur d'une famille de paquets : c'est le chiffre qu'on vient
   chercher, il a droit au bleu d'accent plutôt qu'au gris des valeurs. */
.row-value.count { color: #0a84ff; font-weight: bold; }
.row-label.warn  { color: #ff9f0a; }
.row-icon.warn   { color: #ff9f0a; }
.row-label.ok    { color: #32d74b; }
.row-icon.ok     { color: #32d74b; }
expander title { color: rgba(235, 235, 245, 0.62); font-size: 12px; }
expander title:hover { color: #ebebf0; }
""".encode()

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
        return [], [], ""
    head, _, tail = raw.partition("\n---\n")
    parse = lambda block: [l.split()[0] for l in block.splitlines() if l.strip()]
    return parse(head), parse(tail), raw


def explicit_packages():
    try:
        out = subprocess.run(["pacman", "-Qqe"], capture_output=True, text=True,
                             timeout=15).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def since_label(when):
    """« hier », « il y a 3 jours » — à partir d'un epoch."""
    if not when:
        return None
    days = int((time.time() - when) // 86400)
    if days == 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return "il y a %d jours" % days


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
        return since_label(time.mktime(time.strptime(stamp[:19],
                                                     "%Y-%m-%dT%H:%M:%S")))
    except (OSError, ValueError):
        return None


def read_meta(raw):
    """Métadonnées pré-calculées par updates.sh, ou None.

    Trois appels — `pacman -Sp`, `pacman -Qqe`, la lecture du log — tenaient la
    fenêtre fermée pendant une demi-seconde. updates.sh les fait maintenant en
    amont, à chaque relevé du module. `sig` garantit que le fichier décrit bien
    le cache qu'on vient de lire : sinon on refait le calcul soi-même.
    """
    try:
        with open(META) as fh:
            meta = dict(line.split(" ", 1) for line in fh.read().splitlines()
                        if " " in line)
    except (OSError, ValueError):
        return None
    want = hashlib.sha1(raw.encode()).hexdigest()
    if meta.get("sig", "").strip() != want:
        return None
    try:
        size = int(meta.get("size", "").strip() or 0)
    except ValueError:
        size = 0
    try:
        last = int(meta.get("last", "").strip() or 0)
    except ValueError:
        last = 0
    return {"size": size, "last": last, "apps": set(meta.get("apps", "").split())}


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
    """Un état, une répartition, deux actions.

    Le popup répondait déjà à la bonne question — *quoi* mettre à jour, pas
    seulement *combien* — mais son plan restait une colonne de libellés et de
    boutons. Les familles de paquets forment maintenant une carte : quatre
    lignes comparables, chacune avec son compte à droite.
    """

    IC_SYSTEM = "\U000f0493"    # engrenage
    IC_APPS = "\U000f03d7"      # paquet
    IC_DEPS = "\U000f01a7"      # briques
    IC_AUR = "\U000f08c7"       # logo Arch
    IC_OK = "\U000f012c"        # coche
    IC_WARN = "\U000f0026"      # alerte
    IC_PENDING = "\U000f0dbe"   # téléchargement en attente
    IC_REFRESH = "\U000f0453"   # actualiser
    IC_UPGRADE = "\U000f06b1"   # tout mettre à jour
    IC_SUPERVISED = "\U000f0068"  # baguette : mise à jour suivie par Claude

    def __init__(self):
        super().__init__("Mises à jour", width=380, margin_right=10)
        apply_extra_css()
        self._build()

    # ---- Construction ----

    def _build(self):
        repo, aur, raw = read_cache()
        meta = read_meta(raw)
        explicit = meta["apps"] if meta else explicit_packages()

        system = [p for p in repo if matches(p, SYSTEM_PKGS)]
        apps = [p for p in repo if p not in system and p in explicit]
        deps = [p for p in repo if p not in system and p not in explicit]
        total = len(repo) + len(aur)

        if total == 0:
            since = since_label(meta["last"]) if meta else last_upgrade()
            state = self.add_card()
            row = state.action(self.IC_OK, "Système à jour",
                               subtitle=("Dernière mise à jour : %s" % since)
                               if since else None)
            self._tint(row, "ok")
            actions = self.add_card()
            actions.action(self.IC_REFRESH, "Vérifier maintenant",
                           on_click=self._refresh)
            self._add_supervised(actions)
            return

        # -- Résumé --
        size = meta["size"] / 1024 / 1024 if meta else download_size(repo)
        sub = []
        if size is not None and size >= 1:
            sub.append("%.0f Mo à télécharger" % size)
        since = since_label(meta["last"]) if meta else last_upgrade()
        if since:
            sub.append("dernière mise à jour %s" % since)

        state = self.add_card()
        state.action(self.IC_PENDING,
                     "%d paquet%s en attente" % (total, "s" if total > 1 else ""),
                     subtitle=" · ".join(sub) or None)
        if any(needs_reboot(p) for p in repo):
            row = state.action(self.IC_WARN, "Redémarrage nécessaire ensuite",
                               subtitle="noyau ou brique système mis à jour")
            self._tint(row, "warn")

        # -- Répartition par famille --
        families = Card()
        for icon, name, pkgs, hint in (
                (self.IC_SYSTEM, "Système", system, "noyau et pilotes"),
                (self.IC_APPS, "Applications", apps, "tes logiciels"),
                (self.IC_DEPS, "Dépendances", deps, "bibliothèques internes"),
                (self.IC_AUR, "AUR", aur, "à recompiler")):
            if not pkgs:
                continue
            row = families.action(icon, name, subtitle=hint,
                                  value=str(len(pkgs)))
            row.value_label.get_style_context().add_class("count")
        self.add_card("Répartition", card=families)

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
        exp.set_margin_start(4)
        self.box.pack_start(exp, False, False, 0)

        # -- Actions --
        # Deux engagements — chacun ouvre un terminal et travaille plusieurs
        # minutes —, pas des lignes de liste : chacun garde son bouton.
        #
        # L'action mise en avant est la session Claude. `yay -Syu` installe et
        # s'arrête là ; la session lit ce que la mise à jour laisse derrière —
        # .pacnew à fusionner, unités en échec, annonces Arch — et resynchronise
        # le dépôt de config. C'est la moitié du travail que le bouton brut ne
        # fait pas, et celle qu'on oublie.
        supervised = None
        if CLAUDE_BIN:
            supervised = Gtk.Button(
                label="%s  Mettre à jour avec Claude" % self.IC_SUPERVISED)
            supervised.set_tooltip_text(
                "Installe, contrôle ce que la mise à jour laisse à faire, "
                "puis resynchronise le dépôt de config")
            supervised.connect("clicked", lambda _b: self._supervised(True))

        btn = Gtk.Button(label="\U000f06b1  Tout mettre à jour")
        btn.set_tooltip_text("yay -Syu dans un terminal, sans relecture ensuite")
        btn.connect("clicked", self._upgrade)

        # Le bleu d'accent ne se porte qu'une fois par fenêtre : il désigne
        # l'action recommandée, pas « les boutons ». Sans Claude installé,
        # c'est la mise à jour brute qui le reprend.
        (supervised or btn).get_style_context().add_class("accent")
        for b in filter(None, (supervised, btn)):
            self.box.pack_start(b, False, False, 0)

        more = self.add_card()
        more.action(self.IC_REFRESH, "Actualiser la liste",
                    on_click=self._refresh)

    @staticmethod
    def _tint(row, css):
        """Colore libellé et icône d'une ligne d'état (vert, orange)."""
        row.title_label.get_style_context().add_class(css)
        row.icon_label.get_style_context().add_class(css)

    def _detail_markup(self, system, apps, deps, aur):
        parts = []
        for title, pkgs in (("Système", system), ("Applications", apps),
                            ("Dépendances", deps), ("AUR", aur)):
            if not pkgs:
                continue
            body = GLib.markup_escape_text(", ".join(sorted(pkgs)))
            parts.append("<b>%s</b>\n<span size='small'>%s</span>" % (title, body))
        return "\n\n".join(parts)

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

    def _add_supervised(self, card):
        """Ligne « resynchroniser la config », si Claude Code est installé.

        Réservée à l'écran « système à jour » : rien à installer, donc pas
        d'action principale à mettre en avant — reste le dépôt de config, qui
        peut avoir dérivé depuis la dernière mise à jour.
        """
        if not CLAUDE_BIN:
            return
        card.action(self.IC_SUPERVISED, "Resynchroniser la config",
                    subtitle="session Claude sur le dépôt de config",
                    on_click=lambda _b: self._supervised(False))

    def _supervised(self, upgrade):
        """Ouvre une session Claude qui mène la mise à jour de bout en bout.

        Elle installe elle-même : `sudo` est en NOPASSWD sur cette machine, donc
        `yay -Syu --noconfirm` aboutit sans terminal pour y taper un mot de passe.
        Le terminal reste nécessaire pour la conversation — c'est là que la
        session demande un arbitrage et qu'on valide ses commandes.
        """
        intro = PROMPT_WITH_UPDATES if upgrade else PROMPT_NOTHING_TODO
        cwd = CONFIG_REPO if os.path.isdir(CONFIG_REPO) else os.path.expanduser("~")
        script = "%s %s; %s --refresh" % (
            shlex.quote(CLAUDE_BIN), shlex.quote(CLAUDE_PROMPT % intro),
            shlex.quote(UPDATES_SH))
        subprocess.Popen(
            ["kitty", "--class", "waybar.modules", "--directory", cwd,
             "-T", "Mise à jour par Claude", "bash", "-c", script],
            start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _refresh(self, _btn):
        subprocess.Popen([UPDATES_SH, "--refresh"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(UpdatesPopup, "waybar-updates-menu")


if __name__ == "__main__":
    main()
