#!/usr/bin/env python3
"""Popup Son pour Waybar (style menu luminosité / notifications).

Actions courantes faites maison :
  - volume haut-parleur (slider) + Muet
  - volume micro (slider) + Micro coupé
  - choix de la sortie et de l'entrée audio (si plusieurs périphériques)
  - enregistrement de réunion (micro + sortie audio, canaux séparés)
Et un bouton « Réglages avancés » qui ouvre pavucontrol (le menu complet).
"""
import array
import os
import re
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from menu_common import (LayerPopup, caption_label,  # noqa: E402
                         run_popup, section_label)

DEVNULL = subprocess.DEVNULL
SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
VOL_MAX = 150  # plafond cohérent avec le scroll waybar (-l 1.5)

# -- Enregistrement de réunion --
RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "voice-recorder.sh")
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
REC_PIDFILE = os.path.join(RUNTIME_DIR, "waybar-voicerec.pid")
REC_PATHFILE = os.path.join(RUNTIME_DIR, "waybar-voicerec.path")

# -- VU-mètre micro (capture PCM légère via parec) --
METER_RATE = 8000          # Hz, mono : largement suffisant pour un niveau visuel
METER_CHUNK = 480          # octets lus par boucle (~30 ms de s16le mono à 8 kHz)
METER_FPS_MS = 40          # période de rafraîchissement de la barre (~25 fps)
METER_DECAY = 0.30         # vitesse de retombée (0 = figé, 1 = instantané)

METER_CSS = b"""
levelbar trough {
    background-color: rgba(255, 255, 255, 0.10);
    border-radius: 4px;
    min-height: 8px;
    padding: 0;
    border: none;
}
levelbar block { border-radius: 4px; }
levelbar block.filled, levelbar block.low { background-color: #32d74b; }
levelbar block.high { background-color: #ffd60a; }
levelbar block.full { background-color: #ff453a; }
levelbar block.empty { background-color: transparent; }
"""


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


def rec_state():
    """Renvoie (en_cours, secondes_écoulées, nom_du_fichier)."""
    try:
        with open(REC_PIDFILE) as f:
            pid = f.read().strip()
        secs = int(subprocess.check_output(
            ["ps", "-o", "etimes=", "-p", pid], text=True, stderr=DEVNULL).strip())
    except Exception:
        return False, 0, ""
    try:
        with open(REC_PATHFILE) as f:
            name = os.path.basename(f.read().strip())
    except OSError:
        name = ""
    return True, secs, name


