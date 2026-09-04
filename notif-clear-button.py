#!/usr/bin/env python3
"""Bouton flottant « Tout effacer » au-dessus de la pile de notifications.

Apparait en haut a droite, juste sous la waybar et au-dessus des popups
swaync, des qu'au moins THRESHOLD notifications sont affichees a l'ecran.
Un clic les balaye toutes d'un coup.

Pourquoi un daemon et pas un simple module waybar : swaync n'expose que le
compteur du *centre* de notifications (des centaines de notifs archivees),
jamais le nombre de popups reellement visibles. On reconstruit donc cet etat
localement :

  - ajout      : hook swaync `run-on: receive` (voir notif-sound.sh) qui
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
                 empile « ID URGENCE » dans QUEUE puis envoie SIGUSR1 ici ;
  - expiration : minuteries calquees sur les timeouts de config.json
                 (timeout / timeout-low / timeout-critical, 0 = jamais) ;
  - purge      : flux `swaync-client -s`, dont la baisse de compteur trahit
                 une fermeture faite ailleurs (croix d'un popup, Super+X).

Clic gauche  : --hide-all  (retire les popups, tout reste dans le centre)
Clic droit   : --close-all (supprime reellement les notifications)
"""
import json
import os
import signal
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell  # noqa: E402

try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except (ValueError, ImportError):  # glib < 2.80
    signal_add = GLib.unix_signal_add

SWAYNC_CONFIG = os.path.expanduser("~/.config/swaync/config.json")
PIDFILE = os.path.join(RUNTIME_DIR, "notif-clear-button.pid")
QUEUE = os.path.join(RUNTIME_DIR, "notif-clear-button.queue")

# Nombre de popups a l'ecran a partir duquel le bouton apparait.
THRESHOLD = 2

# Marge sous la waybar. Doit rester coherente avec le `margin-top` applique a
# `.floating-notifications .notification-row:first-child` dans le style.css de
# swaync, qui reserve la bande ou se loge ce bouton.
MARGIN_TOP = 4

CSS = b"""
window { background-color: transparent; }
button.clear-pill {
    background-color: rgba(255, 255, 255, 0.09);
    color: #ebebf0;
    border: 1px solid #3a3a3c;
    border-radius: 14px;
    padding: 3px 14px;
    min-height: 22px;
    font-family: "JetBrainsMono Nerd Font Propo", "JetBrainsMono Nerd Font", sans-serif;
    font-size: 12px;
    box-shadow: none;
}
button.clear-pill:hover { background-color: #ff453a; color: #ffffff; border-color: #ff453a; }
"""


def load_timeouts():
    """Duree d'affichage d'un popup par urgence, en secondes (0 = jamais)."""
    defaults = {"Low": 4, "Normal": 6, "Critical": 0}
    try:
        with open(SWAYNC_CONFIG) as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return defaults
    return {
        "Low": cfg.get("timeout-low", defaults["Low"]),
        "Normal": cfg.get("timeout", defaults["Normal"]),
        "Critical": cfg.get("timeout-critical", defaults["Critical"]),
    }


