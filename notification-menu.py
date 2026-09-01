#!/usr/bin/env python3
"""Popup Notifications pour Waybar (style menu luminosité).

Réglages de notification : Ne pas déranger (switch + minuteries 30 min / 1 h),
son des notifications, effet « Notif Claude → fenêtre ».

La pile de notifications vit dans le centre swaync (clic gauche sur la cloche)
et pas ici : ce popup ne fait que les réglages.
"""
import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
DND_TOGGLE = os.path.join(CONFIG_DIR, "dnd-toggle.sh")
CLAUDE_FLAG = os.path.join(CONFIG_DIR, "claude-notify-focus.disabled")
SOUND_FLAG = os.path.join(CONFIG_DIR, "notif-sound.enabled")
SNOOZE_PID = "/tmp/dnd-snooze.pid"
DEVNULL = subprocess.DEVNULL


class NotificationPopup(LayerPopup):
    def __init__(self):
        super().__init__("Notifications", width=340, margin_right=360)

        # -- Ne pas déranger --
        self.box.pack_start(self._switch_row(
            "  Ne pas déranger", self._dnd_state(),
            self._on_dnd_toggled, store="switch_dnd"), False, False, 0)

        # -- Minuteries Ne pas déranger --
        row = Gtk.Box(spacing=8, homogeneous=True)
        btn30 = Gtk.Button(label="  30 min")
        btn30.connect("clicked", lambda *_: self._snooze(1800, "30 min"))
        btn1h = Gtk.Button(label="  1 h")
        btn1h.connect("clicked", lambda *_: self._snooze(3600, "1 h"))
        row.pack_start(btn30, True, True, 0)
        row.pack_start(btn1h, True, True, 0)
        self.box.pack_start(row, False, False, 0)

        # -- Son des notifications --
        self.box.pack_start(self._switch_row(
            "  Son des notifications", os.path.exists(SOUND_FLAG),
            self._on_sound_toggled), False, False, 0)

        # -- Notif Claude → fenêtre (actif tant que le drapeau est absent) --
        self.box.pack_start(self._switch_row(
            "󰧑  Notif Claude → fenêtre", not os.path.exists(CLAUDE_FLAG),
            self._on_claude_toggled), False, False, 0)

    # ---- Construction ----

    def _switch_row(self, title, active, handler, store=None):
        row = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>%s</b>" % title)
        row.pack_start(lbl, True, True, 0)

        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(active)
        sw.connect("notify::active", handler)
        row.pack_end(sw, False, False, 0)
        if store:
            setattr(self, store, sw)
        return row

    # ---- État ----

    def _dnd_state(self):
        try:
            return subprocess.check_output(
                ["swaync-client", "--get-dnd"], text=True).strip() == "true"
        except Exception:
            return False

    # ---- Handlers ----

    def _on_dnd_toggled(self, switch, _param):
        subprocess.Popen([DND_TOGGLE, "on" if switch.get_active() else "off"],
                         stdout=DEVNULL, stderr=DEVNULL)

    def _snooze(self, secs, label):
        if os.path.exists(SNOOZE_PID):
            try:
                os.kill(int(open(SNOOZE_PID).read().strip()), signal.SIGTERM)
            except Exception:
                pass
            try:
                os.remove(SNOOZE_PID)
            except OSError:
                pass
        subprocess.Popen(["notify-send", "-a", "swaync",
                          "Ne pas déranger", "Activé pour %s" % label])
        subprocess.run([DND_TOGGLE, "on"], stdout=DEVNULL, stderr=DEVNULL)
        proc = subprocess.Popen(
            ["bash", "-c",
             "sleep %d; rm -f '%s'; '%s' off" % (secs, SNOOZE_PID, DND_TOGGLE)],
            stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        with open(SNOOZE_PID, "w") as f:
            f.write(str(proc.pid))
        self.close()

    def _on_sound_toggled(self, switch, _param):
        if switch.get_active():
            open(SOUND_FLAG, "a").close()
            msg = "Activé"
        else:
            try:
                os.remove(SOUND_FLAG)
            except OSError:
                pass
            msg = "Coupé"
        subprocess.Popen(["notify-send", "-a", "swaync",
                          "Son des notifications", msg])
        subprocess.Popen([os.path.join(CONFIG_DIR, "notification-refresh.sh")],
                         stdout=DEVNULL, stderr=DEVNULL)

    def _on_claude_toggled(self, switch, _param):
        # actif (switch on) = drapeau "disabled" absent
        if switch.get_active():
            try:
                os.remove(CLAUDE_FLAG)
            except OSError:
                pass
            msg = "Activé"
        else:
            open(CLAUDE_FLAG, "a").close()
            msg = "Désactivé"
        subprocess.Popen(["notify-send", "-a", "Claude Code",
                          "Notif Claude → fenêtre", msg])
        subprocess.Popen([os.path.join(CONFIG_DIR, "notification-refresh.sh")],
                         stdout=DEVNULL, stderr=DEVNULL)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    NotificationPopup().run()


if __name__ == "__main__":
    main()
