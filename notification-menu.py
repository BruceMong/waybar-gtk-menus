#!/usr/bin/env python3
"""Popup Notifications pour Waybar (style menu luminosité).

Réglages de notification : Ne pas déranger (switch + minuteries 30 min / 1 h),
son des notifications, effet « Notif Claude → fenêtre ».

La pile elle-même vit dans le centre swaync : ce popup ne fait que les
réglages, et se contente d'y mener par sa dernière carte.
"""
import os
import signal
import subprocess

# menu_common fixe lui-même la version de GTK et fournit tout ce qui sert
# ici : ce popup n'a plus une seule ligne de widget à écrire à la main.
from menu_common import LayerPopup, run_popup

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
    IC_CENTER = "\U000f009a"   # cloche
    IC_CLEAR = "\U000f01b4"    # balai

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

        # La pile elle-même vit dans swaync. Le clic gauche sur la cloche y
        # mène déjà, mais ce popup s'ouvre au clic DROIT : sans ces deux
        # lignes, il fallait le refermer et viser à nouveau l'icône pour
        # simplement lire ses notifications.
        pile = self.add_card()
        pile.action(self.IC_CENTER, "Centre de notifications", chevron=True,
                    on_click=self._open_center)
        pile.action(self.IC_CLEAR, "Tout effacer",
                    subtitle="vide la pile et le centre",
                    on_click=self._clear_all)

    # ---- Pile de notifications ----

    def _open_center(self, _btn):
        subprocess.Popen(["swaync-client", "--toggle-panel"],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _clear_all(self, _btn):
        subprocess.Popen(["swaync-client", "--close-all"],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

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
    run_popup(NotificationPopup, "waybar-notification-menu")


if __name__ == "__main__":
    main()