class ClearButton:
    def __init__(self):
        self.timeouts = load_timeouts()
        # id notification -> instant d'expiration (None = ne disparait jamais)
        self.popups = {}
        self.last_count = None
        self.visible = False

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_app_paintable(True)
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.win.set_visual(visual)

        GtkLayerShell.init_for_window(self.win)
        GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self.win, "notif-clear-button")
        GtkLayerShell.set_anchor(self.win, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self.win, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.TOP, MARGIN_TOP)
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.RIGHT, 14)
        # Aucune interactivite clavier : le bouton ne doit jamais voler le
        # focus a la fenetre active.
        GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self.win, 0)

        self.button = Gtk.Button()
        self.button.get_style_context().add_class("clear-pill")
        self.button.connect("clicked", self.on_left_click)
        self.button.connect("button-press-event", self.on_button_press)
        self.win.add(self.button)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win.set_tooltip_text(
            "Clic : retirer les popups de l'écran\n"
            "Clic droit : les supprimer aussi du centre")

    # ── Etat des popups ────────────────────────────────────────────────
    def drain_queue(self):
        """Consomme les lignes « ID URGENCE » deposees par le hook swaync."""
        try:
            with open(QUEUE, "r+") as fh:
                lines = fh.read().splitlines()
                fh.truncate(0)
        except OSError:
            return
        now = time.monotonic()
        for line in lines:
            parts = line.split(None, 1)
            if not parts:
                continue
            nid = parts[0]
            urgency = parts[1].strip() if len(parts) > 1 else "Normal"
            secs = self.timeouts.get(urgency, self.timeouts["Normal"])
            # Un meme ID reaffiche (notification remplacee) repousse simplement
            # son expiration au lieu de compter deux fois.
            self.popups[nid] = None if not secs or secs <= 0 else now + secs

    def expire(self):
        now = time.monotonic()
        self.popups = {k: v for k, v in self.popups.items()
                       if v is None or v > now}

    def drop_oldest(self, n):
        """Retire n popups fermes ailleurs, les plus anciens d'abord."""
        for nid in list(self.popups)[:n]:
            self.popups.pop(nid, None)

    def clear(self):
        self.popups.clear()

    # ── Affichage ──────────────────────────────────────────────────────
    def refresh(self):
        count = len(self.popups)
        show = count >= THRESHOLD
        if show:
            self.button.set_label(f"\U000f039f  Tout effacer · {count}")
            if not self.visible:
                self.win.show_all()
                self.visible = True
        elif self.visible:
            self.win.hide()
            self.visible = False
        return True

    def tick(self):
        self.expire()
        self.refresh()
        return True

    # ── Actions ────────────────────────────────────────────────────────
    def on_button_press(self, _widget, event):
        if event.button == 3:
            self.run_client("--close-all")
            return True
        return False

    def on_left_click(self, _widget):
        self.run_client("--hide-all")

    def run_client(self, flag):
        subprocess.Popen(["swaync-client", flag, "-sw"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.clear()
        self.refresh()

    # ── Flux swaync : rattrape les fermetures faites ailleurs ──────────
    def watch_swaync(self):
        try:
            proc = subprocess.Popen(["swaync-client", "-s"],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            GLib.timeout_add_seconds(5, self.watch_swaync)
            return False
        GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP, self.on_swaync_event)
        return False

    def on_swaync_event(self, source, condition):
        if condition & GLib.IO_HUP:
            GLib.timeout_add_seconds(5, self.watch_swaync)
            return False
        line = source.readline()
        if not line:
            GLib.timeout_add_seconds(5, self.watch_swaync)
            return False
        try:
            count = json.loads(line).get("count")
        except ValueError:
            return True
        if count is None:
            return True
        if self.last_count is not None and count < self.last_count:
            # Le compteur du centre a baisse : autant de notifications ont ete
            # fermees (croix d'un popup, Super+X, vidage du centre).
            self.drop_oldest(self.last_count - count)
            self.refresh()
        self.last_count = count
        return True

    def on_signal(self):
        self.drain_queue()
        self.refresh()
        return True


def main():
    # Instance unique : remplace la precedente (reload waybar, relance manuelle).
    try:
        with open(PIDFILE) as fh:
            old = int(fh.read().strip())
        if old != os.getpid():
            os.kill(old, signal.SIGTERM)
    except (OSError, ValueError, ProcessLookupError):
        pass
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))
    open(QUEUE, "w").close()

    app = ClearButton()
    signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, app.on_signal)
    GLib.timeout_add(500, app.tick)
    GLib.idle_add(app.watch_swaync)

    def quit_(*_):
        for path in (PIDFILE, QUEUE):
            try:
                os.remove(path)
            except OSError:
                pass
        Gtk.main_quit()
        return False

    signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_)
    signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_)
    Gtk.main()


if __name__ == "__main__":
    main()
