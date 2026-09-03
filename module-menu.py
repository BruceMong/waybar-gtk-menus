#!/usr/bin/env python3
"""Fenêtre flottante de visibilité des modules de la waybar.

Un interrupteur par module : basculer applique le changement IMMÉDIATEMENT
(régénère config-active + recharge waybar via SIGUSR2). Pas de bouton OK.
La fenêtre reste ouverte ; flottante/au-dessus via une windowrule Hyprland
(match:class = waybar.modules).
"""
import os
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, Gdk, GLib  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
ACTIVE = os.path.join(CONFIG_DIR, "config-active")
LINK = os.path.join(CONFIG_DIR, "config")
GEN = os.path.join(CONFIG_DIR, "generate-config.py")
STEALTH = os.path.join(CONFIG_DIR, "stealth.sh")

APP_ID = "waybar.modules"

# (label, [ids de module waybar]). Workspaces et l'œil restent toujours visibles.
MODULES = [
    ("Bouton +10", ["custom/ws-tens"]),
    ("Chrome", ["custom/chrome"]),
    ("Fenêtre", ["hyprland/window"]),
    ("Média", ["mpris"]),
    ("Heure", ["clock#time"]),
    ("Date", ["clock#date"]),
    ("Confidentialité", ["privacy"]),
    ("Tray", ["tray"]),
    ("Notifications", ["custom/dnd"]),
    ("CPU", ["cpu"]),
    ("Température", ["temperature"]),
    ("RAM", ["memory"]),
    ("Disque", ["disk"]),
    ("Services en échec", ["systemd-failed-units"]),
    ("Mises à jour", ["custom/updates"]),
    ("Réseau", ["network"]),
    ("Bluetooth", ["bluetooth"]),
    ("Son", ["pulseaudio#icon", "pulseaudio#percentage"]),
    ("Luminosité", ["backlight"]),
    ("Caféine", ["idle_inhibitor"]),
    ("Profil énergie", ["power-profiles-daemon"]),
    ("Batterie", ["battery"]),
    ("Power", ["custom/power"]),
]

# Modules cachés d'un coup par le bouton « accès rapide ».
QUICK_HIDE = ["cpu", "temperature", "memory", "network", "clock#date",
              "hyprland/window"]


