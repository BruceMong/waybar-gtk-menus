#!/usr/bin/env python3
"""Base commune pour les popups Waybar (style menu luminosité).

Fournit une fenêtre GTK3 sur le layer overlay (gtk-layer-shell), ancrée en
haut à droite, avec :
  - fermeture par Échap / Entrée / clic en dehors / bouton croix (✕)
    Le clic en dehors n'est pas intercepté : il atteint la fenêtre visée
    (voir detach_pointer_focus).
  - matériau translucide flouté par le compositeur, palette système macOS
  - un en-tête (titre + croix) déjà construit dans self.box
"""

import atexit
import fcntl
import os
import re
import signal
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, Pango, GtkLayerShell  # noqa: E402

try:                                    # GLib.unix_signal_add est déprécié
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix
    unix_signal_add = GLibUnix.signal_add
except (ValueError, ImportError):       # PyGObject plus ancien
    unix_signal_add = GLib.unix_signal_add

import tokens  # noqa: E402


CSS = ("""
/* Les couleurs de cette feuille sont nommées, pas écrites : les
   `@define-color` sont injectés en tête depuis tokens.py, seule source de la
   palette (`@ink`, `@sysBlue`…). GTK3 n'ayant pas de variables CSS, c'est le
   seul mécanisme de nommage disponible, et il exige la déclaration AVANT
   l'usage — d'où la concaténation plutôt qu'un simple littéral.

   Restent en dur les deux nuances typographiques locales, `#f5f5f7` pour le
   titre et `#f0f0f5` pour le libellé de ligne : elles ne disent pas un rôle
   système mais une hiérarchie interne à la carte, et n'ont donc rien à faire
   dans une palette partagée. */
/* ══════════════════════════════════════════════════════════════════════════
   Feuille commune des popups Waybar — vocabulaire « Control Center » macOS.

   Trois strates, du fond vers l'avant :
     1. la fenêtre   : matériau translucide flouté par le compositeur,
     2. la carte     : `.card`, un aplat clair qui regroupe des lignes,
     3. la ligne     : `.row`, l'unité cliquable (icône · libellé · accessoire).

   Ce qui distingue ce vocabulaire de l'ancien (une pile de boutons pleine
   largeur) : les actions parentes vivent dans une même carte, séparées par un
   filet en retrait, au lieu de flotter chacune sur son propre fond. C'est le
   groupement qui porte le sens — l'œil lit « bloc système » puis « bloc
   dangereux », pas huit boutons interchangeables.
   ══════════════════════════════════════════════════════════════════════════ */

/* Même pile que la barre, dans le même ordre : SF Pro Text d'abord (paquet
   AUR `otf-san-francisco`), Inter puis Adwaita Sans en repli, Nerd Font en
   queue pour les glyphes. L'ordre comptait : la barre demandait SF Pro et ses
   propres popups demandaient Inter — cliquer un module changeait de police au
   milieu du geste, ce qui est exactement ce qu'une famille système interdit. Sans cette règle les
   popups héritent du gtk-font-name système, ici une monospace — ce qui suffit
   à trahir l'ensemble. La taille est fixée ici plutôt que laissée au réglage
   système : la hiérarchie typographique ci-dessous (15 / 13 / 11) n'a de sens
   qu'à partir d'une base connue. */
window, label, button, entry, switch, scale, list, row, popover, menu {
    font-family: "SF Pro Text", "Inter", "Adwaita Sans",
                 "Material Symbols Rounded",
                 "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font",
                 "Symbols Nerd Font", "Noto Sans Symbols 2";
    font-size: 13px;
}

/* Popover façon macOS : matériau translucide, coins arrondis, bordure
   spéculaire d'un pixel. Le flou vient du compositeur (bloc `layerrule`
   waybar-popup dans hyprland.lua), pas d'ici — GTK3 n'a pas de
   backdrop-filter. La fenêtre reçoit un visual RGBA côté Python, sans quoi
   l'alpha serait aplati sur du noir et les coins arrondis laisseraient des
   angles noirs. */
window {
    background-color: rgba(28, 28, 30, 0.74);
    color: @ink;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.13);
}
label { color: @ink; }

/* ── Hiérarchie typographique ──
   Trois niveaux seulement. Au-delà, l'échelle cesse d'être lisible comme une
   hiérarchie et devient du bruit. */
.popup-title { font-size: 15px; font-weight: 600; color: #f5f5f7; }
.section-title {
    font-size: 12px;
    font-weight: 600;
    color: rgba(235, 235, 245, 0.62);
    margin-left: 4px;
}
.caption {
    font-size: 11px;
    color: rgba(235, 235, 245, 0.55);
}

/* ── Carte ──
   L'aplat qui regroupe les lignes. Volontairement sans bordure : sur un
   matériau translucide, un liseré supplémentaire empile deux contours à 1 px
   (celui de la fenêtre, celui de la carte) et l'ensemble se met à grésiller. */
.card {
    background-color: rgba(255, 255, 255, 0.065);
    border-radius: 10px;
}

/* Filet inter-lignes, en retrait sous la colonne d'icônes (12 de padding +
   22 d'icône + 10 d'écart = 44) : le regard descend le long du texte sans
   être coupé à chaque ligne. */
.card separator {
    background-color: rgba(255, 255, 255, 0.075);
    background-image: none;
    min-height: 1px;
    margin-left: 44px;
}
.card separator.flush { margin-left: 0; }

/* ── Ligne ──
   GTK3 ne découpe pas les enfants aux coins arrondis du parent : c'est la
   ligne elle-même qui doit porter le rayon, sinon son survol déborde en angle
   droit sur les coins de la carte. */
.row {
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 0;
    padding: 9px 12px;
    min-height: 24px;
    transition: background-color 110ms ease-out;
}
.card > .row:first-child { border-radius: 10px 10px 0 0; }
.card > .row:last-child  { border-radius: 0 0 10px 10px; }
.card > .row:only-child  { border-radius: 10px; }

button.row:hover  { background-color: rgba(255, 255, 255, 0.10); }
button.row:active { background-color: rgba(255, 255, 255, 0.16); }
button.row:disabled { background-color: transparent; }

/* Une ligne inerte (celle qui porte un interrupteur ou un curseur) ne doit
   pas réagir au survol : rien ne s'y déclenche au clic. */
.row.static:hover { background-color: transparent; }
/* Ligne qui occupe toute la largeur de la carte (une liste imbriquée, une
   image) : le retrait de la gouttière lui reviendrait à laisser deux bandes
   vides sur les côtés. */
.row.bleed { padding: 0; }

/* L'icône doit peser autant que le libellé qu'elle annonce : à 15 px et 78 %
   d'opacité elle passait pour une décoration posée devant le texte, alors que
   c'est elle qu'on lit en premier en parcourant une carte. */
.row-icon {
    font-size: 17px;
    color: rgba(235, 235, 245, 0.88);
}
.row-label { color: #f0f0f5; }
.row-sub {
    font-size: 11px;
    color: rgba(235, 235, 245, 0.52);
}
/* Valeur ou état à droite : gris, jamais accentué — c'est une information,
   pas une action. */
.row-value { color: rgba(235, 235, 245, 0.55); }
.row-chevron {
    font-size: 12px;
    color: rgba(235, 235, 245, 0.32);
}

/* Ligne sélectionnée (sortie audio active, réseau connecté) : teinte bleue
   franche mais tenue, plutôt qu'un aplat plein qui ferait bloc dans la carte. */
.row.selected { background-color: rgba(10, 132, 255, 0.22); }
button.row.selected:hover { background-color: rgba(10, 132, 255, 0.30); }
.row.selected .row-icon, .row.selected .row-label { color: @onAccent; }

/* Action destructive : c'est le texte qui rougit, pas le fond. Un aplat rouge
   pleine largeur crie plus fort que le danger réel d'un redémarrage, et deux
   aplats côte à côte (Redémarrer, Éteindre) saturent tout le popup. */
.row.destructive .row-label,
.row.destructive .row-icon { color: @sysRed; }
button.row.destructive:hover { background-color: rgba(255, 69, 58, 0.16); }

/* ── En-tête ── */
button.close-btn {
    color: rgba(235, 235, 245, 0.5);
    background: none;
    background-image: none;
    border: none;
    box-shadow: none;
    padding: 0;
    min-width: 22px;
    min-height: 22px;
    border-radius: 11px;
    font-size: 12px;
    transition: background-color 110ms ease-out, color 110ms ease-out;
}
button.close-btn:hover {
    color: @onAccent;
    background-color: rgba(255, 255, 255, 0.16);
}

/* ── Curseurs ──
   Rail fin, molette blanche large : les proportions du slider système. Comme
   pour l'interrupteur, les marges du thème doivent être remises à zéro — elles
   portaient le curseur à 56 px de haut, soit une ligne une fois et demie plus
   haute que ses voisines. Remis à 25 px, il tombe pile dans le gabarit. */
scale { min-height: 0; margin: 0; padding: 0; }
scale trough {
    background-color: rgba(255, 255, 255, 0.16);
    background-image: none;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    margin: 0;
}
scale highlight {
    background-color: @sysBlue;
    background-image: none;
    border: none;
    border-radius: 3px;
    min-height: 6px;
}
scale slider {
    background-color: @onAccent;
    background-image: none;
    border-radius: 50%;
    min-width: 18px;
    min-height: 18px;
    margin: 0;
    border: none;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
}
/* La graduation (le repère des 100 % quand le curseur monte à 150) tient sur
   sept pixels : un trait, pas une réglette. */
scale marks { min-height: 7px; margin: 0; }
scale marks mark indicator { min-height: 4px; min-width: 1px; }
scale value { color: rgba(235, 235, 245, 0.55); font-size: 12px; padding-left: 10px; }
scale mark label { color: rgba(235, 235, 245, 0.42); font-size: 10px; }

/* ── Interrupteurs ──
   Le gabarit doit être posé de force : le thème système ajoute une marge et
   une bordure au curseur, qui poussaient l'interrupteur à 48 × 36 px — une
   ligne d'un tiers plus haute que ses voisines dans la même carte. Remises à
   zéro, les dimensions demandées sont respectées (44 × 24, soit une ligne de
   42 px, exactement la hauteur d'une ligne-bouton). */
switch {
    background-color: rgba(255, 255, 255, 0.17);
    background-image: none;
    margin: 0;
    border: none;
    box-shadow: none;
    padding: 2px;
    border-radius: 12px;
    min-width: 36px;
    min-height: 20px;
    transition: background-color 140ms ease-out;
}
switch:checked { background-color: @sysBlue; }
switch slider {
    background-color: @onAccent;
    margin: 0;
    border: none;
    border-radius: 50%;
    min-width: 20px;
    min-height: 20px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}
/* Variante « état matériel » (micro coupé, Ne pas déranger) : vert système,
   comme les bascules des Réglages plutôt que celles du Control Center. */
switch.green:checked { background-color: @sysGreen; }

/* ── Boutons pleins ──
   Réservés à l'action franche d'un popup (« Démarrer l'enregistrement »,
   « Rafraîchir »). Tout le reste passe par `.row` dans une carte. */
button {
    background-color: rgba(255, 255, 255, 0.09);
    background-image: none;
    color: @ink;
    border: none;
    box-shadow: none;
    border-radius: 8px;
    padding: 8px 12px;
    transition: background-color 110ms ease-out;
}
button:hover { background-color: rgba(255, 255, 255, 0.16); }
button:active { background-color: rgba(255, 255, 255, 0.22); }
button:disabled { color: rgba(235, 235, 245, 0.3); }

/* Bouton d'action principale : bleu système, encre blanche — sur macOS le
   texte d'un bouton accentué est blanc, jamais la couleur du fond. */
button.accent { background-color: @sysBlue; color: @onAccent; }
button.accent:hover { background-color: @sysBlueHover; }

/* Action en cours qu'on vient interrompre (arrêter un enregistrement) : rouge
   système, même logique d'encre blanche que le bouton accentué. */
button.danger { background-color: @sysRed; color: @onAccent; }
button.danger:hover { background-color: @sysRedHover; }

/* ── Champs de saisie ── */
entry {
    background-color: rgba(255, 255, 255, 0.09);
    background-image: none;
    color: @ink;
    border: none;
    box-shadow: none;
    border-radius: 8px;
    padding: 7px 10px;
}
entry:focus { outline: 2px solid rgba(10, 132, 255, 0.75); outline-offset: -2px; }

/* ── Barres de défilement : superposées et fines, pas une gouttière ── */
scrollbar, scrollbar trough {
    background-color: transparent;
    background-image: none;
    border: none;
}
scrollbar slider {
    background-color: rgba(255, 255, 255, 0.22);
    border-radius: 4px;
    min-width: 6px;
    min-height: 28px;
    margin: 2px;
    border: none;
}
scrollbar slider:hover { background-color: rgba(255, 255, 255, 0.36); }

/* ── Focus clavier : anneau bleu, comme le focus ring système ── */
button:focus,
switch:focus,
scale:focus,
row:focus,
list row:focus,
checkbutton:focus,
*:focus {
    outline: 2px solid rgba(10, 132, 255, 0.75);
    outline-offset: -2px;
}

/* Variante « blue » : conservée pour les menus qui ajoutent encore la classe
   à leur fenêtre. L'accent étant désormais bleu partout, elle ne modifie plus
   rien — la garder évite de devoir toucher chaque appelant. */""")
