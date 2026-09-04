#!/usr/bin/env python3
"""Popup Média pour Waybar (style menu son / notifications).

Ce que le morceau est, et les trois gestes qu'on veut sur lui : précédent,
lecture/pause, suivant.

Il remplace le play/pause posé sur le clic gauche du module. Ce clic-là avait
deux défauts : rien dans la barre n'annonçait qu'il existait — pas plus que la
molette ou le clic du milieu — et il mettait en pause la cible qu'on venait de
viser, si bien que le geste suivant se faisait à l'aveugle.

Deux particularités, toutes deux dues aux navigateurs :

  - Chrome publie bien titre et artiste sur MPRIS, mais seulement une fois sa
    session média établie : tant qu'elle ne l'est pas, sa propriété Metadata
    se réduit à `mpris:length` (constaté sur le bus). Le titre de la fenêtre
    Hyprland sert alors de secours — jamais de source principale, car il donne
    l'onglet ACTIF, qui n'est pas forcément celui qui joue.

  - lancer une vidéo depuis un menu la laisse jouer dans un onglet qu'on ne
    regarde pas. Une mise en lecture depuis ce popup enchaîne donc sur le
    Picture-in-Picture, sauf s'il y a déjà une fenêtre PiP à l'écran.
    mpris-pip.sh dit lui-même quand il ne peut pas aboutir — le raccourci de
    l'extension n'atteint que l'onglet au premier plan de sa fenêtre.
"""
import json
import os
import re
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup, run_popup  # noqa: E402

DEVNULL = subprocess.DEVNULL
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
PIP_SCRIPT = os.path.join(CONFIG_DIR, "mpris-pip.sh")

# Rythme de rafraîchissement. Le popup ne montre ni progression ni pochette :
# il n'a à suivre qu'un changement de piste ou de statut, y compris ceux
# déclenchés depuis Spotify lui-même pendant que le menu est ouvert.
REFRESH_MS = 1000

# Lecteurs dont le média vit dans un onglet : ce sont eux qui gagnent à passer
# en PiP, et eux dont le titre doit être cherché sur la fenêtre.
BROWSERS = ("chromium", "chrome", "firefox", "brave", "vivaldi")

# Classe Hyprland des fenêtres où chercher un titre de vidéo. La même que
# celle visée par mpris-pip.sh : le PWA « chrome-notes… » porte sa propre
# classe et n'est pas un lecteur.
BROWSER_CLASSES = ("google-chrome", "chromium", "firefox")

# Fenêtre Picture-in-Picture, Chrome comme Firefox. Même motif que la règle
# `pip-float` de hyprland.lua — les deux désignent la même fenêtre, elles
# doivent le dire de la même façon.
PIP_TITLE = re.compile(r"picture.in.picture", re.I)

# Suffixe que le navigateur colle au titre de la fenêtre, et compteur d'onglet
# que Gmail ou YouTube posent devant.
BROWSER_SUFFIX = re.compile(r"\s*[-–]\s*(Google Chrome|Chromium|Mozilla Firefox)\s*$")
TAB_COUNTER = re.compile(r"^\(\d+\)\s*")
SITE_SUFFIX = re.compile(r"\s*[-–]\s*(YouTube|Vimeo|Twitch|Dailymotion|SoundCloud)\s*$", re.I)

# Marques de direction que YouTube enrobe autour des noms de chaîne. Invisibles
# à l'écran, mais elles comptent dans la longueur du libellé et ressortent dès
# qu'on tronque ou qu'on journalise le titre.
BIDI_MARKS = re.compile("[‎‏‪-‮⁦-⁩]")

# Nom lisible du lecteur : `playerctl -l` rend des identifiants d'instance
# (« chromium.instance1107 »), pas des noms d'application.
PLAYER_LABELS = {"chromium": "Chrome", "chrome": "Chrome",
                 "spotify": "Spotify", "firefox": "Firefox"}


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""


def players():
    """[(nom, statut)] pour chaque lecteur MPRIS, dans l'ordre de playerctl.

    `playerctl -l` et `playerctl -a status` rendent leurs lignes dans le même
    ordre : les apparier donne le statut de chacun sans un appel par lecteur.
    """
    return list(zip(run(["playerctl", "-l"]).split(),
                    run(["playerctl", "-a", "status"]).split()))


