#!/usr/bin/env python3
"""Popup Notifications pour Waybar (style menu luminosité).

Réglages de notification : Ne pas déranger (switch + minuteries 30 min / 1 h),
son des notifications, notifications discrètes (moins larges), notifications
de Claude Code.

La pile elle-même vit dans le centre swaync : ce popup ne fait que les
réglages, et se contente d'y mener par sa dernière carte.
"""
import os
import signal
import subprocess

# menu_common fixe lui-même la version de GTK et fournit tout ce qui sert
# ici : ce popup n'a plus une seule ligne de widget à écrire à la main.
from menu_common import GLib, Gtk, LayerPopup, run_popup

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
DND_TOGGLE = os.path.join(CONFIG_DIR, "dnd-toggle.sh")
# Lu par les hooks Notification et Stop de Claude Code (~/.claude/hooks) : tant
# qu'il existe, aucun des deux n'envoie de notification. Le suivi du module
# custom/claude, lui, continue.
CLAUDE_FLAG = os.path.join(CONFIG_DIR, "claude-notify-focus.disabled")
# Mascotte posée à côté des hooks — la même que sur leurs notifications.
CLAUDE_ICON = os.path.expanduser("~/.claude/hooks/clawd.png")
SOUND_FLAG = os.path.join(CONFIG_DIR, "notif-sound.enabled")
# Largeur des notifications flottantes : le drapeau est lu par notif-compact.sh,
# qui régénère la config swaync et la fait recharger.
COMPACT_FLAG = os.path.join(CONFIG_DIR, "notif-compact.enabled")
COMPACT_TOGGLE = os.path.join(CONFIG_DIR, "notif-compact.sh")
# Opacité des surfaces de swaync, en pourcent. Même script : il réécrit le
# bloc généré de style-active.css et demande à swaync de relire son CSS.
# Fichier absent = 72 %, l'alpha écrit en dur dans style.css.
OPACITY_FILE = os.path.join(CONFIG_DIR, "notif-opacity")
DEFAULT_OPACITY = 72
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
    IC_COMPACT = "\U000f084c"  # flèches qui se resserrent
    IC_OPACITY = "\U000f05cc"  # gouttes superposées (opacité)
    IC_CLAUDE = "\U000f09d1"   # cerveau
    IC_CENTER = "\U000f009a"   # cloche
    IC_CLEAR = "\U000f01b4"    # balai

    def __init__(self):
        super().__init__("Notifications", width=340, margin_right=360)
        self._opacity_timeout_id = None

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
        prefs.toggle(self.IC_COMPACT, "Notifications discrètes",
                     os.path.exists(COMPACT_FLAG), self._on_compact_toggled,
                     subtitle="cartes plus étroites, texte plus petit")
        # Le curseur suit l'interrupteur : les deux règlent l'encombrement
        # d'une notification à l'écran, l'un sa taille, l'autre sa présence.
        opacity = self._read_opacity()
        self.scale_opacity = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 30, 100, 5)
        self.scale_opacity.set_value(opacity)
        self.scale_opacity.set_draw_value(False)
        _row, self.lbl_opacity = prefs.slider(
            self.IC_OPACITY, "Opacité", self.scale_opacity, "%d %%" % opacity)
        self.scale_opacity.connect("value-changed", self._on_opacity_changed)
        # Un aperçu par déplacement, pas un par pixel : le CSS se recharge en
        # continu pendant le glissé, mais la notification-témoin — la seule
        # chose qui rende le réglage visible quand la pile est vide — n'est
        # envoyée qu'au relâchement.
        self.scale_opacity.connect("button-release-event", self._preview_opacity)
        self.scale_opacity.connect("key-release-event", self._preview_opacity)
        # Actif tant que le drapeau « disabled » est absent. L'ancien libellé
        # « Notif Claude → fenêtre » laissait croire qu'il ne réglait que le
        # clic vers la fenêtre, alors qu'il coupe les notifications elles-mêmes.
        prefs.toggle(self.IC_CLAUDE, "Notifications Claude Code",
                     not os.path.exists(CLAUDE_FLAG), self._on_claude_toggled,
                     subtitle="en attente, tâche terminée")

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

    def _on_compact_toggled(self, switch, _param):
        # Le script pose ou retire lui-même le drapeau et recharge swaync ;
        # la notification part APRÈS, pour s'afficher déjà à la nouvelle
        # largeur — c'est elle qui montre le résultat du réglage.
        subprocess.run([COMPACT_TOGGLE, "on" if switch.get_active() else "off"],
                       stdout=DEVNULL, stderr=DEVNULL)
        subprocess.Popen(["notify-send", "-a", "swaync", "Notifications discrètes",
                          "Activées" if switch.get_active() else "Désactivées"])

    def _read_opacity(self):
        try:
            value = int(open(OPACITY_FILE).read().strip())
        except Exception:
            return DEFAULT_OPACITY
        return min(100, max(30, value))

    def _on_opacity_changed(self, scale):
        # Debounce : le CSS est régénéré et relu par swaync à chaque appel,
        # inutile de le faire à chaque pixel du glissé.
        if self._opacity_timeout_id:
            GLib.source_remove(self._opacity_timeout_id)
        value = int(scale.get_value())
        self.lbl_opacity.set_text("%d %%" % value)
        self._opacity_timeout_id = GLib.timeout_add(
            120, self._apply_opacity, value)

    def _apply_opacity(self, value):
        self._opacity_timeout_id = None
        subprocess.Popen([COMPACT_TOGGLE, "opacity", str(value)],
                         stdout=DEVNULL, stderr=DEVNULL)
        return False

    def _preview_opacity(self, _widget, _event):
        value = int(self.scale_opacity.get_value())
        # -r : une seule notification-témoin, remplacée à chaque essai, plutôt
        # qu'une pile qui grandit à mesure qu'on cherche la bonne valeur.
        subprocess.Popen(["notify-send", "-a", "swaync", "-r", "9911",
                          "Opacité des notifications", "%d %%" % value],
                         stdout=DEVNULL, stderr=DEVNULL)
        return False

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
        subprocess.Popen(["notify-send", "-a", "Claude Code", "-i", CLAUDE_ICON,
                          "Notifications Claude Code", msg])
        subprocess.Popen([os.path.join(CONFIG_DIR, "notification-refresh.sh")],
                         stdout=DEVNULL, stderr=DEVNULL)


def main():
    run_popup(NotificationPopup, "waybar-notification-menu")


if __name__ == "__main__":
    main()
