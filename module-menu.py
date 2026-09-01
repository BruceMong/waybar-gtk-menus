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
/* Même pile que la barre : Inter (ou Adwaita Sans, son dérivé déjà présent)
   pour le texte, Nerd Font en queue pour les glyphes. Sans cette règle la
   fenêtre hérite du gtk-font-name système, ici une monospace. */
window, label, button, entry, switch, scale, list, row {
    font-family: "Inter", "Adwaita Sans", "SF Pro Text",
                 "Material Symbols Rounded",
                 "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font",
                 "Symbols Nerd Font", "Noto Sans Symbols 2";
}
window { background-color: rgba(30, 30, 32, 0.72); border-radius: 12px; }
.title { font-size: 15px; font-weight: bold; color: #ebebf0; margin: 4px 2px 10px 2px; }
.mod-row { padding: 7px 4px; }
.mod-name { font-size: 14px; color: #ebebf0; }
switch { min-width: 48px; min-height: 26px; }
.close-btn {
    background: transparent;
    border: none;
    color: #ebebf0;
    font-size: 18px;
    font-weight: bold;
    padding: 0 8px;
    min-height: 24px;
    min-width: 24px;
}
.close-btn:hover { background-color: rgba(255, 69, 58, 0.18); color: #ff453a; border-radius: 6px; }
.quick-btn {
    font-size: 13px;
    padding: 8px;
    border-radius: 8px;
    background-color: rgba(255, 255, 255, 0.09);
    color: #ebebf0;
    border: none;
}
.quick-btn:hover { background-color: rgba(255, 255, 255, 0.16); }
.quick-btn.hide:hover { background-color: rgba(255, 214, 10, 0.22); color: #ffd60a; }
.quick-btn.show:hover { background-color: rgba(50, 215, 75, 0.22); color: #32d74b; }
.quick-btn.stealth:hover { background-color: rgba(10, 132, 255, 0.22); color: #0a84ff; }
switch:focus, button:focus, .mod-row:focus, *:focus {
    outline: 2px solid rgba(10, 132, 255, 0.75);
    outline-offset: -2px;
}
""".encode()


class ModuleWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Modules waybar")

        # Fenêtre Hyprland ordinaire (pas un layer) : c'est la windowrule
        # waybar-modules-float qui la place. En GTK4 la translucidité vient du
        # CSS (`window { background-color: rgba(...) }`) : pas de visual RGBA à
        # poser à la main comme en GTK3. Hyprland floute alors la surface non
        # opaque — même matériau que les popups de la barre.

        self.set_default_size(300, 720)

        self.switches = []  # (switch, [ids])

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        self.set_child(outer)

        # En-tête : titre + croix de fermeture.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Modules waybar")
        title.add_css_class("title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        header.append(title)

        close = Gtk.Button(label="✕")
        close.add_css_class("close-btn")
        close.set_valign(Gtk.Align.START)
        close.connect("clicked", lambda _b: self.close())
        header.append(close)
        outer.append(header)

        # Boutons d'accès rapide.
        quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        quick.set_margin_bottom(8)

        btn_hide = Gtk.Button(label="Cacher CPU·Temp·RAM·Wifi·Date·Fenêtre")
        btn_hide.add_css_class("quick-btn")
        btn_hide.add_css_class("hide")
        btn_hide.set_hexpand(True)
        btn_hide.connect("clicked", self.on_quick_hide)
        quick.append(btn_hide)

        btn_show = Gtk.Button(label="Tout afficher")
        btn_show.add_css_class("quick-btn")
        btn_show.add_css_class("show")
        btn_show.connect("clicked", self.on_show_all)
        quick.append(btn_show)

        outer.append(quick)

        # Mode discret : masque toute la barre sauf l'œil (clic sur l'œil = ressortir).
        btn_stealth = Gtk.Button(label="󰈉  Masquer la barre (mode discret)")
        btn_stealth.add_css_class("quick-btn")
        btn_stealth.add_css_class("stealth")
        btn_stealth.set_margin_bottom(8)
        btn_stealth.connect("clicked", self.on_stealth)
        outer.append(btn_stealth)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.append(scrolled)

        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled.set_child(listbox)

        hidden = load_hidden()
        folded = load_folded()
        for label, ids in MODULES:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("mod-row")

            # Un module replié par autofit reste « visible » ici (le choix de
            # l'utilisateur est intact) mais n'est pas dans la barre : sans
            # cette mention, l'interrupteur semble mentir.
            if any(i in folded for i in ids):
                label += "  (replié — pas la place)"

            name = Gtk.Label(label=label)
            name.add_css_class("mod-name")
            name.set_xalign(0.0)
            name.set_hexpand(True)
            row.append(name)

            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(not any(i in hidden for i in ids))  # actif = visible
            handler = sw.connect("state-set", self.on_toggle, ids)
            row.append(sw)

            self.switches.append((sw, ids, handler))
            listbox.append(row)

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
