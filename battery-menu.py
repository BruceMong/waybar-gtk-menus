#!/usr/bin/env python3
"""Popup Batterie & Énergie pour Waybar (style menu luminosité).

Profil énergétique (power-profiles-daemon), caféine (caffeine.service) et
délais hypridle : verrouillage, extinction écran, mise en veille. Application
immédiate. Le profil et la caféine n'ont plus de module dans la barre : ce
menu est leur seule interface à la souris.
"""
import os
import re
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup, run_popup  # noqa: E402

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
    """(start, end) du bloc listener ANNONCÉ par `marker`, bornes incluses.

    Le marqueur est le commentaire qui coiffe le bloc : le `listener {` qu'il
    désigne le SUIT, il ne le précède pas. Remonter depuis le marqueur — ce
    que faisait la version précédente — attrapait le bloc d'au-dessus :
    « Extinction ecran » ouvrait sur le listener du verrouillage, si bien que
    son curseur réécrivait le délai de lock ; « Verrouillage », n'ayant rien
    au-dessus de lui, remontait jusqu'à la ligne 0 et englobait `general {}`,
    que « Jamais » commentait en entier (donc plus de lock_cmd du tout).

    On descend donc du marqueur vers le premier `listener` rencontré, puis
    jusqu'à l'accolade qui le referme. Un bloc désactivé étant commenté ligne
    à ligne, « listener » et « } » restent reconnaissables tels quels.
    """
    try:
        head = next(i for i, line in enumerate(lines) if marker in line)
    except StopIteration:
        return None
    start = next((i for i in range(head, len(lines))
                  if "listener" in lines[i]), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if "}" in lines[i]), None)
    if end is None:
        return None
    return start, end


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
    IC_CAFFEINE = "\U000f0176"  # tasse
    IC_LOCK = "\U000f033e"      # cadenas
    IC_SCREEN = "\U000f0379"    # écran
    IC_SLEEP = "\U000f0904"     # veille
    IC_BATTERY = "\U000f0079"   # batterie
    IC_PLUG = "\U000f06a5"      # prise secteur
    IC_POWER = "\U000f0241"     # éclair
    IC_HEALTH = "\U000f0b7c"    # cœur / santé

    def __init__(self):
        super().__init__("Batterie & Énergie", width=360, margin_right=150)
        self._reload_id = None
        self._delays = []       # (marker, scale, label), du plus tôt au plus tard
        self._syncing = False   # propagation en cours : ne pas la relancer

        # -- État de la batterie --
        # Un popup intitulé « Batterie » qui ne disait rien de la batterie
        # renvoyait au tooltip du module pour la seule information qu'on vient
        # y chercher : combien il reste, et pour combien de temps.
        self._add_battery_card()

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
        # L'ordre des lignes est celui des événements, et il est contraint :
        # cf. _enforce_order.
        idle = self.add_card("Après inactivité")
        # Caféine en tête : c'est l'interrupteur qui suspend les trois délais
        # du dessous. L'icône de la barre ne s'affiche que quand elle est
        # active ; ici est le seul endroit où on l'allume à la souris.
        idle.toggle(self.IC_CAFFEINE, "Caféine", self._caffeine_active(),
                    self._on_caffeine_toggled,
                    subtitle="bloque verrouillage, extinction et veille")
        self.lbl_lock = self._add_delay(idle, self.IC_LOCK,
                                        "Verrouillage", LOCK)
        self.lbl_dpms = self._add_delay(idle, self.IC_SCREEN,
                                        "Extinction écran", DPMS)
        self.lbl_susp = self._add_delay(idle, self.IC_SLEEP,
                                        "Mise en veille", SUSPEND)

    # ---- Batterie ----

    @staticmethod
    def _battery_path():
        """Premier /sys/class/power_supply qui est bien une batterie."""
        base = "/sys/class/power_supply"
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return None
        for name in names:
            path = os.path.join(base, name)
            try:
                with open(os.path.join(path, "type")) as f:
                    if f.read().strip() == "Battery":
                        return path
            except OSError:
                continue
        return None

    @staticmethod
    def _read(path, name, cast=str):
        try:
            with open(os.path.join(path, name)) as f:
                return cast(f.read().strip())
        except (OSError, ValueError):
            return None

    def _add_battery_card(self):
        """Charge, autonomie, puissance, santé — ce que le module ne dit pas.

        Tout vient de /sys/class/power_supply : `upower` ferait le même travail
        au prix d'un appel externe, pour des champs que le noyau expose déjà.
        """
        path = self._battery_path()
        if path is None:
            return                      # machine sans batterie : rien à dire

        capacity = self._read(path, "capacity", int)
        status = self._read(path, "status") or ""
        card = self.add_card("Batterie")

        charging = status in ("Charging", "Full")
        icon = self.IC_PLUG if charging else self.IC_BATTERY
        title = "%d %%" % capacity if capacity is not None else "Niveau inconnu"
        card.info(icon, title, subtitle=self._status_text(path, status))

        # Puissance instantanée : certains noyaux exposent power_now (µW),
        # d'autres seulement current_now (µA) qu'il faut multiplier par la
        # tension. Sans l'un ni l'autre, la ligne ne s'affiche pas.
        watts = self._power_watts(path)
        if watts is not None:
            card.info(self.IC_POWER, "Consommation", value="%.1f W" % watts)

        health = self._health(path)
        if health is not None:
            card.info(self.IC_HEALTH, "Santé", value="%d %%" % health,
                      subtitle="capacité pleine charge / capacité d'origine")

    def _status_text(self, path, status):
        """« en charge — 1 h 12 avant 100 % », ou l'autonomie restante."""
        labels = {"Charging": "en charge", "Discharging": "sur batterie",
                  "Full": "pleine", "Not charging": "branchée"}
        text = labels.get(status, status.lower() or "état inconnu")
        secs = self._time_remaining(path, status)
        if secs:
            hours, minutes = divmod(int(secs) // 60, 60)
            left = "%d h %02d" % (hours, minutes) if hours else "%d min" % minutes
            text += (" — %s avant 100 %%" if status == "Charging"
                     else " — %s d'autonomie") % left
        return text

    def _power_watts(self, path):
        micro = self._read(path, "power_now", int)
        if micro is None:
            amps = self._read(path, "current_now", int)
            volts = self._read(path, "voltage_now", int)
            if amps is None or volts is None:
                return None
            micro = amps * volts / 1e6
        return abs(micro) / 1e6

    def _time_remaining(self, path, status):
        """Secondes restantes, estimées depuis l'énergie et le débit courant."""
        watts = self._power_watts(path)
        if not watts:
            return None
        # energy_* est en µWh, charge_* en µAh : le second se convertit en
        # énergie par la tension.
        now = self._read(path, "energy_now", int)
        full = self._read(path, "energy_full", int)
        if now is None or full is None:
            volts = self._read(path, "voltage_now", int)
            charge_now = self._read(path, "charge_now", int)
            charge_full = self._read(path, "charge_full", int)
            if None in (volts, charge_now, charge_full):
                return None
            now = charge_now * volts / 1e6
            full = charge_full * volts / 1e6
        remaining = (full - now) if status == "Charging" else now
        return remaining / 1e6 / watts * 3600

    def _health(self, path):
        full = self._read(path, "energy_full", int) or \
            self._read(path, "charge_full", int)
        design = self._read(path, "energy_full_design", int) or \
            self._read(path, "charge_full_design", int)
        if not full or not design:
            return None
        return int(round(full / design * 100))

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

    # ---- Caféine ----

    @staticmethod
    def _caffeine_active():
        return subprocess.run(
            ["systemctl", "--user", "-q", "is-active", "caffeine"],
            check=False).returncode == 0

    @staticmethod
    def _on_caffeine_toggled(switch, _pspec):
        # caffeine-toggle.sh porte la notification et le rafraîchissement de
        # l'icône de la barre ; on lui passe l'état voulu, pas une bascule,
        # pour que l'interrupteur et l'unité ne se croisent jamais.
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "caffeine-toggle.sh")
        subprocess.Popen([script, "on" if switch.get_active() else "off"],
                         stdout=DEVNULL, stderr=DEVNULL)

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
        self._delays.append((marker, scale, lbl))
        return lbl

    @staticmethod
    def _delay_text(idx):
        return "Jamais" if idx >= JAMAIS else "%d min" % DURATIONS[idx]

    def _enforce_order(self, moved):
        """Garde verrouillage < extinction écran < mise en veille.

        Ce n'est pas une coquetterie : hypridle.conf le dit en capitales. Un
        hyprlock qui démarre alors que la sortie DRM est déjà éteinte crée sa
        surface sur un écran mort ; au réveil, le rallumage passe mais rien
        n'est re-rendu, et seul un cycle de veille récupère la session. Rien
        n'empêchait de régler le verrouillage à 60 min et l'extinction à 1 min.

        Deux règles, et c'est tout ce qui fait la subtilité :

        · « Jamais » ne contraint rien et n'est contraint par rien. C'est un
          délai qui n'arrive pas : il ne peut ni arriver trop tôt, ni forcer
          un voisin à se décaler. Le traiter comme le cran le plus tardif
          faisait basculer les trois réglages à « Jamais » dès qu'on y mettait
          le verrouillage, alors qu'éteindre l'écran sans verrouiller est
          parfaitement légitime.

        · quand la cascade réclame des crans qui n'existent pas — ramener
          l'extinction à 1 min voudrait un verrouillage à 0 —, c'est le
          curseur déplacé qui cède le minimum nécessaire. La version
          précédente écrasait les deux réglages sur la même valeur, ce qui
          était précisément la situation que cette méthode doit interdire.
        """
        order = [scale for _m, scale, _l in self._delays]
        pivot = order.index(moved)
        idx = [int(round(s.get_value())) for s in order]

        for i in range(pivot - 1, -1, -1):
            if JAMAIS not in (idx[i], idx[i + 1]) and idx[i] >= idx[i + 1]:
                idx[i] = idx[i + 1] - 1
        for i in range(pivot + 1, len(idx)):
            if JAMAIS not in (idx[i], idx[i - 1]) and idx[i] <= idx[i - 1]:
                idx[i] = idx[i - 1] + 1

        shortfall = -min(idx)
        if shortfall > 0:
            idx = [v if v == JAMAIS else min(v + shortfall, JAMAIS)
                   for v in idx]

        # Seuls les curseurs qui bougent vraiment sont réécrits : `set_value`
        # rappelle _on_delay_changed, donc réécrit hypridle.conf.
        for scale, value in zip(order, idx):
            if int(round(scale.get_value())) != value:
                scale.set_value(value)

    def _on_delay_changed(self, scale, marker, lbl):
        idx = int(round(scale.get_value()))
        minutes = 0 if idx >= JAMAIS else DURATIONS[idx]
        lbl.set_text(self._delay_text(idx))
        set_timeout(marker, minutes)
        # La propagation déplace d'autres curseurs, donc rappelle ce handler :
        # le drapeau évite qu'ils ne se repoussent mutuellement en boucle.
        if not self._syncing:
            self._syncing = True
            try:
                self._enforce_order(scale)
            finally:
                self._syncing = False
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
    run_popup(BatteryPopup, "waybar-battery-menu")


if __name__ == "__main__":
    main()
