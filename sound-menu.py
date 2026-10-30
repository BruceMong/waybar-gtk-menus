#!/usr/bin/env python3
"""Popup Son pour Waybar (style menu luminosité / notifications).

Actions courantes faites maison :
  - volume haut-parleur (slider) + Muet
  - volume micro (slider) + Micro coupé
  - choix de la sortie et de l'entrée audio (si plusieurs périphériques)
Et un bouton « Réglages avancés » qui ouvre pavucontrol (le menu complet).
"""
import os
import re
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup, run_popup  # noqa: E402
from mic_meter import MicMeter  # noqa: E402

DEVNULL = subprocess.DEVNULL
SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
VOL_MAX = 150  # plafond cohérent avec le scroll waybar (-l 1.5)

# Enregistrer (réunion, écran) a son propre popup, ouvert depuis l'icône ●
# de la barre : il prenait un tiers de celui-ci pour une action de call.
RECORD_MENU = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "record-menu.py")


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""


def get_volume(target):
    """Renvoie (volume_en_%, muted)."""
    out = run(["wpctl", "get-volume", target])
    m = re.search(r"Volume:\s*([0-9.]+)", out)
    vol = int(round(float(m.group(1)) * 100)) if m else 0
    return vol, "[MUTED]" in out


def source_exists():
    return bool(re.search(r"Volume:", run(["wpctl", "get-volume", SOURCE])))


def _list_devices(kind):
    """Périphériques pulse d'un type donné : [(name, description, is_default)].

    `kind` vaut "sinks" ou "sources" ; les deux se lisent exactement pareil,
    d'où la fonction commune. Le menu ne proposait le choix que pour la sortie,
    et il fallait ouvrir pavucontrol pour désigner un micro — alors que c'est
    le geste le plus fréquent des deux quand un casque vient d'être branché.
    """
    default = run(["pactl", "get-default-%s" % kind[:-1]]).strip()
    devices, name = [], None
    for line in run(["pactl", "list", kind]).splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            name = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else ""
        elif s.startswith("Description:") and name is not None:
            desc = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else name
            devices.append((name, desc, name == default))
            name = None
    return devices


def list_sinks():
    return _list_devices("sinks")


def list_sources():
    """Entrées réelles : les moniteurs de sortie ne sont pas des micros.

    pactl liste un `.monitor` par sink — les proposer comme entrée reviendrait
    à offrir « enregistre ce que tu entends » au milieu des micros, ce qui n'est
    jamais ce qu'on cherche ici et fait doubler la liste.
    """
    return [d for d in _list_devices("sources") if not d[0].endswith(".monitor")]