def load_hidden():
    try:
        with open(HIDDEN_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def load_folded():
    """Modules repliés automatiquement par autofit.py (barre trop étroite)."""
    try:
        with open(os.path.join(CONFIG_DIR, "modules-hidden-auto"),
                  encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def save_hidden(hidden):
    with open(HIDDEN_FILE, "w", encoding="utf-8") as f:
        for module in sorted(hidden):
            f.write(module + "\n")


def ensure_symlink():
    try:
        if not (os.path.islink(LINK) and os.readlink(LINK) == ACTIVE):
            if os.path.lexists(LINK):
                os.remove(LINK)
            os.symlink(ACTIVE, LINK)
    except OSError:
        pass


def apply():
    """Régénère la config active et recharge waybar."""
    subprocess.run(["python3", GEN], check=False)
    ensure_symlink()
    subprocess.run(["pkill", "-SIGUSR2", "waybar"], check=False)


CSS = """
/* Même vocabulaire que les popups de la barre (menu_common.py), transposé en
   GTK4 : cette fenêtre n'est pas un layer-shell et ne peut donc pas partager
   la feuille commune, mais elle partage ses matériaux — sans quoi elle serait
   la seule surface du système à ne pas ressembler aux autres.

   Les valeurs sont volontairement identiques : aplat de carte à 6,5 %, rayon
   de 10 px, lignes de 42 px, filets en retrait de 44 px. */
window, label, button, entry, switch, scale, list, row {
    font-family: "SF Pro Text", "Inter", "Adwaita Sans",
                 "Material Symbols Rounded",
                 "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font",
                 "Symbols Nerd Font", "Noto Sans Symbols 2";
    font-size: 13px;
}
window {
    background-color: rgba(28, 28, 30, 0.74);
    color: #ebebf0;
    border-radius: 14px;
}
label { color: #ebebf0; }

.popup-title { font-size: 15px; font-weight: 600; color: #f5f5f7; }
.section-title {
    font-size: 12px;
    font-weight: 600;
    color: rgba(235, 235, 245, 0.62);
    margin-left: 4px;
}

.card { background-color: rgba(255, 255, 255, 0.065); border-radius: 10px; }
.card separator {
    background-color: rgba(255, 255, 255, 0.075);
    background-image: none;
    min-height: 1px;
    margin-left: 44px;
}
/* Carte sans colonne d'icônes (la liste des modules) : le filet se recale sur
   le texte, sinon il démarre au milieu d'un blanc. */
.card.flush separator { margin-left: 12px; }
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
.row.static:hover { background-color: transparent; }

.row-icon { font-size: 17px; color: rgba(235, 235, 245, 0.88); }
.row-label { color: #f0f0f5; }
.row-sub { font-size: 11px; color: rgba(235, 235, 245, 0.52); }

/* Un module replié par autofit n'est pas caché par choix : la mention est une
   précision sur l'état, pas un second libellé. */
.row-note { font-size: 11px; color: rgba(235, 235, 245, 0.42); }

/* Comme dans les popups, le thème gonfle l'interrupteur si on ne remet pas
   ses marges à zéro : 48 x 36 au lieu de 44 x 24. */
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
switch:checked { background-color: #0a84ff; }
switch slider {
    background-color: #ffffff;
    margin: 0;
    border: none;
    border-radius: 50%;
    min-width: 20px;
    min-height: 20px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}

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
}
button.close-btn:hover {
    color: #ffffff;
    background-color: rgba(255, 255, 255, 0.16);
}

/* Les trois actions de tête teintent leur survol : elles ne règlent pas un
   module mais toute la barre, et le geste n'est pas anodin. */
button.row.hide:hover    { background-color: rgba(255, 214, 10, 0.18); }
button.row.show:hover    { background-color: rgba(50, 215, 75, 0.18); }
button.row.stealth:hover { background-color: rgba(10, 132, 255, 0.20); }

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

switch:focus, button:focus, .row:focus, *:focus {
    outline: 2px solid rgba(10, 132, 255, 0.75);
    outline-offset: -2px;
}
""".encode()


# ── Cartes et lignes (transposition GTK4 de menu_common) ──────────────────

ICON_WIDTH = 22
ROW_SPACING = 10


def _icon_slot(icon):
    """Colonne d'icône de largeur fixe.

    Même ruse qu'en GTK3 : un GtkOverlay prend la taille de son enfant
    principal et ignore celle de ses calques, si bien qu'un glyphe plus large
    que 22 px déborde sans décaler le libellé de sa ligne.
    """
    lbl = Gtk.Label(label=icon or "")
    lbl.add_css_class("row-icon")
    lbl.set_halign(Gtk.Align.CENTER)
    lbl.set_valign(Gtk.Align.CENTER)
    gauge = Gtk.Box()
    gauge.set_size_request(ICON_WIDTH, -1)
    slot = Gtk.Overlay()
    slot.set_child(gauge)
    slot.add_overlay(lbl)
    slot.set_valign(Gtk.Align.CENTER)
    return slot


class Card(Gtk.Box):
    """Groupe de lignes apparentées, filets en retrait."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self._rows = 0

    def add_row(self, widget):
        if self._rows:
            self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self.append(widget)
        self._rows += 1
        return widget

    def action(self, icon, title, css=None, on_click=None):
        btn = Gtk.Button()
        btn.add_css_class("row")
        btn.set_has_frame(False)
        if css:
            btn.add_css_class(css)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=ROW_SPACING)
        body.append(_icon_slot(icon))
        lbl = Gtk.Label(label=title, xalign=0.0)
        lbl.add_css_class("row-label")
        lbl.set_ellipsize(3)          # Pango.EllipsizeMode.END
        lbl.set_hexpand(True)
        body.append(lbl)
        btn.set_child(body)
        if on_click is not None:
            btn.connect("clicked", on_click)
        return self.add_row(btn)

    def toggle(self, title, active, note=None):
        """Ligne « nom du module + interrupteur ». Renvoie l'interrupteur."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=ROW_SPACING)
        row.add_css_class("row")
        row.add_css_class("static")

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        lbl = Gtk.Label(label=title, xalign=0.0)
        lbl.add_css_class("row-label")
        texts.append(lbl)
        if note:
            sub = Gtk.Label(label=note, xalign=0.0)
            sub.add_css_class("row-note")
            texts.append(sub)
        row.append(texts)

        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(active)
        row.append(sw)
        self.add_row(row)
        return sw


class ModuleWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Modules waybar")

        # Fenêtre Hyprland ordinaire (pas un layer) : c'est la windowrule
        # waybar-modules-float qui la place. En GTK4 la translucidité vient du
        # CSS (`window { background-color: rgba(...) }`) : pas de visual RGBA à
        # poser à la main comme en GTK3. Hyprland floute alors la surface non
        # opaque — même matériau que les popups de la barre.

        self.set_default_size(320, 720)

        self.switches = []  # (switch, [ids], handler)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        self.set_child(outer)

        # En-tête : titre + croix de fermeture.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(4)
        header.set_margin_end(2)
        title = Gtk.Label(label="Modules waybar")
        title.add_css_class("popup-title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        header.append(title)

        close = Gtk.Button(label="\u2715")
        close.add_css_class("close-btn")
        close.set_has_frame(False)
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: self.close())
        header.append(close)
        outer.append(header)

        # -- Actions de tête --
        # Trois boutons de formes et de largeurs différentes se disputaient le
        # haut de la fenêtre. En lignes d'une même carte, on voit ce qu'ils ont
        # en commun : ils agissent sur la barre entière, pas sur un module.
        quick = Card()
        quick.action("\U000f0209", "Cacher CPU · Temp · RAM · Wifi · Date · Fenêtre",
                     css="hide", on_click=self.on_quick_hide)
        quick.action("\U000f0208", "Tout afficher",
                     css="show", on_click=self.on_show_all)
        quick.action("\U000f05f9", "Masquer la barre (mode discret)",
                     css="stealth", on_click=self.on_stealth)
        outer.append(quick)

        section = Gtk.Label(label="Modules", xalign=0.0)
        section.add_css_class("section-title")
        outer.append(section)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.append(scrolled)

        card = Card()
        card.add_css_class("flush")     # ces lignes-là n'ont pas d'icône
        scrolled.set_child(card)

        hidden = load_hidden()
        folded = load_folded()
        for label, ids in MODULES:
            # Un module replié par autofit reste « visible » ici (le choix de
            # l'utilisateur est intact) mais n'est pas dans la barre : sans
            # cette mention, l'interrupteur semble mentir.
            note = ("replié — pas la place"
                    if any(i in folded for i in ids) else None)
            sw = card.toggle(label, not any(i in hidden for i in ids), note)
            handler = sw.connect("state-set", self.on_toggle, ids)
            self.switches.append((sw, ids, handler))

        # Échap ferme la fenêtre.
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

        # Fermeture au clic en dehors : quand la fenêtre perd le focus
        # toplevel, on ferme — mais après un délai de grâce. Avec
        # focus_follows_mouse (Hyprland), le trajet souris œil -> menu survole
        # d'autres fenêtres qui volent le focus ; sans délai le menu se
        # fermerait avant d'être atteint. Le délai est annulé si le focus
        # revient ou si le pointeur entre dans la fenêtre. Le garde
        # _was_active évite de fermer pendant la phase d'ouverture.
        self._was_active = False
        self._pointer_inside = False
        self._close_timeout = None
        self.connect("notify::is-active", self.on_active_changed)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self.on_pointer_enter)
        motion.connect("leave", self.on_pointer_leave)
        self.add_controller(motion)

    def on_pointer_enter(self, *_):
        self._pointer_inside = True
        self._cancel_close()

    def on_pointer_leave(self, *_):
        self._pointer_inside = False
        if self._was_active and not self.is_active():
            self._schedule_close()

    def on_active_changed(self, *_):
        if self.is_active():
            self._was_active = True
            self._cancel_close()
        elif self._was_active and not self._pointer_inside:
            self._schedule_close()

    def _schedule_close(self):
        if self._close_timeout is None:
            self._close_timeout = GLib.timeout_add(800, self._close_if_inactive)

    def _cancel_close(self):
        if self._close_timeout is not None:
            GLib.source_remove(self._close_timeout)
            self._close_timeout = None

    def _close_if_inactive(self):
        self._close_timeout = None
        if not self.is_active() and not self._pointer_inside:
            self.close()
        return False

    def on_toggle(self, switch, state, ids):
        hidden = load_hidden()
        if state:                       # visible -> retire les ids
            hidden.difference_update(ids)
        else:                           # caché -> ajoute les ids
            hidden.update(ids)
        save_hidden(hidden)
        apply()
        return False                    # laisse le switch refléter l'état

    def on_stealth(self, _btn):
        # stealth.sh régénère la config et recharge waybar lui-même.
        subprocess.run([STEALTH, "on"], check=False)
        self.close()

    def on_quick_hide(self, _btn):
        hidden = load_hidden()
        hidden.update(QUICK_HIDE)
        save_hidden(hidden)
        apply()
        self.refresh()

    def on_show_all(self, _btn):
        save_hidden(set())
        apply()
        self.refresh()

    def on_key(self, _ctrl, keyval, _code, _mods):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        # Flèches haut/bas : naviguer entre les interrupteurs et boutons.
        # (Espace / Entrée bascule l'interrupteur ciblé, géré nativement.)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            direction = (Gtk.DirectionType.TAB_BACKWARD if keyval == Gdk.KEY_Up
                         else Gtk.DirectionType.TAB_FORWARD)
            self.child_focus(direction)
            return True
        return False

    def refresh(self):
        hidden = load_hidden()
        for sw, ids, handler in self.switches:
            sw.handler_block(handler)
            sw.set_active(not any(i in hidden for i in ids))
            sw.handler_unblock(handler)


class ModuleApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.win is None:
            self.win = ModuleWindow(self)
        else:
            self.win.refresh()
        self.win.present()
        if self.win.switches:
            self.win.switches[0][0].grab_focus()


if __name__ == "__main__":
    ModuleApp().run(None)
