#!/usr/bin/env python3
"""Popup Média pour Waybar (style menu son / notifications).

Ce que le morceau est, et les trois gestes qu'on veut sur lui : précédent,
lecture/pause, suivant.

Il remplace le play/pause posé sur le clic gauche du module. Ce clic-là avait
deux défauts : rien dans la barre n'annonçait qu'il existait — pas plus que la
molette ou le clic du milieu — et il mettait en pause la cible qu'on venait de
viser, si bien que le geste suivant se faisait à l'aveugle.

Le lecteur commandé est celui que le popup affiche : on cherche le premier qui
joue, à défaut le premier en pause, et toutes les actions partent vers lui
nommément. Passer par `playerctld` aurait fait suivre « le dernier lecteur
actif », c'est-à-dire potentiellement un autre que celui dont on lit le titre.
"""
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup, run_popup  # noqa: E402

DEVNULL = subprocess.DEVNULL

# Rythme de rafraîchissement. Le popup ne montre ni progression ni pochette :
# il n'a à suivre qu'un changement de piste ou de statut, y compris ceux
# déclenchés depuis Spotify lui-même pendant que le menu est ouvert.
REFRESH_MS = 1000


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""


def active_player():
    """Nom et statut du lecteur à commander, ou (None, None) s'il n'y en a pas.

    `playerctl -l` et `playerctl -a status` rendent leurs lignes dans le même
    ordre : les apparier donne le statut de chaque lecteur sans un appel par
    lecteur. Celui qui joue l'emporte sur celui qui est en pause — c'est le
    même arbitrage que fait la barre.
    """
    names = run(["playerctl", "-l"]).split()
    stats = run(["playerctl", "-a", "status"]).split()
    pairs = list(zip(names, stats))
    for want in ("Playing", "Paused"):
        for name, status in pairs:
            if status == want:
                return name, status
    return (pairs[0] if pairs else (None, None))


def metadata(player):
    """(titre, artiste) du lecteur, chaînes vides si l'information manque."""
    if not player:
        return "", ""
    out = run(["playerctl", "-p", player, "metadata", "--format",
               "{{title}}\n{{artist}}"]).split("\n")
    out += ["", ""]
    return out[0].strip(), out[1].strip()


class MediaPopup(LayerPopup):
    """Une seule carte : le morceau, puis la rangée de transport.

    Les trois boutons vivent sur une ligne à part plutôt qu'en accessoires de
    la ligne du titre : ils commandent le morceau, ils ne le décrivent pas, et
    une cible tactile de 8 px de padding ne tient pas dans une colonne de
    valeur.
    """

    IC_PREV = "\U000f04ae"    # piste précédente
    IC_NEXT = "\U000f04ad"    # piste suivante
    IC_PLAY = "\U000f040a"    # lecture
    IC_PAUSE = "\U000f03e4"   # pause
    IC_NONE = "\U000f075a"    # note barrée : plus rien à commander

    # Mêmes glyphes que les `player-icons` du module dans config-full : le
    # popup et la barre doivent désigner le lecteur du même trait.
    PLAYER_ICONS = {
        "spotify": "\U000f04c7",
        "chromium": "\U000f02af",
        "chrome": "\U000f02af",
        "firefox": "\U000f0239",
    }
    IC_DEFAULT = "\U000f0387"  # note de musique

    # Le module média siège à gauche de la barre, après le nom de l'app. La
    # fenêtre étant ancrée à droite, la marge la ramène sous lui : 1280 px
    # logiques - 320 de large - 382 où commence le module.
    MARGIN_RIGHT = 578

    def __init__(self):
        super().__init__("Média", width=320, margin_right=self.MARGIN_RIGHT)

        self.player, status = active_player()
        title, artist = metadata(self.player)

        card = self.add_card()
        self.now = card.info(self._player_icon(), title or "Aucune lecture",
                             subtitle=artist or None)

        self.btn_prev = self._transport(self.IC_PREV, "previous",
                                        "Piste précédente")
        self.btn_toggle = self._transport(self.IC_PAUSE, "play-pause",
                                          "Lecture / pause", accent=True)
        self.btn_next = self._transport(self.IC_NEXT, "next", "Piste suivante")

        row = Gtk.Box(spacing=8, homogeneous=True)
        for btn in (self.btn_prev, self.btn_toggle, self.btn_next):
            row.pack_start(btn, True, True, 0)
        card.custom(row)

        self._apply(status, title, artist)
        self._tick = GLib.timeout_add(REFRESH_MS, self._refresh)
        self.connect("destroy", self._stop_refresh)

    # ---- Construction ----

    def _transport(self, glyph, command, tooltip, accent=False):
        btn = Gtk.Button(label=glyph)
        btn.set_tooltip_text(tooltip)
        ctx = btn.get_style_context()
        if accent:
            ctx.add_class("accent")
        btn.connect("clicked", lambda _b, c=command: self._send(c))
        return btn

    def _player_icon(self):
        if not self.player:
            return self.IC_NONE
        key = self.player.split(".")[0].lower()
        return self.PLAYER_ICONS.get(key, self.IC_DEFAULT)

    # ---- Actions ----

    def _send(self, command):
        """Envoie une commande au lecteur, sans refermer le popup.

        Enchaîner deux pistes est un geste courant : refermer à chaque clic,
        comme le font les menus qui lancent une application, obligerait à
        rouvrir le menu entre chaque.
        """
        if not self.player:
            return
        subprocess.run(["playerctl", "-p", self.player, command],
                       stdout=DEVNULL, stderr=DEVNULL)
        # Spotify met un instant à publier le nouveau titre sur D-Bus ; le
        # tick périodique rattrape ce que cette relecture immédiate manque.
        GLib.timeout_add(250, self._refresh_once)

    # ---- Rafraîchissement ----

    def _refresh_once(self):
        self._refresh()
        return False

    def _refresh(self):
        player, status = active_player()
        if player != self.player:
            self.player = player
            if self.now.icon_label is not None:
                self.now.icon_label.set_text(self._player_icon())
        title, artist = metadata(player)
        self._apply(status, title, artist)
        return True

    def _apply(self, status, title, artist):
        """Reflète l'état courant sur les widgets déjà construits.

        Mettre à jour les labels plutôt que reconstruire la carte : une
        reconstruction toutes les secondes ferait perdre le focus clavier et
        clignoter la fenêtre.
        """
        playing = status == "Playing"
        self.now.title_label.set_text(title or "Aucune lecture")

        sub = self.now.subtitle_label
        sub.set_text(artist)
        sub.set_visible(bool(artist))

        self.btn_toggle.set_label(self.IC_PAUSE if playing else self.IC_PLAY)
        for btn in (self.btn_prev, self.btn_toggle, self.btn_next):
            btn.set_sensitive(bool(self.player))

    def _stop_refresh(self, *_):
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0


def main():
    run_popup(MediaPopup, "waybar-media-menu")


if __name__ == "__main__":
    main()