def auto_player(found):
    """Le lecteur à commander par défaut : celui qui joue l'emporte.

    C'est le même arbitrage que fait la barre. Sans lui, mettre Spotify en
    lecture pendant qu'un onglet Chrome dort en pause donnerait la main au
    mauvais des deux.
    """
    for want in ("Playing", "Paused"):
        for name, status in found:
            if status == want:
                return name
    return found[0][0] if found else None


def base_name(player):
    """« chromium.instance1107 » -> « chromium »."""
    return (player or "").split(".")[0].lower()


def is_browser(player):
    return base_name(player) in BROWSERS


def clients():
    try:
        return json.loads(run(["hyprctl", "clients", "-j"]) or "[]")
    except ValueError:
        return []


def pip_open():
    """Une fenêtre Picture-in-Picture est-elle déjà posée sur l'écran ?

    mpris-pip.sh est une bascule : l'appeler alors que le PiP est ouvert le
    referme. Une lecture lancée depuis le menu ne doit donc l'appeler que
    lorsqu'il n'y a rien à l'écran.
    """
    return any(PIP_TITLE.search(c.get("title") or "") for c in clients())


def browser_now():
    """(titre, site) de la vidéo du navigateur, lus sur la fenêtre Hyprland.

    Secours pour le seul cas où Chrome n'a pas encore publié ses métadonnées.
    Le titre de fenêtre est celui de l'onglet ACTIF : il ne dit donc pas ce qui
    joue, il dit ce qu'on regarde — les deux coïncident souvent, pas toujours.
    C'est pourquoi now_playing ne s'en sert qu'à défaut de titre MPRIS. On
    préfère la fenêtre dont le titre nomme un site de vidéo, sinon la dernière
    que le focus a visitée (`focusHistoryID` croît avec l'ancienneté).
    """
    windows = [c for c in clients()
               if (c.get("class") or "").lower() in BROWSER_CLASSES]
    if not windows:
        return "", ""
    named = [c for c in windows if SITE_SUFFIX.search(
        BROWSER_SUFFIX.sub("", c.get("title") or ""))]
    pool = named or windows
    win = min(pool, key=lambda c: c.get("focusHistoryID", 1 << 30))

    title = BIDI_MARKS.sub("", win.get("title") or "")
    title = TAB_COUNTER.sub("", BROWSER_SUFFIX.sub("", title))
    site = ""
    m = SITE_SUFFIX.search(title)
    if m:
        site = m.group(1)
        title = SITE_SUFFIX.sub("", title)
    return title.strip(), site


def now_playing(player):
    """(titre, sous-titre) du lecteur, chaînes vides si rien n'est lisible."""
    if not player:
        return "", ""
    out = run(["playerctl", "-p", player, "metadata", "--format",
               "{{title}}\n{{artist}}"]).split("\n")
    out += ["", ""]
    title, artist = out[0].strip(), out[1].strip()
    if not title and is_browser(player):
        title, artist = browser_now()
    return title, artist


