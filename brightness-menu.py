#!/usr/bin/env python3
"""Popup luminosité + température couleur (hyprsunset) pour Waybar.

Ce popup dupliquait toute la mécanique de menu_common — layer-shell, visual
RGBA, fermeture au clic dehors, feuille de style — dans une copie qui avait
divergé depuis. Il en hérite désormais, ce qui l'aligne d'office sur les
autres menus et retire cent cinquante lignes qu'il fallait maintenir en
double.
"""

import subprocess
import os
import signal
import re
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

TEMP_FILE = os.path.expanduser("~/.cache/hyprsunset-temp")
KBD_DEVICE = "tpacpi::kbd_backlight"
KITTY_CONF = os.path.expanduser("~/.config/kitty/kitty.conf")
CHROME_OPACITY_CONF = os.path.expanduser("~/.config/hypr/chrome-opacity.lua")
PIP_OPACITY_CONF = os.path.expanduser("~/.config/hypr/pip-opacity.lua")

PIP_RULE_TEMPLATE = """\
-- Opacite PiP — geree par le slider du menu luminosite Waybar.
-- Ne pas editer a la main : la valeur opacity est reecrite par brightness-menu.py.
hl.window_rule({{
    name  = "pip-opacity",
    match = {{ title = "(?i)picture.in.picture" }},

    opacity = "{value} {value}",
}})
"""

CHROME_RULE_ON = """\
-- Transparence Chrome — gere par le menu luminosite Waybar (switch on/off).
-- Etat : ACTIF
hl.window_rule({
    name  = "chrome-opacity",
    match = { class = "google-chrome" },

    opacity = "0.95 0.90",
})
"""

CHROME_RULE_OFF = """\
-- Transparence Chrome — gere par le menu luminosite Waybar (switch on/off).
-- Etat : INACTIF
"""


KBD_LABELS = ["Éteint", "Faible", "Fort"]

# Glyphes de la colonne d'icônes (Material Design de la Nerd Font).
IC_SCREEN = "\U000f00e0"     # soleil
IC_KEYBOARD = "\U000f030c"   # clavier
IC_NIGHT = "\U000f0594"      # lune
IC_TEMP = "\U000f050f"       # thermomètre
IC_TERM = "\U000f018d"       # console
IC_PIP = "\U000f0567"        # vidéo
IC_CHROME = "\U000f02af"     # navigateur
IC_DARK = "\U000f0821"       # bascule clair / sombre


