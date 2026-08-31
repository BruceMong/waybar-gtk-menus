#!/usr/bin/env python3
"""Base commune pour les popups Waybar (style menu luminosité).

Fournit une fenêtre GTK3 sur le layer overlay (gtk-layer-shell), ancrée en
haut à droite, avec :
  - fermeture par Échap / Entrée / clic en dehors / bouton croix (✕)
  - matériau translucide flouté par le compositeur, palette système macOS
  - un en-tête (titre + croix) déjà construit dans self.box
"""

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

        # Layer overlay (au-dessus de Waybar) + clavier exclusif.
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, margin_top)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, margin_right)

        # Namespace dédié : c'est lui que cible le bloc `layerrule`
        # waybar-popup dans hyprland.conf. Sans namespace propre on ne
        # pourrait viser que « gtk-layer-shell », ce qui engloberait la
        # fenêtre de fermeture plein écran ci-dessous — et flouterait donc
        # tout l'écran dès l'ouverture d'un menu.
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

        # Dismiss layer : fenêtre plein écran transparente, ferme au clic dehors.
        self._dismiss = Gtk.Window()
        GtkLayerShell.init_for_window(self._dismiss)
        GtkLayerShell.set_layer(self._dismiss, GtkLayerShell.Layer.TOP)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self._dismiss, edge, True)
        self._dismiss.set_app_paintable(True)
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self._dismiss.set_visual(visual)
        self._dismiss.connect(
            "draw",
            lambda w, cr: (cr.set_source_rgba(0, 0, 0, 0),
                           cr.set_operator(1), cr.paint(), False)[-1],
        )
        eb = Gtk.EventBox()
        eb.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        eb.connect("button-press-event", lambda *_: (self.close(), True)[1])
        self._dismiss.add(eb)
        self.connect("destroy", lambda *_: self._dismiss.destroy())

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
        self._dismiss.show_all()
        self.show_all()
        self._grab_first_focus()
        Gtk.main()
