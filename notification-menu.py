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

# menu_common fixe lui-même la version de GTK et fournit tout ce qui sert
# ici : ce popup n'a plus une seule ligne de widget à écrire à la main.
from menu_common import LayerPopup

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
DND_TOGGLE = os.path.join(CONFIG_DIR, "dnd-toggle.sh")
CLAUDE_FLAG = os.path.join(CONFIG_DIR, "claude-notify-focus.disabled")
SOUND_FLAG = os.path.join(CONFIG_DIR, "notif-sound.enabled")
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
SNOOZE_PID = os.path.join(RUNTIME_DIR, "dnd-snooze.pid")
DEVNULL = subprocess.DEVNULL


class NotificationPopup(LayerPopup):
    """Trois cartes : l'interrupteur, le silence minuté, les réglages durables.

    Les deux minuteries étaient auparavant une paire de boutons côte à côte,
    sans rapport visible avec l'interrupteur qu'elles actionnent. En lignes,
    sous le même toit, elles se lisent pour ce qu'elles sont : deux façons
    d'activer le même réglage, mais pour un temps donné.
    """

    IC_DND = "\U000f009b"      # cloche barrée
    IC_TIMER = "\U000f051b"    # minuteur
    IC_SOUND = "\U000f057e"    # haut-parleur
    IC_CLAUDE = "\U000f09d1"   # cerveau

    def __init__(self):
        super().__init__("Notifications", width=340, margin_right=360)

        dnd = self.add_card()
        self.switch_dnd = dnd.toggle(self.IC_DND, "Ne pas déranger",
                                     self._dnd_state(), self._on_dnd_toggled)

        snooze = self.add_card("Silence temporaire")
        snooze.action(self.IC_TIMER, "30 minutes",
                      on_click=lambda *_: self._snooze(1800, "30 min"))
        snooze.action(self.IC_TIMER, "1 heure",
                      on_click=lambda *_: self._snooze(3600, "1 h"))

        prefs = self.add_card("Réglages")
        prefs.toggle(self.IC_SOUND, "Son des notifications",
                     os.path.exists(SOUND_FLAG), self._on_sound_toggled)
        # Actif tant que le drapeau « disabled » est absent.
        prefs.toggle(self.IC_CLAUDE, "Notif Claude → fenêtre",
                     not os.path.exists(CLAUDE_FLAG), self._on_claude_toggled)

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