CSS = (tokens.css_vars() + CSS).encode()


HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")
# Liste des popups qui ont détaché le focus du curseur (un PID par ligne).
# Nécessaire pour ne rétablir le réglage qu'au dernier popup fermé.
FOCUS_LOCK = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "waybar-popup-focus")


def _hyprctl(*args):
    """hyprctl silencieux : hors Hyprland, on veut juste ne rien casser."""
    try:
        return subprocess.run(["hyprctl", *args], timeout=2, check=False,
                              capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None


def _config_follow_mouse():
    """Valeur de input:follow_mouse telle qu'écrite dans hyprland.lua.

    C'est elle qu'on rétablit, pas la valeur lue à chaud : si un popup a été
    tué avant d'avoir pu faire le ménage, le réglage courant vaut encore 2 et
    le popup suivant le figerait définitivement.
    """
    try:
        with open(HYPR_CONF, encoding="utf-8") as fh:
            found = re.findall(r"^\s*follow_mouse\s*=\s*(\d+)", fh.read(),
                               re.M)
        return int(found[-1]) if found else 1
    except (OSError, ValueError):
        return 1


def _proc_start(pid):
    """Date de démarrage du processus (champ 22 de /proc/<pid>/stat), ou None.

    Le noyau recycle les PIDs : seul le couple (pid, date de démarrage)
    identifie un processus de façon stable. Le nom de la commande, entre
    parenthèses, peut contenir des espaces — d'où le découpage après la
    DERNIÈRE parenthèse fermante.
    """
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
            data = fh.read()
        return int(data[data.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _lock_pids():
    """Popups encore vivants inscrits dans le verrou, en (pid, démarrage).

    Une entrée dont le PID n'existe plus — ou existe mais appartient désormais
    à un autre processus, le PID ayant été recyclé — est purgée. Sans cette
    seconde vérification, un popup tué sans avoir pu faire son ménage laisse
    une entrée qui finit par « revivre » sur un processus quelconque : plus
    aucun popup ne rétablit alors follow_mouse, et la session reste en mode 2
    sans que rien ne puisse la réparer.
    """
    entries = []
    try:
        with open(FOCUS_LOCK, encoding="utf-8") as fh:
            for line in fh.read().splitlines():
                fields = line.split()
                if len(fields) != 2:
                    continue  # format d'une version antérieure : périmé
                entries.append((int(fields[0]), int(fields[1])))
    except (OSError, ValueError):
        return []
    return [(pid, start) for pid, start in entries
            if _proc_start(pid) == start]


def _write_lock(entries):
    try:
        with open(FOCUS_LOCK, "w", encoding="utf-8") as fh:
            fh.write("\n".join("%d %d" % e for e in entries))
    except OSError:
        pass


_focus_detached = False


def detach_pointer_focus():
    """Détache le focus clavier du survol de la souris (follow_mouse = 2).

    Un popup se ferme quand il perd le focus clavier. Avec le réglage habituel
    (follow_mouse = 1), le simple passage du curseur au-dessus d'une fenêtre le
    lui volerait : le menu se fermerait au moindre mouvement. En mode 2, seul
    un vrai clic déplace le focus — précisément l'événement sur lequel on veut
    fermer, et ce clic-là part bien à la fenêtre visée puisque plus aucune
    surface ne l'intercepte.

    Le réglage d'origine est rétabli par restore_pointer_focus(), une fois le
    dernier popup fermé.
    """
    global _focus_detached
    if _focus_detached:
        return
    _focus_detached = True
    _write_lock(_lock_pids() + [(os.getpid(), _proc_start(os.getpid()))])
    _hyprctl("keyword", "input:follow_mouse", "2")
    atexit.register(restore_pointer_focus)
    # Un SIGTERM (fin de session, pkill) doit lui aussi rendre le réglage :
    # atexit ne s'exécuterait pas. GLib.unix_signal_add fait passer le signal
    # par la boucle principale, contrairement à signal.signal qui resterait en
    # attente tant que GTK ne rend pas la main.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        # PRIORITY_DEFAULT, surtout pas PRIORITY_HIGH. GLib arrête sa passe
        # `check` dès qu'une source prête est de priorité plus haute que les
        # suivantes : au-dessus de la source d'événements GDK (elle est à
        # G_PRIORITY_DEFAULT), celle-ci n'est jamais vérifiée. Or GDK a réservé
        # la lecture de la socket Wayland dans sa passe `prepare`
        # (wl_display_prepare_read) et ne la rend que dans `check` : la
        # réservation reste posée, et l'aller-retour Wayland que gtk_main()
        # fait en sortant attend indéfiniment un lecteur qui ne viendra pas.
        # Le popup reste alors affiché, insensible au clavier comme à la
        # souris, et seul un kill -9 en vient à bout. Reproduit à 100 %.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _on_term)


def _on_term(*_):
    restore_pointer_focus()
    Gtk.main_quit()
    arm_exit_watchdog()
    return False


def arm_exit_watchdog(delay=1.5):
    """Coupe court si la fermeture ne va pas à son terme.

    On ne peut plus compter sur la boucle principale : main_quit() vient d'en
    sortir, et GTK est capable de se bloquer dans son dernier aller-retour
    Wayland. Un timer GLib n'y suffirait donc pas — d'où un thread, seul à
    pouvoir encore agir pendant que le thread principal dort dans un appel
    natif. os._exit court-circuite atexit : le réglage follow_mouse est rendu
    ici, sans quoi la session resterait en mode 2.
    """
    def bail():
        restore_pointer_focus()
        os._exit(0)

    watchdog = threading.Timer(delay, bail)
    watchdog.daemon = True
    watchdog.start()


def restore_pointer_focus():
    """Rend son réglage follow_mouse à Hyprland, si plus aucun popup n'est là.

    Enchaîner deux menus ferme le premier (il perd le focus au profit du
    second) alors que le second vient de détacher le focus : le verrou évite
    que ce premier départ ne rétablisse le réglage sous les pieds du second.
    """
    global _focus_detached
    if not _focus_detached:
        return
    _focus_detached = False
    others = [e for e in _lock_pids() if e[0] != os.getpid()]
    _write_lock(others)
    if not others:
        _hyprctl("keyword", "input:follow_mouse", str(_config_follow_mouse()))


def apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


# ══════════════════════════════════════════════════════════════════════════
# Vocabulaire visuel : cartes et lignes
# ══════════════════════════════════════════════════════════════════════════
#
# Trois primitives suffisent à construire tous les popups :
#
#   Card()                 le groupe — un aplat arrondi, filets internes
#   action_row(...)        une ligne cliquable (icône · libellé · accessoire)
#   switch_row(...)        une ligne inerte portant un interrupteur
#
# La colonne d'icônes fait une largeur fixe : c'est elle qui aligne les
# libellés d'une carte à l'autre, et sur laquelle se cale le retrait des
# filets. Une ligne sans icône passe une chaîne vide plutôt que None — la
# colonne reste, l'alignement tient.

ICON_WIDTH = 22     # colonne d'icônes, en px
ROW_SPACING = 10    # écart icône ↔ texte


def _icon_slot(icon):
    """Colonne d'icône de largeur strictement fixe, ou None si `icon` est None.

    Un simple size_request sur le label ne suffirait pas : c'est un minimum, et
    un glyphe large (le réseau, l'enveloppe) en réclame davantage — le libellé
    de cette ligne-là décrocherait de quelques pixels vers la droite, ce qui se
    voit immédiatement dans une carte. Un GtkOverlay prend la taille de son
    enfant principal et ignore celle de ses calques : le gabarit vide impose
    22 px, le glyphe se centre dedans et déborde silencieusement s'il est plus
    large.
    """
    if icon is None:
        return None
    lbl = Gtk.Label(label=icon)
    lbl.set_halign(Gtk.Align.CENTER)
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.get_style_context().add_class("row-icon")
    gauge = Gtk.Box()
    gauge.set_size_request(ICON_WIDTH, -1)
    slot = Gtk.Overlay()
    slot.add(gauge)
    slot.add_overlay(lbl)
    slot.set_valign(Gtk.Align.CENTER)
    slot.label = lbl
    return slot


def _row_body(icon, title, subtitle=None, value=None, chevron=False,
              markup=False):
    """Corps commun d'une ligne. Renvoie (box, widgets_nommés)."""
    body = Gtk.Box(spacing=ROW_SPACING)
    refs = {}

    # Colonne d'icônes. `icon=""` la réserve sans rien y mettre — c'est ce
    # qu'il faut pour une ligne sans pictogramme au milieu de lignes qui en
    # ont, sinon son libellé décrocherait vers la gauche. `icon=None` la
    # supprime : dans une carte où aucune ligne n'a d'icône (la liste des
    # raccourcis), la réserver ne ferait qu'un retrait de 44 px sur du vide.
    ic = _icon_slot(icon)
    if ic is not None:
        body.pack_start(ic, False, False, 0)
    refs["icon_label"] = getattr(ic, "label", None)

    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    texts.set_valign(Gtk.Align.CENTER)
    lbl = Gtk.Label(xalign=0)
    lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.get_style_context().add_class("row-label")
    if markup:
        lbl.set_markup(title)
    else:
        lbl.set_text(title)
    texts.pack_start(lbl, False, False, 0)
    refs["title_label"] = lbl

    sub = Gtk.Label(xalign=0)
    sub.set_ellipsize(Pango.EllipsizeMode.END)
    # Un popup n'est pas redimensionnable : il prend sa largeur naturelle, et
    # set_default_size ne sert que de plancher. Sans borne, une explication un
    # peu bavarde élargit donc toute la fenêtre. Le titre, lui, reste libre —
    # c'est lui qu'on doit pouvoir lire en entier.
    sub.set_max_width_chars(34)
    sub.get_style_context().add_class("row-sub")
    sub.set_no_show_all(True)
    if subtitle:
        sub.set_text(subtitle)
        sub.set_visible(True)
    texts.pack_start(sub, False, False, 0)
    refs["subtitle_label"] = sub

    body.pack_start(texts, True, True, 0)

    # `pack_end` empile depuis la droite : le chevron passe donc avant la
    # valeur, sinon on lit « › pavucontrol » au lieu de « pavucontrol › ».
    if chevron:
        chev = Gtk.Label(label="›")
        chev.get_style_context().add_class("row-chevron")
        body.pack_end(chev, False, False, 0)

    val = Gtk.Label(xalign=1)
    val.get_style_context().add_class("row-value")
    val.set_no_show_all(True)
    if value:
        val.set_text(value)
        val.set_visible(True)
    body.pack_end(val, False, False, 0)
    refs["value_label"] = val

    return body, refs


def action_row(icon, title, subtitle=None, value=None, chevron=False,
               destructive=False, selected=False, on_click=None, markup=False,
               tooltip=None):
    """Ligne cliquable d'une carte.

    Les sous-widgets sont accrochés au bouton (`row.title_label`,
    `row.value_label`, `row.subtitle_label`, `row.icon_label`) : un menu qui
    rafraîchit son contenu met à jour ces labels au lieu de reconstruire la
    ligne, ce qui préserve le focus clavier et la position de défilement.
    """
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    ctx = btn.get_style_context()
    ctx.add_class("row")
    if destructive:
        ctx.add_class("destructive")
    if selected:
        ctx.add_class("selected")
    body, refs = _row_body(icon, title, subtitle, value, chevron, markup)
    btn.add(body)
    for name, widget in refs.items():
        setattr(btn, name, widget)
    if on_click is not None:
        btn.connect("clicked", on_click)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    return btn


def info_row(icon, title, subtitle=None, value=None, markup=False):
    """Ligne de pur constat : même gabarit qu'une ligne d'action, mais inerte.

    Rendre une `action_row` insensible aurait fait l'affaire visuellement, à
    ceci près que `:disabled` atténue le texte — or une ligne qui *informe*
    n'est pas une ligne indisponible, elle doit rester la plus lisible de la
    carte. D'où une ligne statique, comme celle des interrupteurs, mais sans
    accessoire à droite.
    """
    row = Gtk.Box(spacing=ROW_SPACING)
    ctx = row.get_style_context()
    ctx.add_class("row")
    ctx.add_class("static")
    body, refs = _row_body(icon, title, subtitle, value, markup=markup)
    row.pack_start(body, True, True, 0)
    for name, widget in refs.items():
        setattr(row, name, widget)
    return row


def switch_row(icon, title, active, handler, subtitle=None, green=False):
    """Ligne inerte portant un interrupteur. Renvoie (ligne, interrupteur)."""
    row = Gtk.Box(spacing=ROW_SPACING)
    ctx = row.get_style_context()
    ctx.add_class("row")
    ctx.add_class("static")

    body, refs = _row_body(icon, title, subtitle)
    row.pack_start(body, True, True, 0)

    sw = Gtk.Switch()
    sw.set_valign(Gtk.Align.CENTER)
    sw.set_active(active)
    if green:
        sw.get_style_context().add_class("green")
    if handler is not None:
        sw.connect("notify::active", handler)
    row.pack_end(sw, False, False, 0)

    for name, widget in refs.items():
        setattr(row, name, widget)
    row.switch = sw
    return row, sw


def control_row(icon, child, expand=True):
    """Ligne inerte : colonne d'icône, puis un contrôle qui occupe la largeur.

    C'est la ligne des curseurs et des jauges. L'icône reste dans la même
    colonne que celle des lignes-boutons, donc le curseur démarre exactement
    où démarrent les libellés au-dessus et en dessous.
    """
    row = Gtk.Box(spacing=ROW_SPACING)
    ctx = row.get_style_context()
    ctx.add_class("row")
    ctx.add_class("static")

    slot = _icon_slot(icon)
    if slot is not None:
        row.pack_start(slot, False, False, 0)

    row.pack_start(child, expand, expand, 0)
    row.icon_label = getattr(slot, "label", None)
    return row


def slider_row(icon, title, scale, value=None):
    """Ligne d'un réglage continu : titre et valeur au-dessus, curseur dessous.

    Un curseur seul ne dit pas ce qu'il règle, et un titre posé au-dessus de la
    carte le détache du contrôle. Les deux tiennent donc dans la même ligne :
    le libellé à gauche, la valeur courante à droite, le rail en dessous —
    aligné sur la colonne d'icônes, jamais sous l'icône.

    Renvoie (ligne, label_de_valeur) : le second sert à refléter la valeur
    pendant que l'on déplace le curseur.
    """
    stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    head = Gtk.Box(spacing=8)
    lbl = Gtk.Label(xalign=0)
    lbl.set_text(title)
    lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.get_style_context().add_class("row-label")
    head.pack_start(lbl, True, True, 0)
    val = Gtk.Label(xalign=1)
    val.get_style_context().add_class("row-value")
    if value is not None:
        val.set_text(value)
    head.pack_end(val, False, False, 0)
    stack.pack_start(head, False, False, 0)
    stack.pack_start(scale, False, False, 0)

    row = control_row(icon, stack)
    row.title_label = lbl
    row.value_label = val
    row.scale = scale
    return row, val


def custom_row(child, margins=True):
    """Ligne libre (curseur, VU-mètre, saisie) posée dans une carte."""
    row = Gtk.Box(spacing=ROW_SPACING)
    ctx = row.get_style_context()
    ctx.add_class("row")
    ctx.add_class("static")
    if not margins:
        ctx.add_class("bleed")
    row.pack_start(child, True, True, 0)
    return row


class Card(Gtk.Box):
    """Groupe de lignes apparentées.

    C'est le groupement qui porte le sens : « ces trois actions relèvent de la
    session », « ces deux-là coupent le courant ». Empilées sans écart et
    séparées d'un filet en retrait, elles se lisent comme un bloc ; espacées
    de 12 px chacune sur son propre fond, elles se lisaient comme huit
    boutons interchangeables.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("card")
        self._rows = 0

    def add_row(self, widget, separator=True, flush=False):
        if self._rows and separator:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            if flush:
                sep.get_style_context().add_class("flush")
            self.pack_start(sep, False, False, 0)
        self.pack_start(widget, False, False, 0)
        self._rows += 1
        return widget

    def action(self, icon, title, separator=True, **kwargs):
        return self.add_row(action_row(icon, title, **kwargs),
                            separator=separator)

    def toggle(self, icon, title, active, handler, separator=True, **kwargs):
        row, sw = switch_row(icon, title, active, handler, **kwargs)
        self.add_row(row, separator=separator)
        return sw

    def info(self, icon, title, separator=True, **kwargs):
        return self.add_row(info_row(icon, title, **kwargs),
                            separator=separator)

    def custom(self, child, separator=True, flush=False, margins=True):
        return self.add_row(custom_row(child, margins=margins),
                            separator=separator, flush=flush)

    def control(self, icon, child, separator=True, expand=True):
        return self.add_row(control_row(icon, child, expand=expand),
                            separator=separator)

    def sync_separators(self):
        """Recalcule les filets après un filtrage de lignes.

        Masquer une ligne laisse son filet derrière elle : deux traits collés
        là où deux lignes ont disparu, un trait en tête de carte quand c'est
        la première qui part. Un filet ne se montre donc que s'il sépare
        effectivement deux lignes visibles.
        """
        seen_visible = False
        pending = None          # dernier filet rencontré, en attente d'aval
        for child in self.get_children():
            if isinstance(child, Gtk.Separator):
                child.set_visible(False)
                pending = child if seen_visible else None
                continue
            if not child.get_visible():
                continue
            if pending is not None:
                pending.set_visible(True)
                pending = None
            seen_visible = True

    def slider(self, icon, title, scale, value=None, separator=True):
        row, val = slider_row(icon, title, scale, value)
        self.add_row(row, separator=separator)
        return row, val


def section_label(text):
    lbl = Gtk.Label(xalign=0)
    lbl.set_text(text)
    lbl.get_style_context().add_class("section-title")
    return lbl


def caption_label(text, markup=False, width_chars=40):
    """Texte explicatif secondaire, replié sur plusieurs lignes.

    La borne de largeur n'est pas cosmétique : la largeur *naturelle* d'un
    label replié est celle du texte mis sur une seule ligne. Un popup n'étant
    pas redimensionnable, une phrase d'explication suffisait à l'élargir de
    quatre-vingts pixels.
    """
    lbl = Gtk.Label(xalign=0)
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(width_chars)
    lbl.get_style_context().add_class("caption")
    if markup:
        lbl.set_markup(text)
    else:
        lbl.set_text(text)
    return lbl


class LayerPopup(Gtk.Window):
    """Popup overlay ancrée en haut à droite, à la sauce menu luminosité.

    Le contenu se construit en empilant des widgets dans self.box (l'en-tête
    titre + croix y est déjà présent).
    """

    def __init__(self, title, width=340, margin_right=150, margin_top=40):
        super().__init__(title=title)

        # Layer overlay (au-dessus de Waybar), focus clavier à la demande.
        # Surtout pas EXCLUSIVE : Hyprland réserve alors aussi le *pointeur* au
        # client du layer, si bien qu'aucun clic n'atteint plus les fenêtres du
        # dessous tant que le popup est ouvert.
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, margin_top)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, margin_right)

        # Namespace dédié : c'est lui que cible le bloc `layerrule`
        # waybar-popup dans hyprland.lua. Sans namespace propre on ne
        # pourrait viser que « gtk-layer-shell », commun à tous les clients
        # gtk-layer-shell de la session.
        GtkLayerShell.set_namespace(self, "waybar-popup")

        # Visual RGBA : sans lui GTK aplatit l'alpha du fond sur du noir. Le
        # matériau translucide et les coins arrondis en dépendent tous les
        # deux (sinon les angles restent noirs).
        _visual = Gdk.Screen.get_default().get_rgba_visual()
        if _visual is not None:
            self.set_visual(_visual)

        self.set_default_size(width, -1)
        self.set_resizable(False)
        self.connect("key-press-event", self._on_key)

        # Fermeture au clic dehors, sans surface de capture. Une fenêtre
        # transparente plein écran ferait bien l'affaire pour détecter le clic,
        # mais elle l'avalerait : le bouton visé derrière le popup ne le
        # recevrait jamais. On ferme donc sur perte du focus clavier, que seul
        # un clic provoque grâce à detach_pointer_focus().
        detach_pointer_focus()
        self._had_focus = False
        self._close_src = 0
        self.connect("notify::has-toplevel-focus", self._on_focus_change)
        self.connect("destroy", lambda *_: restore_pointer_focus())

        apply_css()

        # Conteneur principal + en-tête (titre + croix). Gouttière de 14 px :
        # les cartes touchent presque le bord, comme dans un popover système,
        # et le texte des lignes retombe à 26 px du bord de la fenêtre.
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.set_margin_top(14)
        self.box.set_margin_bottom(14)
        self.box.set_margin_start(14)
        self.box.set_margin_end(14)
        self._build_header(title)
        self.add(self.box)

    def _build_header(self, title):
        header = Gtk.Box(spacing=8)
        # Léger retrait pour que le titre ne colle pas au bord des cartes : il
        # les surplombe d'un cran, il ne s'aligne pas dessus.
        header.set_margin_start(4)
        header.set_margin_end(2)
        lbl = Gtk.Label(xalign=0)
        lbl.set_text(title)
        lbl.get_style_context().add_class("popup-title")
        header.pack_start(lbl, True, True, 0)

        btn = Gtk.Button(label="\u2715")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_valign(Gtk.Align.CENTER)
        btn.get_style_context().add_class("close-btn")
        btn.connect("clicked", lambda *_: self.close())
        header.pack_end(btn, False, False, 0)
        self.box.pack_start(header, False, False, 0)

    # ---- Assemblage du contenu ----

    def add_card(self, title=None, card=None, expand=False):
        """Ajoute une carte au popup, précédée d'un intertitre facultatif.

        Le couple (intertitre, carte) est enveloppé dans son propre conteneur :
        6 px les séparent l'un de l'autre, contre 12 px entre deux groupes.
        Sans cela, le spacing uniforme de `self.box` détacherait le titre de la
        carte qu'il désigne autant que du groupe précédent.
        """
        card = Card() if card is None else card
        if title is None:
            self.box.pack_start(card, expand, expand, 0)
            return card
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.pack_start(section_label(title), False, False, 0)
        wrap.pack_start(card, expand, expand, 0)
        self.box.pack_start(wrap, expand, expand, 0)
        return card

    def _on_key(self, _widget, event):
        kv = event.keyval
        # Échap ferme toujours.
        if kv == Gdk.KEY_Escape:
            self.close()
            return True
        # Flèches haut/bas : déplacer le focus d'un élément à l'autre, sauf si
        # le widget courant consomme déjà ces touches (slider, liste, champ).
        if kv in (Gdk.KEY_Up, Gdk.KEY_Down):
            focus = self.get_focus()
            # Champs de saisie : les fleches leur sont reservees. Les curseurs
            # (Gtk.Scale) sont horizontaux -> gauche/droite ajuste la valeur,
            # donc haut/bas reste libre pour changer de barre.
            if isinstance(focus, (Gtk.Entry, Gtk.SpinButton)):
                return False
            if focus is not None and focus.get_ancestor(Gtk.ListBox) is not None:
                return False
            direction = (Gtk.DirectionType.TAB_BACKWARD if kv == Gdk.KEY_Up
                         else Gtk.DirectionType.TAB_FORWARD)
            self.child_focus(direction)
            return True
        # Entrée / Tab : laisser GTK activer ou parcourir le widget ciblé.
        return False

    def _on_focus_change(self, *_):
        if self._close_src:
            GLib.source_remove(self._close_src)
            self._close_src = 0
        if self.props.has_toplevel_focus:
            self._had_focus = True
            return
        # Avant le tout premier focus, rien à fermer : la fenêtre vient d'être
        # mappée et le compositeur ne lui a pas encore donné le clavier.
        if not self._had_focus:
            return
        # Court sursis : un aller-retour de focus (menu GTK qui s'ouvre, popup
        # système) ne doit pas passer pour un clic en dehors.
        self._close_src = GLib.timeout_add(150, self._close_if_unfocused)

    def _close_if_unfocused(self):
        self._close_src = 0
        if not self.props.has_toplevel_focus:
            self.close()
        return False

    def _grab_first_focus(self):
        """Donne le focus au premier élément interactif du contenu.

        On saute l'en-tête (index 0 = titre + croix) pour ne pas démarrer sur
        le bouton de fermeture.
        """
        for child in self.box.get_children()[1:]:
            if self._focus_into(child):
                return
        # Repli : au moins un widget focusable (header inclus).
        self.child_focus(Gtk.DirectionType.TAB_FORWARD)

    @staticmethod
    def _focus_into(widget):
        if (widget.get_can_focus() and widget.get_sensitive()
                and widget.get_visible()):
            widget.grab_focus()
            return True
        if isinstance(widget, Gtk.Container):
            for c in widget.get_children():
                if LayerPopup._focus_into(c):
                    return True
        return False

    def run(self):
        self.connect("destroy", Gtk.main_quit)
        # Le popup détruit, le process n'a plus qu'à rendre la main : s'il s'y
        # attarde, c'est qu'il est bloqué (cf. arm_exit_watchdog).
        self.connect("destroy", lambda *_: arm_exit_watchdog())
        self.show_all()
        self._grab_first_focus()
        Gtk.main()


# ══════════════════════════════════════════════════════════════════════════
# Lancement : une seule instance à la fois, et le clic qui rouvre referme
# ══════════════════════════════════════════════════════════════════════════

def single_instance(name):
    """Verrou d'instance unique, avec bascule.

    Waybar relance le script à chaque clic sur l'icône. Sans verrou, deux
    popups se superposaient : la barre est un layer qui ne prend pas le focus
    clavier, donc le premier popup ne le perd jamais et ne se ferme pas de
    lui-même. Recliquer sur l'icône empilait une seconde fenêtre au lieu de
    refermer la première — vérifié, deux processus vivants pour deux clics.

    Un second lancement termine donc l'instance en place et rend la main :
    l'icône de la barre devient une bascule, comme on l'attend d'un menu.

    Renvoie le fichier de verrou (à garder référencé le temps du popup), ou
    None s'il faut sortir immédiatement.
    """
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                        name + ".lock")
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.seek(0)
        try:
            os.kill(int(f.read().strip()), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def run_popup(factory, lock_name):
    """Ouvre un popup en instance unique, et le referme si on le relance.

    `factory` est appelé seulement une fois le verrou obtenu : construire la
    fenêtre pour la détruire aussitôt ferait clignoter une surface à l'écran.
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    lock = single_instance(lock_name)
    if lock is None:
        return None
    win = factory()
    # SIGTERM (envoyé par le lancement suivant) : fermeture propre plutôt
    # qu'une fenêtre tuée en laissant le réglage follow_mouse détaché.
    unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                    lambda *_: (win.close(), False)[1])
    win.run()
    return win
