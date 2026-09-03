#!/usr/bin/env python3
"""Popup Batterie & Énergie pour Waybar (style menu luminosité).

Profil énergétique (power-profiles-daemon) + délais hypridle :
verrouillage, extinction écran, mise en veille. Application immédiate.
"""
import os
import re
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

HYPRIDLE = os.path.expanduser("~/.config/hypr/hypridle.conf")
DEVNULL = subprocess.DEVNULL

# Marqueurs (commentaires) identifiant chaque bloc listener dans hypridle.conf.
LOCK = "Verrouillage"
DPMS = "Extinction ecran"
SUSPEND = "Suspend"

# Durées proposées (minutes) ; le dernier cran du slider = « Jamais ».
DURATIONS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60]
JAMAIS = len(DURATIONS)  # index du cran « Jamais »

# (id power-profiles-daemon, libellé)
PROFILES = [
    ("power-saver", "Économie"),
    ("balanced", "Équilibré"),
    ("performance", "Performance"),
]


# --- Édition de hypridle.conf -------------------------------------------------

def _block_bounds(lines, marker):
    """(start, end) du bloc listener contenant `marker`, bornes incluses."""
    for i, line in enumerate(lines):
        if marker in line:
            start = i
            while start > 0 and "listener" not in lines[start]:
                start -= 1
            end = i
            while end < len(lines) - 1 and "}" not in lines[end]:
                end += 1
            return start, end
    return None


def _is_disabled(block):
    for line in block:
        if "listener" in line:
            return line.lstrip().startswith("#")
    return False


def get_timeout_index(marker):
    """Index slider du délai courant (JAMAIS si le bloc est commenté)."""
    try:
        with open(HYPRIDLE, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return JAMAIS
    bounds = _block_bounds(lines, marker)
    if not bounds:
        return JAMAIS
    start, end = bounds
    block = lines[start:end + 1]
    if _is_disabled(block):
        return JAMAIS
    for line in block:
        m = re.search(r"timeout\s*=\s*(\d+)", line)
        if m:
            minutes = int(m.group(1)) // 60
            if minutes in DURATIONS:
                return DURATIONS.index(minutes)
            # cran le plus proche
            return min(range(len(DURATIONS)),
                       key=lambda i: abs(DURATIONS[i] - minutes))
    return JAMAIS


def set_timeout(marker, minutes):
    """minutes == 0 → désactive (commente) le bloc ; sinon (ré)active + règle."""
    with open(HYPRIDLE, encoding="utf-8") as f:
        lines = f.read().split("\n")
    bounds = _block_bounds(lines, marker)
    if not bounds:
        return
    start, end = bounds
    disabled = _is_disabled(lines[start:end + 1])

    if minutes == 0:
        if not disabled:
            for i in range(start, end + 1):
                lines[i] = "#" + lines[i]
    else:
        if disabled:
            for i in range(start, end + 1):
                lines[i] = re.sub(r"^#", "", lines[i], count=1)
        secs = minutes * 60
        for i in range(start, end + 1):
            if re.search(r"^\s*timeout\s*=", lines[i]):
                lines[i] = re.sub(r"timeout\s*=\s*\d+",
                                  "timeout = %d" % secs, lines[i])
                break

    with open(HYPRIDLE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- Popup --------------------------------------------------------------------

class BatteryPopup(LayerPopup):
    """Deux cartes : ce que consomme la machine, quand elle s'éteint seule.

    Chaque réglage tient dans une ligne unique — libellé et valeur en tête,
    curseur en dessous. La version précédente alternait un libellé nu et un
    curseur nu sur toute la hauteur du popup : rien ne rattachait l'un à
    l'autre, sinon la proximité.
    """

    IC_PROFILE = "\U000f04c5"   # compteur de vitesse
    IC_LOCK = "\U000f033e"      # cadenas
    IC_SCREEN = "\U000f0379"    # écran
    IC_SLEEP = "\U000f0904"     # veille

    def __init__(self):
        super().__init__("Batterie & Énergie", width=360, margin_right=150)
        self._reload_id = None

        # -- Profil énergétique --
        energy = self.add_card("Énergie")
        self.scale_profile = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, len(PROFILES) - 1, 1)
        self.scale_profile.set_draw_value(False)
        self.scale_profile.add_mark(0, Gtk.PositionType.BOTTOM, "Éco")
        self.scale_profile.add_mark(len(PROFILES) - 1,
                                    Gtk.PositionType.BOTTOM, "Perf")
        prof_idx = self._current_profile_index()
        self.scale_profile.set_value(prof_idx)
        _row, self.lbl_profile = energy.slider(
            self.IC_PROFILE, "Profil", self.scale_profile,
            PROFILES[prof_idx][1])
        self.scale_profile.connect("value-changed", self._on_profile_changed)

        # -- Délais hypridle --
        idle = self.add_card("Après inactivité")
        self.lbl_lock = self._add_delay(idle, self.IC_LOCK,
                                        "Verrouillage", LOCK)
        self.lbl_dpms = self._add_delay(idle, self.IC_SCREEN,
                                        "Extinction écran", DPMS)
        self.lbl_susp = self._add_delay(idle, self.IC_SLEEP,
                                        "Mise en veille", SUSPEND)

    # ---- Profil ----

    def _current_profile_index(self):
        try:
            cur = subprocess.check_output(
                ["powerprofilesctl", "get"], text=True).strip()
        except Exception:
            cur = "balanced"
        return next((i for i, (pid, _) in enumerate(PROFILES) if pid == cur), 1)

    def _set_profile_label(self, idx):
        self.lbl_profile.set_text(PROFILES[idx][1])

    def _on_profile_changed(self, scale):
        idx = int(round(scale.get_value()))
        pid, _ = PROFILES[idx]
        subprocess.Popen(["powerprofilesctl", "set", pid],
                         stdout=DEVNULL, stderr=DEVNULL)
        self._set_profile_label(idx)

    # ---- Délais ----

    def _add_delay(self, card, icon, title, marker):
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, JAMAIS, 1)
        scale.set_draw_value(False)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, "1 min")
        scale.add_mark(JAMAIS, Gtk.PositionType.BOTTOM, "Jamais")

        idx = get_timeout_index(marker)
        scale.set_value(idx)
        _row, lbl = card.slider(icon, title, scale, self._delay_text(idx))
        scale.connect("value-changed", self._on_delay_changed, marker, lbl)
        return lbl

    @staticmethod
    def _delay_text(idx):
        return "Jamais" if idx >= JAMAIS else "%d min" % DURATIONS[idx]

    def _on_delay_changed(self, scale, marker, lbl):
        idx = int(round(scale.get_value()))
        minutes = 0 if idx >= JAMAIS else DURATIONS[idx]
        lbl.set_text(self._delay_text(idx))
        set_timeout(marker, minutes)
        self._schedule_reload()

    def _schedule_reload(self):
        if self._reload_id:
            GLib.source_remove(self._reload_id)
        self._reload_id = GLib.timeout_add(400, self._reload_hypridle)

    def _reload_hypridle(self):
        self._reload_id = None
        subprocess.run(["killall", "hypridle"], stdout=DEVNULL, stderr=DEVNULL)
        subprocess.Popen(["hypridle"], stdout=DEVNULL, stderr=DEVNULL,
                         start_new_session=True)
        return False


def main():
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    BatteryPopup().run()


if __name__ == "__main__":
    main()
