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
import os
import re
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell  # noqa: E402


CSS = """
/* Même pile que la barre : Inter (ou Adwaita Sans, son dérivé déjà présent)
   pour le texte, Nerd Font en queue pour les glyphes. Sans cette règle les
   popups héritent du gtk-font-name système, ici une monospace — ce qui suffit
   à trahir l'ensemble. */
window, label, button, entry, switch, scale, list, row, popover, menu {
    font-family: "Inter", "Adwaita Sans", "SF Pro Text",
                 "Material Symbols Rounded",
                 "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font",
                 "Symbols Nerd Font", "Noto Sans Symbols 2";
}
/* Popover façon macOS : matériau translucide, coins arrondis, bordure
   spéculaire d'un pixel. Le flou vient du compositeur (bloc `layerrule`
   waybar-popup dans hyprland.conf), pas d'ici — GTK3 n'a pas de
   backdrop-filter. La fenêtre reçoit un visual RGBA côté Python, sans quoi
   l'alpha serait aplati sur du noir et les coins arrondis laisseraient des
   angles noirs. */
window {
    background-color: rgba(30, 30, 32, 0.72);
    color: #ebebf0;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.14);
}
label { color: #ebebf0; }

/* ── Curseurs ── */
scale trough {
    background-color: rgba(255, 255, 255, 0.14);
    border-radius: 4px;
    min-height: 8px;
}
scale highlight {
    background-color: #0a84ff;
    border-radius: 4px;
    min-height: 8px;
}
scale slider {
    background-color: #ffffff;
    border-radius: 50%;
    min-width: 18px;
    min-height: 18px;
    margin: -5px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
}
scale value { color: #9a9aa2; font-size: 12px; }
scale mark label { color: #68686f; font-size: 10px; }

/* ── Interrupteurs ── */
switch {
    background-color: rgba(255, 255, 255, 0.16);
    border-radius: 12px;
    min-width: 40px;
    min-height: 20px;
}
switch:checked { background-color: #0a84ff; }
switch slider {
    background-color: #ffffff;
    border-radius: 50%;
    min-width: 16px;
    min-height: 16px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
}

/* ── Boutons ──
   Remplissage translucide plutôt qu'une couleur pleine : posé sur le
   matériau, il reste solidaire du fond au lieu de flotter dessus. */
button {
    background-color: rgba(255, 255, 255, 0.09);
    color: #ebebf0;
    border: none;
    box-shadow: none;
    border-radius: 7px;
    padding: 8px 12px;
}
button:hover { background-color: rgba(255, 255, 255, 0.16); }
button:active { background-color: rgba(255, 255, 255, 0.22); }

/* Bouton d'action principale : bleu système, encre blanche — sur macOS le
   texte d'un bouton accentué est blanc, jamais la couleur du fond. */
button.accent { background-color: #0a84ff; color: #ffffff; }
button.accent:hover { background-color: #409cff; }

/* Action en cours qu'on vient interrompre (arrêter un enregistrement) : rouge
   système, même logique d'encre blanche que le bouton accentué. */
button.danger { background-color: #ff453a; color: #ffffff; }
button.danger:hover { background-color: #ff6961; }

/* ── Champs de saisie ── */
entry {
    background-color: rgba(255, 255, 255, 0.09);
    color: #ebebf0;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
}
entry:focus { outline: 2px solid rgba(10, 132, 255, 0.75); outline-offset: -2px; }

button.close-btn {
    color: #9a9aa2;
    background: none;
    padding: 0 6px;
    min-width: 24px;
    min-height: 24px;
    font-size: 14px;
}
button.close-btn:hover {
    color: #ff453a;
    background-color: rgba(255, 69, 58, 0.18);
    border-radius: 6px;
}

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
   rien — la garder évite de devoir toucher chaque appelant. */
""".encode()


HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.conf")
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
    """Valeur de input:follow_mouse telle qu'écrite dans hyprland.conf.

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
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, _on_term)


def _on_term(*_):
    restore_pointer_focus()
    Gtk.main_quit()
    # Filet : si le signal est arrivé avant le démarrage de la boucle (un menu
    # comme keybinds met un instant à se construire), main_quit() n'a rien
    # arrêté et le popup resterait à l'écran. On ne laisse pas traîner.
    GLib.timeout_add(200, lambda: os._exit(0))
    return False


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
        # waybar-popup dans hyprland.conf. Sans namespace propre on ne
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

        # Conteneur principal + en-tête (titre + croix).
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.set_margin_top(16)
        self.box.set_margin_bottom(16)
        self.box.set_margin_start(20)
        self.box.set_margin_end(20)
        self._build_header(title)
        self.add(self.box)

    def _build_header(self, title):
        header = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b>" % GLib.markup_escape_text(title))
        header.pack_start(lbl, True, True, 0)

        btn = Gtk.Button(label="✕")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_valign(Gtk.Align.CENTER)
        btn.get_style_context().add_class("close-btn")
        btn.connect("clicked", lambda *_: self.close())
        header.pack_end(btn, False, False, 0)
        self.box.pack_start(header, False, False, 0)

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
        self.show_all()
        self._grab_first_focus()
        Gtk.main()
