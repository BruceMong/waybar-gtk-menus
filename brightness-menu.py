#!/usr/bin/env python3
"""Popup luminosité + température couleur (hyprsunset) pour Waybar."""

import subprocess
import os
import signal
import re
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

TEMP_FILE = os.path.expanduser("~/.cache/hyprsunset-temp")
KBD_DEVICE = "tpacpi::kbd_backlight"
KITTY_CONF = os.path.expanduser("~/.config/kitty/kitty.conf")
CHROME_OPACITY_CONF = os.path.expanduser("~/.config/hypr/chrome-opacity.conf")
PIP_OPACITY_CONF = os.path.expanduser("~/.config/hypr/pip-opacity.conf")

PIP_RULE_TEMPLATE = """\
# Opacite PiP — geree par le slider du menu luminosite Waybar.
# Ne pas editer a la main : la valeur opacity est reecrite par brightness-menu.py.
windowrule {{
    name = pip-opacity
    match:title = (?i)picture.in.picture

    opacity = {value} {value}
}}
"""

CHROME_RULE_ON = """\
# Transparence Chrome — gere par le menu luminosite Waybar (switch on/off).
# Etat : ACTIF
windowrule {
    name = chrome-opacity
    match:class = google-chrome

    opacity = 0.95 0.90
}
"""

CHROME_RULE_OFF = """\
# Transparence Chrome — gere par le menu luminosite Waybar (switch on/off).
# Etat : INACTIF
"""