class SoundPopup(LayerPopup):
    """Trois cartes : ce qui sort, ce qui entre, et les actions (dont Enregistrer).

    L'ancienne version empilait quinze contrôles de même poids visuel sur un
    seul niveau ; la carte du micro dit maintenant d'un coup d'œil que le
    curseur, la coupure et le VU-mètre parlent du même périphérique.
    """

    # Un seul glyphe par fonction, tous dans la même fonte : les emoji couleur
    # de la version précédente (🔊, 🎤) rompaient la colonne d'icônes, qui est
    # justement ce qui aligne l'ensemble.
    IC_OUT = "\U000f057e"      # haut-parleur
    IC_MUTE = "\U000f075f"     # haut-parleur barré
    IC_SINK = "\U000f04c3"     # périphérique de sortie
    IC_MIC = "\U000f036c"      # micro
    IC_MIC_OFF = "\U000f036d"  # micro barré
    IC_SOURCE = "\U000f036c"   # périphérique d'entrée
    IC_LEVEL = ""              # le VU-mètre porte son propre libellé
    IC_RECORD = "\U000f044a"   # ● record
    IC_FIX = "\U000f0709"      # rotation / réinitialisation
    IC_PREFS = "\U000f0493"    # engrenage

    def __init__(self):
        super().__init__("Son", width=360, margin_right=70)
        self._timeouts = {}
        self.sink_rows = []
        self.source_rows = []
        self.switch_mic = None
        self.meter = None

        vol, muted = get_volume(SINK)

        # ── Sortie ──
        out = self.add_card("Sortie")
        self.scale_out = self._make_scale(vol, SINK)
        out.control(self.IC_OUT, self.scale_out)
        out.toggle(self.IC_MUTE, "Muet", muted,
                   lambda sw, _p: self._set_mute(SINK, sw))

        # Les sorties disponibles prolongent la carte : c'est le même sujet que
        # le curseur juste au-dessus, pas une nouvelle rubrique. Une sortie
        # unique ne mérite pas d'être listée — il n'y a rien à y choisir.
        sinks = list_sinks()
        for name, desc, is_def in (sinks if len(sinks) > 1 else []):
            row = out.action(self.IC_SINK, desc, selected=is_def,
                             on_click=lambda _b, n=name: self._select_sink(n))
            self.sink_rows.append((row, name))

        # ── Micro ──
        if source_exists():
            mvol, mmuted = get_volume(SOURCE)
            mic = self.add_card("Micro")
            self.scale_mic = self._make_scale(mvol, SOURCE)
            mic.control(self.IC_MIC, self.scale_mic)
            self.switch_mic = mic.toggle(
                self.IC_MIC_OFF, "Micro coupé", mmuted,
                lambda sw, _p: self._set_mute(SOURCE, sw), green=True)

            # Symétrique de la sortie : une entrée unique ne mérite pas d'être
            # listée, il n'y a rien à y choisir.
            sources = list_sources()
            for name, desc, is_def in (sources if len(sources) > 1 else []):
                row = mic.action(
                    self.IC_SOURCE, desc, selected=is_def,
                    on_click=lambda _b, n=name: self._select_source(n))
                self.source_rows.append((row, name))

            # VU-mètre : le niveau réel, à vérifier avant de lancer un
            # enregistrement. Empilé sous son propre libellé, il occupe la
            # largeur du curseur qui le surplombe.
            self.meter = MicMeter()
            mic.control(self.IC_LEVEL, self.meter)

        # ── Aller plus loin ──
        more = self.add_card()
        more.action(self.IC_RECORD, "Enregistrer",
                    subtitle="Réunion ou écran",
                    chevron=True, on_click=self._open_record_menu)
        if source_exists():
            more.action(self.IC_FIX, "Réparer le micro",
                        subtitle="Démute et réinitialise l'entrée",
                        on_click=self._fix_mic)
        more.action(self.IC_PREFS, "Réglages avancés", value="pavucontrol",
                    chevron=True, on_click=self._open_pavucontrol)

    # ---- Construction ----

    def _make_scale(self, value, target):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, VOL_MAX, 5)
        scale.set_value(value)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_digits(0)
        scale.add_mark(100, Gtk.PositionType.BOTTOM, None)
        scale.connect("value-changed", self._on_volume_changed, target)
        return scale

    # ---- Handlers ----

    def _on_volume_changed(self, scale, target):
        # Debounce : éviter un appel wpctl à chaque pixel de drag.
        if target in self._timeouts:
            GLib.source_remove(self._timeouts[target])
        self._timeouts[target] = GLib.timeout_add(
            60, self._apply_volume, target, int(scale.get_value()))

    def _apply_volume(self, target, percent):
        self._timeouts.pop(target, None)
        subprocess.Popen(["wpctl", "set-volume", target, "%d%%" % percent],
                         stdout=DEVNULL, stderr=DEVNULL)
        return False

    def _set_mute(self, target, switch):
        subprocess.Popen(["wpctl", "set-mute", target,
                          "1" if switch.get_active() else "0"],
                         stdout=DEVNULL, stderr=DEVNULL)

    def _select_sink(self, name):
        subprocess.run(["pactl", "set-default-sink", name],
                       stdout=DEVNULL, stderr=DEVNULL)
        # Déplacer les flux en cours vers la nouvelle sortie.
        for line in run(["pactl", "list", "short", "sink-inputs"]).splitlines():
            idx = line.split("\t", 1)[0].strip()
            if idx:
                subprocess.run(["pactl", "move-sink-input", idx, name],
                               stdout=DEVNULL, stderr=DEVNULL)
        # La sélection se déplace d'une ligne à l'autre : c'est la classe qui
        # porte l'état, plus besoin de réécrire les libellés pour y coller ou
        # en retirer une coche.
        for row, rname in self.sink_rows:
            ctx = row.get_style_context()
            if rname == name:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _select_source(self, name):
        subprocess.run(["pactl", "set-default-source", name],
                       stdout=DEVNULL, stderr=DEVNULL)
        # Déplacer les flux d'enregistrement en cours vers la nouvelle entrée,
        # comme on le fait pour les flux de lecture à la sortie.
        for line in run(["pactl", "list", "short", "source-outputs"]).splitlines():
            idx = line.split("\t", 1)[0].strip()
            if idx:
                subprocess.run(["pactl", "move-source-output", idx, name],
                               stdout=DEVNULL, stderr=DEVNULL)
        for row, rname in self.source_rows:
            ctx = row.get_style_context()
            if rname == name:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")
        # Le VU-mètre écoute la source par défaut telle qu'elle était au
        # lancement de parec : sans relance, il continuerait d'afficher le
        # niveau de l'entrée qu'on vient justement d'abandonner — et c'est
        # pour vérifier le niveau du NOUVEAU micro qu'on change d'entrée.
        if self.meter is not None:
            self.meter.restart()
        # Le curseur et la coupure visent @DEFAULT_AUDIO_SOURCE@, qui pointe
        # désormais ailleurs : leur position doit suivre.
        self._sync_mic_controls()

    def _open_pavucontrol(self, _btn):
        subprocess.Popen(["pavucontrol"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _fix_mic(self, _btn):
        # Lance le reset à chaud en arrière-plan (le résultat arrive en
        # notification swaync), puis ferme le popup.
        fixmic = os.path.expanduser("~/.local/bin/fix-mic")
        subprocess.Popen([fixmic], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_record_menu(self, _btn):
        # Un popup à la fois : celui-ci se ferme, l'autre prend sa place.
        subprocess.Popen([RECORD_MENU], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(SoundPopup, "waybar-sound-menu")


if __name__ == "__main__":
    main()