class MediaPopup(LayerPopup):
    """Une carte pour le morceau et son transport, une autre pour les lecteurs.

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

        found = players()
        # Lecteur choisi à la main dans la carte du bas. Tant qu'il vit, il
        # l'emporte sur l'arbitrage automatique : sans cela, désigner l'onglet
        # Chrome pendant que Spotify joue serait défait au tick suivant.
        self._pinned = None
        self.player = auto_player(found)
        self._status = dict(found).get(self.player)

        card = self.add_card()
        self.now = card.info(self._player_icon(), "Aucune lecture")

        self.btn_prev = self._transport(self.IC_PREV, "previous",
                                        "Piste précédente")
        self.btn_toggle = self._transport(self.IC_PAUSE, "play-pause",
                                          "Lecture / pause", accent=True)
        self.btn_next = self._transport(self.IC_NEXT, "next", "Piste suivante")

        row = Gtk.Box(spacing=8, homogeneous=True)
        for btn in (self.btn_prev, self.btn_toggle, self.btn_next):
            row.pack_start(btn, True, True, 0)
        card.custom(row)

        # La liste des lecteurs n'a de sens qu'à partir de deux : avec un seul,
        # elle n'offrirait aucun choix. Elle est construite une fois pour
        # toutes — un lecteur ne s'ouvre ni ne se ferme dans les quelques
        # secondes où le popup est à l'écran, et seule la coche se rafraîchit.
        self.player_rows = []
        if len(found) > 1:
            picker = self.add_card("Lecteurs")
            for name, _status in found:
                key = base_name(name)
                label = PLAYER_LABELS.get(key, key.capitalize() or name)
                row = picker.action(
                    self.PLAYER_ICONS.get(key, self.IC_DEFAULT), label,
                    selected=(name == self.player),
                    on_click=lambda _b, n=name: self._pick(n))
                self.player_rows.append((row, name))

        self._apply()
        self._tick = GLib.timeout_add(REFRESH_MS, self._refresh)
        self.connect("destroy", self._stop_refresh)

    # ---- Construction ----

    def _transport(self, glyph, command, tooltip, accent=False):
        btn = Gtk.Button(label=glyph)
        btn.set_tooltip_text(tooltip)
        if accent:
            btn.get_style_context().add_class("accent")
        btn.connect("clicked", lambda _b, c=command: self._send(c))
        return btn

    def _player_icon(self):
        if not self.player:
            return self.IC_NONE
        return self.PLAYER_ICONS.get(base_name(self.player), self.IC_DEFAULT)

    # ---- Actions ----

    def _pick(self, name):
        self._pinned = name
        self.player = name
        self._refresh()

    def _send(self, command):
        """Envoie une commande au lecteur, sans refermer le popup.

        Enchaîner deux pistes est un geste courant : refermer à chaque clic,
        comme le font les menus qui lancent une application, obligerait à
        rouvrir le menu entre chaque. Seule la mise en PiP fait exception —
        elle déplace le regard vers la vidéo, le menu n'a plus rien à y faire.
        """
        if not self.player:
            return
        starting = command == "play-pause" and self._status != "Playing"
        subprocess.run(["playerctl", "-p", self.player, command],
                       stdout=DEVNULL, stderr=DEVNULL)

        if starting and is_browser(self.player) and not pip_open():
            # --playing : viser la vidéo que MPRIS annonce, pas celle qu'on a
            # sous les yeux — les deux diffèrent dès qu'on a changé d'onglet.
            # mpris-pip.sh donne le focus au navigateur pour lui envoyer le
            # raccourci de l'extension : le popup le perdrait de toute façon.
            subprocess.Popen([PIP_SCRIPT, "--playing"],
                             stdout=DEVNULL, stderr=DEVNULL)
            self.close()
            return

        # Spotify met un instant à publier le nouveau titre sur D-Bus ; le
        # tick périodique rattrape ce que cette relecture immédiate manque.
        GLib.timeout_add(250, self._refresh_once)

    # ---- Rafraîchissement ----

    def _refresh_once(self):
        self._refresh()
        return False

    def _refresh(self):
        found = players()
        names = [n for n, _ in found]
        if self._pinned and self._pinned not in names:
            self._pinned = None          # le lecteur épinglé s'est fermé
        player = self._pinned or auto_player(found)

        if player != self.player:
            self.player = player
            if self.now.icon_label is not None:
                self.now.icon_label.set_text(self._player_icon())
        self._status = dict(found).get(player)
        self._apply()
        return True

    def _apply(self):
        """Reflète l'état courant sur les widgets déjà construits.

        Mettre à jour les labels plutôt que reconstruire la carte : une
        reconstruction toutes les secondes ferait perdre le focus clavier et
        clignoter la fenêtre.
        """
        title, subtitle = now_playing(self.player)
        self.now.title_label.set_text(title or "Aucune lecture")

        sub = self.now.subtitle_label
        sub.set_text(subtitle)
        sub.set_visible(bool(subtitle))

        playing = self._status == "Playing"
        self.btn_toggle.set_label(self.IC_PAUSE if playing else self.IC_PLAY)
        for btn in (self.btn_prev, self.btn_toggle, self.btn_next):
            btn.set_sensitive(bool(self.player))

        for row, name in self.player_rows:
            ctx = row.get_style_context()
            if name == self.player:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _stop_refresh(self, *_):
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0


def main():
    run_popup(MediaPopup, "waybar-media-menu")


if __name__ == "__main__":
    main()