class BrightnessPopup(Gtk.Window):
    def __init__(self):
        super().__init__(title="Luminosité & Couleur")

        # Utiliser gtk-layer-shell pour être sur le layer overlay (au-dessus de Waybar)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 40)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 150)

        # Namespace dédié, visé par le bloc `layerrule` waybar-popup dans
        # hyprland.conf. Le visual RGBA est ce qui permet à l'alpha du fond
        # d'exister : sans lui GTK l'aplatit sur du noir, et les coins
        # arrondis laissent des angles noirs.
        GtkLayerShell.set_namespace(self, "waybar-popup")
        _visual = Gdk.Screen.get_default().get_rgba_visual()
        if _visual is not None:
            self.set_visual(_visual)

        self.set_default_size(340, 300)
        self.set_resizable(False)

        self.connect("key-press-event", self._on_key)

        # Dismiss layer : fenêtre plein écran transparente derrière la popup,
        # ferme la popup quand on clique en dehors.
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

        self._apply_css()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)

        # -- En-tête : titre + bouton fermer --
        hbox_header = Gtk.Box(spacing=8)
        lbl_title = Gtk.Label(xalign=0)
        lbl_title.set_markup("<b>Luminosité &amp; Couleur</b>")
        hbox_header.pack_start(lbl_title, True, True, 0)

        btn_close = Gtk.Button(label="✕")
        btn_close.set_relief(Gtk.ReliefStyle.NONE)
        btn_close.set_valign(Gtk.Align.CENTER)
        btn_close.get_style_context().add_class("close-btn")
        btn_close.connect("clicked", lambda *_: self.close())
        hbox_header.pack_end(btn_close, False, False, 0)
        box.pack_start(hbox_header, False, False, 0)

        # -- Luminosité --
        lbl_bright = Gtk.Label(xalign=0)
        lbl_bright.set_markup("<b>  Luminosité</b>")
        box.pack_start(lbl_bright, False, False, 0)

        self.scale_bright = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 1, 100, 5
        )
        self.scale_bright.set_value(self._get_brightness())
        self.scale_bright.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_bright.set_digits(0)
        self.scale_bright.connect("value-changed", self._on_brightness_changed)
        box.pack_start(self.scale_bright, False, False, 0)

        # -- Rétroéclairage clavier --
        lbl_kbd = Gtk.Label(xalign=0)
        lbl_kbd.set_markup("<b>󰌌  Clavier</b>")
        box.pack_start(lbl_kbd, False, False, 0)

        self.scale_kbd = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 2, 1
        )
        self.scale_kbd.set_value(self._get_kbd_brightness())
        self.scale_kbd.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_kbd.set_digits(0)
        self.scale_kbd.add_mark(0, Gtk.PositionType.BOTTOM, "Off")
        self.scale_kbd.add_mark(1, Gtk.PositionType.BOTTOM, "Faible")
        self.scale_kbd.add_mark(2, Gtk.PositionType.BOTTOM, "Fort")
        self.scale_kbd.connect("value-changed", self._on_kbd_changed)
        box.pack_start(self.scale_kbd, False, False, 0)

        # -- Opacité Kitty --
        lbl_opacity = Gtk.Label(xalign=0)
        lbl_opacity.set_markup("<b>  Opacité Kitty</b>")
        box.pack_start(lbl_opacity, False, False, 0)

        self.scale_opacity = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 30, 100, 5
        )
        self.scale_opacity.set_value(self._get_kitty_opacity())
        self.scale_opacity.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_opacity.set_digits(0)
        self.scale_opacity.add_mark(30, Gtk.PositionType.BOTTOM, "Transparent")
        self.scale_opacity.add_mark(100, Gtk.PositionType.BOTTOM, "Opaque")
        self.scale_opacity.connect("value-changed", self._on_kitty_opacity_changed)
        box.pack_start(self.scale_opacity, False, False, 0)

        # -- Opacité PiP (windowrule Hyprland, titre "Picture in picture") --
        lbl_pip = Gtk.Label(xalign=0)
        lbl_pip.set_markup("<b>󰕧  Opacité PiP</b>")
        box.pack_start(lbl_pip, False, False, 0)

        self.scale_pip = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 20, 100, 5
        )
        self.scale_pip.set_value(self._get_pip_opacity())
        self.scale_pip.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_pip.set_digits(0)
        self.scale_pip.add_mark(20, Gtk.PositionType.BOTTOM, "Transparent")
        self.scale_pip.add_mark(100, Gtk.PositionType.BOTTOM, "Opaque")
        self.scale_pip.connect("value-changed", self._on_pip_opacity_changed)
        box.pack_start(self.scale_pip, False, False, 0)

        # -- Transparence Chrome (windowrule Hyprland) --
        hbox_chrome = Gtk.Box(spacing=8)
        lbl_chrome = Gtk.Label(xalign=0)
        lbl_chrome.set_markup("<b>  Transparence Chrome</b>")
        hbox_chrome.pack_start(lbl_chrome, True, True, 0)

        self.switch_chrome = Gtk.Switch()
        self.switch_chrome.set_valign(Gtk.Align.CENTER)
        self.switch_chrome.set_active(self._is_chrome_transparent())
        self.switch_chrome.connect("notify::active", self._on_chrome_toggled)
        hbox_chrome.pack_end(self.switch_chrome, False, False, 0)
        box.pack_start(hbox_chrome, False, False, 0)

        # -- Mode sombre (prefer-color-scheme) --
        hbox_dark = Gtk.Box(spacing=8)
        lbl_dark = Gtk.Label(xalign=0)
        lbl_dark.set_markup("<b>󰖔  Mode sombre</b>")
        hbox_dark.pack_start(lbl_dark, True, True, 0)

        self.switch_dark = Gtk.Switch()
        self.switch_dark.set_valign(Gtk.Align.CENTER)
        self.switch_dark.set_active(self._is_dark_mode())
        self.switch_dark.connect("notify::active", self._on_dark_toggled)
        hbox_dark.pack_end(self.switch_dark, False, False, 0)
        box.pack_start(hbox_dark, False, False, 0)

        # -- Température couleur --
        hbox_temp = Gtk.Box(spacing=8)
        lbl_temp = Gtk.Label(xalign=0)
        lbl_temp.set_markup("<b>  Température</b>")
        hbox_temp.pack_start(lbl_temp, True, True, 0)

        self.switch_temp = Gtk.Switch()
        self.switch_temp.set_valign(Gtk.Align.CENTER)
        hbox_temp.pack_end(self.switch_temp, False, False, 0)
        box.pack_start(hbox_temp, False, False, 0)

        self.scale_temp = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 2500, 6500, 100
        )
        self.scale_temp.set_inverted(True)  # gauche = chaud, droite = froid
        self.scale_temp.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_temp.set_digits(0)
        self.scale_temp.add_mark(2500, Gtk.PositionType.BOTTOM, "Chaud")
        self.scale_temp.add_mark(6500, Gtk.PositionType.BOTTOM, "Froid")

        current_temp = self._get_temperature()
        if current_temp is not None:
            self.switch_temp.set_active(True)
            self.scale_temp.set_value(current_temp)
            self.scale_temp.set_sensitive(True)
        else:
            self.switch_temp.set_active(False)
            self.scale_temp.set_value(4500)
            self.scale_temp.set_sensitive(False)

        self.switch_temp.connect("notify::active", self._on_switch_toggled)
        self.scale_temp.connect("value-changed", self._on_temperature_changed)
        box.pack_start(self.scale_temp, False, False, 0)

        self.add(box)

        # Debounce pour hyprsunset
        self._temp_timeout_id = None
        # Debounce pour l'opacité kitty
        self._opacity_timeout_id = None
        # Debounce pour l'opacité PiP
        self._pip_opacity_timeout_id = None

    def _apply_css(self):
        css = """
        /* Même pile que la barre : Inter (ou Adwaita Sans, son dérivé déjà présent)
           pour le texte, Nerd Font en queue pour les glyphes. Sans cette règle les
           popups héritent du gtk-font-name système, ici une monospace — ce qui suffit
           à trahir l'ensemble. */
        window, label, button, entry, switch, scale, list, row, popover, menu {
            font-family: "Inter", "Adwaita Sans", "SF Pro Text",
                         "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font",
                         "Symbols Nerd Font", "Noto Sans Symbols 2";
        }
        window {
            background-color: rgba(30, 30, 32, 0.72);
            color: #ebebf0;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.14);
        }
        label {
            color: #ebebf0;
        }
        scale trough {
            background-color: rgba(255, 255, 255, 0.09);
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
        }
        scale value {
            color: #9a9aa2;
            font-size: 12px;
        }
        scale mark label {
            color: #68686f;
            font-size: 10px;
        }
        switch {
            background-color: rgba(255, 255, 255, 0.09);
            border-radius: 12px;
            min-width: 40px;
            min-height: 20px;
        }
        switch:checked {
            background-color: #0a84ff;
        }
        switch slider {
            background-color: #ffffff;
            border-radius: 50%;
            min-width: 16px;
            min-height: 16px;
        }
        button.close-btn {
            color: #9a9aa2;
            background: none;
            border: none;
            box-shadow: none;
            padding: 0 6px;
            min-width: 24px;
            min-height: 24px;
            font-size: 14px;
        }
        button.close-btn:hover {
            color: #ff453a;
            background-color: rgba(255, 255, 255, 0.09);
            border-radius: 6px;
        }
        scale:focus, button:focus, switch:focus, *:focus {
            outline: 2px solid rgba(10, 132, 255, 0.75);
            outline-offset: -2px;
        }
        """.encode()
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_key(self, _widget, event):
        kv = event.keyval
        if kv == Gdk.KEY_Escape:
            self.close()
            return True
        # Haut/bas : passer d'un curseur à l'autre. Gauche/droite : ajuster la
        # valeur du curseur ciblé (géré nativement par Gtk.Scale).
        if kv in (Gdk.KEY_Up, Gdk.KEY_Down):
            direction = (Gtk.DirectionType.TAB_BACKWARD if kv == Gdk.KEY_Up
                         else Gtk.DirectionType.TAB_FORWARD)
            self.child_focus(direction)
            return True
        return False

    def _get_brightness(self):
        try:
            out = subprocess.check_output(
                ["brightnessctl", "-m"], text=True
            )
            return int(out.split(",")[3].strip("%"))
        except Exception:
            return 50

    def _get_temperature(self):
        """Retourne la température actuelle ou None si hyprsunset est off."""
        try:
            subprocess.check_output(["pgrep", "-x", "hyprsunset"])
            try:
                with open(TEMP_FILE) as f:
                    return int(f.read().strip())
            except Exception:
                return 4500
        except subprocess.CalledProcessError:
            return None

    def _get_kbd_brightness(self):
        try:
            out = subprocess.check_output(
                ["brightnessctl", "--device", KBD_DEVICE, "get"], text=True
            )
            return int(out.strip())
        except Exception:
            return 0

    def _get_kitty_opacity(self):
        """Lit background_opacity dans kitty.conf, renvoie un pourcentage."""
        try:
            with open(KITTY_CONF) as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("background_opacity"):
                        return int(round(float(s.split()[1]) * 100))
        except Exception:
            pass
        return 72

    def _on_kitty_opacity_changed(self, scale):
        # Debounce: éviter de réécrire le fichier à chaque pixel de drag
        if self._opacity_timeout_id:
            GLib.source_remove(self._opacity_timeout_id)
        self._opacity_timeout_id = GLib.timeout_add(
            120, self._apply_kitty_opacity, int(scale.get_value())
        )

    def _apply_kitty_opacity(self, percent):
        self._opacity_timeout_id = None
        value = f"{percent / 100:.2f}"
        try:
            with open(KITTY_CONF) as f:
                content = f.read()
            new_content, n = re.subn(
                r"(?m)^[ \t]*background_opacity\s+.*$",
                f"background_opacity {value}",
                content,
            )
            if n == 0:
                new_content = content.rstrip("\n") + f"\nbackground_opacity {value}\n"
            with open(KITTY_CONF, "w") as f:
                f.write(new_content)
        except Exception:
            return False
        # Recharge la config de toutes les instances kitty en cours (live)
        subprocess.Popen(
            ["pkill", "-USR1", "-x", "kitty"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return False

    def _get_pip_opacity(self):
        """Lit la valeur opacity dans pip-opacity.conf, renvoie un pourcentage."""
        try:
            with open(PIP_OPACITY_CONF) as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("opacity") and "=" in s:
                        val = s.split("=", 1)[1].split()[0]
                        return int(round(float(val) * 100))
        except Exception:
            pass
        return 60

    def _on_pip_opacity_changed(self, scale):
        # Debounce: éviter de réécrire le fichier + reload à chaque pixel de drag
        if self._pip_opacity_timeout_id:
            GLib.source_remove(self._pip_opacity_timeout_id)
        self._pip_opacity_timeout_id = GLib.timeout_add(
            150, self._apply_pip_opacity, int(scale.get_value())
        )

    def _apply_pip_opacity(self, percent):
        self._pip_opacity_timeout_id = None
        value = f"{percent / 100:.2f}"
        try:
            with open(PIP_OPACITY_CONF) as f:
                content = f.read()
            new_content, n = re.subn(
                r"(?m)^([ \t]*opacity\s*)=.*$",
                rf"\g<1>= {value} {value}",
                content,
            )
            if n == 0:
                new_content = PIP_RULE_TEMPLATE.format(value=value)
            with open(PIP_OPACITY_CONF, "w") as f:
                f.write(new_content)
        except FileNotFoundError:
            with open(PIP_OPACITY_CONF, "w") as f:
                f.write(PIP_RULE_TEMPLATE.format(value=value))
        except Exception:
            return False
        # Reload re-applique la windowrule d'opacite a la fenetre PiP ouverte
        subprocess.Popen(
            ["hyprctl", "reload"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return False

    def _is_chrome_transparent(self):
        """Actif si le fichier de regle contient une ligne opacity non commentee."""
        try:
            with open(CHROME_OPACITY_CONF) as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("opacity") and "=" in s:
                        return True
        except FileNotFoundError:
            pass
        return False

    def _on_chrome_toggled(self, switch, _param):
        content = CHROME_RULE_ON if switch.get_active() else CHROME_RULE_OFF
        try:
            with open(CHROME_OPACITY_CONF, "w") as f:
                f.write(content)
        except Exception:
            return
        # Reload re-applique les windowrules d'opacite aux fenetres deja ouvertes
        subprocess.Popen(
            ["hyprctl", "reload"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _is_dark_mode(self):
        try:
            out = subprocess.check_output(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                text=True,
            ).strip().strip("'")
            return out == "prefer-dark"
        except Exception:
            return True

    def _on_dark_toggled(self, switch, _param):
        scheme = "prefer-dark" if switch.get_active() else "default"
        subprocess.Popen(
            ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_kbd_changed(self, scale):
        val = int(scale.get_value())
        subprocess.Popen(
            ["brightnessctl", "--device", KBD_DEVICE, "set", str(val), "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_brightness_changed(self, scale):
        val = int(scale.get_value())
        subprocess.Popen(
            ["brightnessctl", "set", f"{val}%", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_switch_toggled(self, switch, _param):
        active = switch.get_active()
        self.scale_temp.set_sensitive(active)
        if active:
            self._apply_temperature(int(self.scale_temp.get_value()))
        else:
            subprocess.Popen(["pkill", "-x", "hyprsunset"])
            try:
                os.remove(TEMP_FILE)
            except FileNotFoundError:
                pass

    def _on_temperature_changed(self, scale):
        if not self.switch_temp.get_active():
            return
        # Debounce: attendre 150ms avant d'appliquer
        if self._temp_timeout_id:
            GLib.source_remove(self._temp_timeout_id)
        self._temp_timeout_id = GLib.timeout_add(
            150, self._apply_temperature, int(scale.get_value())
        )

    def _apply_temperature(self, temp):
        self._temp_timeout_id = None
        subprocess.run(["pkill", "-x", "hyprsunset"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "hyprsunset", "--signal", "SIGKILL"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(TEMP_FILE, "w") as f:
            f.write(str(temp))
        subprocess.Popen(
            ["hyprsunset", "-t", str(temp)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # détache hyprsunset du groupe Waybar (survit au reload SIGUSR2)
        )
        return False


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    win = BrightnessPopup()
    win.connect("destroy", Gtk.main_quit)
    # Afficher le dismiss layer en premier pour qu'il soit derrière la popup
    win._dismiss.show_all()
    win.show_all()
    win.scale_bright.grab_focus()
    Gtk.main()


if __name__ == "__main__":
    main()