def fmt_duration(secs):
    if secs >= 3600:
        return "%d:%02d:%02d" % (secs // 3600, secs % 3600 // 60, secs % 60)
    return "%02d:%02d" % (secs // 60, secs % 60)


class SoundPopup(LayerPopup):
    """Trois cartes : ce qui sort, ce qui entre, ce qu'on enregistre.

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
    IC_FOLDER = "\U000f024b"   # dossier
    IC_FIX = "\U000f0709"      # rotation / réinitialisation
    IC_PREFS = "\U000f0493"    # engrenage

    def __init__(self):
        super().__init__("Son", width=360, margin_right=70)
        self._timeouts = {}
        self.sink_rows = []
        self.source_rows = []
        self.switch_mic = None
        self._meter_peak = 0.0       # dernier pic écrit par le thread de capture
        self._meter_shown = 0.0      # valeur affichée (lissée)
        self._meter_stop = threading.Event()
        self._meter_proc = None

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
            level = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            level.pack_start(caption_label("Niveau d'entrée"), False, False, 0)
            self.meter = Gtk.LevelBar.new_for_interval(0.0, 1.0)
            self.meter.set_mode(Gtk.LevelBarMode.CONTINUOUS)
            self.meter.add_offset_value("low", 0.55)
            self.meter.add_offset_value("high", 0.80)
            self.meter.add_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL, 1.0)
            self.meter.set_valign(Gtk.Align.CENTER)
            self._apply_meter_css()
            level.pack_start(self.meter, False, False, 0)
            mic.control(self.IC_LEVEL, level)

            self._start_meter()
            GLib.timeout_add(METER_FPS_MS, self._refresh_meter)
            self.connect("destroy", lambda *_: self._stop_meter())

        # ── Enregistrement de réunion ──
        # Placé sous le micro : c'est le même geste mental (« ce que
        # j'enregistre »), et le VU-mètre juste au-dessus sert de vérification
        # avant de lancer. Hors carte : c'est l'action franche du popup, elle a
        # droit à un bouton plein plutôt qu'à une ligne de liste.
        rec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rec.pack_start(section_label("Enregistrer la réunion"), False, False, 0)
        self.rec_hint = caption_label("")
        rec.pack_start(self.rec_hint, False, False, 0)

        # Libellé facultatif : collé au nom du fichier pour retrouver la
        # réunion plus tard sans avoir à réécouter.
        self.rec_entry = Gtk.Entry()
        self.rec_entry.set_placeholder_text("Nom (facultatif) — ex. point client")
        self.rec_entry.connect("activate", self._toggle_record)
        rec.pack_start(self.rec_entry, False, False, 0)

        self.rec_btn = Gtk.Button()
        self.rec_btn.connect("clicked", self._toggle_record)
        rec.pack_start(self.rec_btn, False, False, 0)
        self.box.pack_start(rec, False, False, 0)

        self._refresh_record()
        GLib.timeout_add_seconds(1, self._refresh_record)

        # ── Aller plus loin ──
        more = self.add_card()
        more.action(self.IC_FOLDER, "Dossier des enregistrements",
                    on_click=self._open_rec_dir)
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
        self._restart_meter()
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

    # ---- Enregistrement de réunion ----

    def _refresh_record(self):
        """Reflète l'état réel du process : le popup n'est pas seul à pouvoir
        lancer ou arrêter (module waybar, raccourci clavier)."""
        if self.rec_btn.get_parent() is None:          # popup détruit
            return GLib.SOURCE_REMOVE
        recording, secs, name = rec_state()
        ctx = self.rec_btn.get_style_context()
        if recording:
            self.rec_btn.set_label("  Arrêter  ·  %s" % fmt_duration(secs))
            ctx.add_class("danger")
            ctx.remove_class("accent")
            self.rec_entry.set_sensitive(False)
            self.rec_hint.set_markup(
                "<span color='#ff453a'>Enregistrement en cours</span> — %s"
                % GLib.markup_escape_text(name))
        else:
            self.rec_btn.set_label("  Démarrer l'enregistrement")
            ctx.add_class("accent")
            ctx.remove_class("danger")
            self.rec_entry.set_sensitive(True)
            self.rec_hint.set_text(
                "Micro et sortie audio sur deux canaux séparés — "
                "la transcription sait qui parle.")
        return GLib.SOURCE_CONTINUE

    def _refresh_record_once(self):
        self._refresh_record()
        return GLib.SOURCE_REMOVE

    def _toggle_record(self, _widget):
        recording, _secs, _name = rec_state()
        if recording:
            subprocess.Popen([RECORDER, "stop"], stdout=DEVNULL, stderr=DEVNULL)
        else:
            label = self.rec_entry.get_text().strip()
            subprocess.Popen([RECORDER, "start", label],
                             stdout=DEVNULL, stderr=DEVNULL)
        # Le démarrage de ffmpeg prend ~0,6 s (le script vérifie qu'il tient) :
        # on laisse passer ce délai avant de relire l'état. UN SEUL tir :
        # _refresh_record renvoie SOURCE_CONTINUE (c'est ce qu'attend le timer
        # périodique d'une seconde posé au montage), donc le rendre directement
        # ici installerait un second timer permanent à chaque clic.
        GLib.timeout_add(900, self._refresh_record_once)

    def _open_rec_dir(self, _btn):
        subprocess.Popen([RECORDER, "dir"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    # ---- VU-mètre micro ----

    def _apply_meter_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(METER_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def _restart_meter(self):
        """Recommence la capture sur la source par défaut du moment."""
        if getattr(self, "meter", None) is None:
            return
        self._stop_meter()
        self._meter_stop.clear()
        self._meter_peak = 0.0
        self._meter_shown = 0.0
        self._start_meter()
        GLib.timeout_add(METER_FPS_MS, self._refresh_meter)

    def _sync_mic_controls(self):
        """Replace curseur et interrupteur sur l'entrée devenue par défaut."""
        vol, muted = get_volume(SOURCE)
        self.scale_mic.handler_block_by_func(self._on_volume_changed)
        self.scale_mic.set_value(vol)
        self.scale_mic.handler_unblock_by_func(self._on_volume_changed)
        if self.switch_mic is not None:
            self.switch_mic.set_active(muted)

    def _start_meter(self):
        try:
            self._meter_proc = subprocess.Popen(
                ["parec", "--format=s16le",
                 "--rate=%d" % METER_RATE, "--channels=1",
                 "--latency-msec=30"],
                stdout=subprocess.PIPE, stderr=DEVNULL)
        except Exception:
            self._meter_proc = None
            return
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _meter_loop(self):
        proc = self._meter_proc
        while not self._meter_stop.is_set() and proc.poll() is None:
            data = proc.stdout.read(METER_CHUNK)
            if not data:
                break
            samples = array.array("h")
            try:
                samples.frombytes(data)
            except (ValueError, EOFError):
                continue
            if not samples:
                continue
            # Retire l'offset DC (certains DMIC ont un décalage constant) pour
            # que le niveau retombe bien à zéro dans le silence.
            dc = sum(samples) / len(samples)
            peak = max((abs(s - dc) for s in samples), default=0) / 32768.0
            self._meter_peak = peak

    def _refresh_meter(self):
        if self._meter_stop.is_set():
            return GLib.SOURCE_REMOVE
        target = self._meter_peak
        if target >= self._meter_shown:
            self._meter_shown = target          # montée instantanée
        else:
            self._meter_shown += (target - self._meter_shown) * METER_DECAY
        self.meter.set_value(min(self._meter_shown, 1.0))
        return GLib.SOURCE_CONTINUE

    def _stop_meter(self):
        self._meter_stop.set()
        if self._meter_proc and self._meter_proc.poll() is None:
            self._meter_proc.terminate()


def main():
    run_popup(SoundPopup, "waybar-sound-menu")


if __name__ == "__main__":
    main()