class BrightnessPopup(LayerPopup):
    """Quatre cartes : l'écran, la lumière du soir, la transparence, le thème.

    Sept réglages se suivaient auparavant en une seule colonne de libellés et
    de curseurs, sans que rien ne dise que l'opacité de Kitty et celle du PiP
    règlent la même chose, ni que le curseur de température ne sert à rien
    tant que son interrupteur est éteint.
    """

    def __init__(self):
        super().__init__("Luminosité & Couleur", width=340, margin_right=150)

        # Debounces (hyprsunset, kitty.conf, windowrule PiP) : chaque réglage
        # écrit dans un fichier ou relance un démon, hors de question de le
        # faire à chaque pixel de glissement.
        self._temp_timeout_id = None
        self._opacity_timeout_id = None
        self._pip_opacity_timeout_id = None

        # ── Écran ──
        screen = self.add_card("Écran")

        bright = self._get_brightness()
        self.scale_bright = self._make_scale(1, 100, 5, bright)
        _r, self.lbl_bright = screen.slider(
            IC_SCREEN, "Luminosité", self.scale_bright, "%d %%" % bright)
        self.scale_bright.connect("value-changed", self._on_brightness_changed)

        kbd = self._get_kbd_brightness()
        self.scale_kbd = self._make_scale(0, 2, 1, kbd)
        self.scale_kbd.add_mark(0, Gtk.PositionType.BOTTOM, "Éteint")
        self.scale_kbd.add_mark(2, Gtk.PositionType.BOTTOM, "Fort")
        _r, self.lbl_kbd = screen.slider(
            IC_KEYBOARD, "Clavier", self.scale_kbd, KBD_LABELS[min(kbd, 2)])
        self.scale_kbd.connect("value-changed", self._on_kbd_changed)

        # ── Lumière du soir ──
        # L'interrupteur et le curseur qu'il commande sont dans la même carte :
        # le curseur grisé se lit alors comme « éteint », pas comme « cassé ».
        night = self.add_card("Lumière du soir")
        current_temp = self._get_temperature()
        self.switch_temp = night.toggle(IC_NIGHT, "Filtre chaud",
                                        current_temp is not None,
                                        self._on_switch_toggled)

        self.scale_temp = self._make_scale(2500, 6500, 100,
                                           current_temp or 4500)
        # Inversé : glisser vers la droite réchauffe l'écran, ce qui est le sens
        # du geste attendu — « plus de filtre ». Les marques suivent
        # l'inversion, d'où Froid à gauche et Chaud à droite.
        self.scale_temp.set_inverted(True)
        self.scale_temp.add_mark(2500, Gtk.PositionType.BOTTOM, "Chaud")
        self.scale_temp.add_mark(6500, Gtk.PositionType.BOTTOM, "Froid")
        self.scale_temp.set_sensitive(current_temp is not None)
        _r, self.lbl_temp = night.slider(
            IC_TEMP, "Température", self.scale_temp,
            "%d K" % (current_temp or 4500))
        self.scale_temp.connect("value-changed", self._on_temperature_changed)

        # ── Transparence ──
        transp = self.add_card("Transparence")

        kitty = self._get_kitty_opacity()
        self.scale_opacity = self._make_scale(30, 100, 5, kitty)
        _r, self.lbl_opacity = transp.slider(
            IC_TERM, "Kitty", self.scale_opacity, "%d %%" % kitty)
        self.scale_opacity.connect("value-changed",
                                   self._on_kitty_opacity_changed)

        pip = self._get_pip_opacity()
        self.scale_pip = self._make_scale(20, 100, 5, pip)
        _r, self.lbl_pip = transp.slider(
            IC_PIP, "Picture-in-picture", self.scale_pip, "%d %%" % pip)
        self.scale_pip.connect("value-changed", self._on_pip_opacity_changed)

        self.switch_chrome = transp.toggle(IC_CHROME, "Chrome",
                                           self._is_chrome_transparent(),
                                           self._on_chrome_toggled)

        # ── Apparence ──
        appearance = self.add_card("Apparence")
        self.switch_dark = appearance.toggle(IC_DARK, "Mode sombre",
                                             self._is_dark_mode(),
                                             self._on_dark_toggled)

    @staticmethod
    def _make_scale(lo, hi, step, value):
        """Curseur sans valeur incrustée : elle vit dans le libellé de la ligne."""
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                         lo, hi, step)
        scale.set_value(value)
        scale.set_draw_value(False)
        return scale

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
        self.lbl_opacity.set_text("%d %%" % int(scale.get_value()))
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
        """Lit la valeur opacity dans pip-opacity.lua, renvoie un pourcentage.

        La regle est en Lua depuis la migration du 2026-09-02, donc la valeur
        est une chaine entre guillemets : `opacity = "0.86 0.86",`.
        """
        try:
            with open(PIP_OPACITY_CONF) as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("opacity") and "=" in s:
                        val = s.split("=", 1)[1].strip().strip(',').strip('"')
                        return int(round(float(val.split()[0]) * 100))
        except Exception:
            pass
        return 60

    def _on_pip_opacity_changed(self, scale):
        # Debounce: éviter de réécrire le fichier + reload à chaque pixel de drag
        if self._pip_opacity_timeout_id:
            GLib.source_remove(self._pip_opacity_timeout_id)
        self.lbl_pip.set_text("%d %%" % int(scale.get_value()))
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
                rf'\g<1>= "{value} {value}",',
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
        """Actif si le fichier de regle contient une ligne opacity non commentee.

        En Lua un commentaire commence par `--`, donc une ligne desactivee ne
        commence jamais par « opacity » : le test reste valable tel quel.
        """
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
        self.lbl_kbd.set_text(KBD_LABELS[min(val, 2)])
        subprocess.Popen(
            ["brightnessctl", "--device", KBD_DEVICE, "set", str(val), "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_brightness_changed(self, scale):
        val = int(scale.get_value())
        self.lbl_bright.set_text("%d %%" % val)
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
        self.lbl_temp.set_text("%d K" % int(scale.get_value()))
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
    BrightnessPopup().run()


if __name__ == "__main__":
    main()
