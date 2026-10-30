#!/usr/bin/env python3
"""Popup « Enregistrer » pour Waybar (module custom/voice-rec, icône ●).

Tout ce qui sert pendant un call, en un clic : lancer ou arrêter
l'enregistrement de la réunion, vérifier le micro au VU-mètre, masquer le
compteur de la barre (partage d'écran). L'enregistrement d'écran et le
dossier sont un cran plus loin.

Il vivait dans le popup Son, dont il prenait un tiers de la hauteur pour une
action qui ne sert qu'en réunion. Son garde une ligne qui mène ici.

L'enregistrement lui-même est fait par voice-recorder.sh ; ce popup ne fait
que le piloter et relire son état.
"""
import json
import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from menu_common import (LayerPopup, caption_label,  # noqa: E402
                         run_popup, section_label)
from mic_meter import MicMeter  # noqa: E402

DEVNULL = subprocess.DEVNULL
HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(HERE, "voice-recorder.sh")
SCREEN_MENU = os.path.join(HERE, "screen-menu.py")
WIDTH = 360

# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
REC_PIDFILE = os.path.join(RUNTIME_DIR, "waybar-voicerec.pid")
REC_PATHFILE = os.path.join(RUNTIME_DIR, "waybar-voicerec.path")
REC_WATCHPID = os.path.join(RUNTIME_DIR, "waybar-voicerec-watch.pid")
# Préférence et non état : elle doit survivre à la session, d'où
# XDG_STATE_HOME. Lue par voice-status.sh.
REC_HIDEFILE = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "waybar", "voicerec-hide-time")


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


def margin_under_cursor(width, fallback=420):
    """Marge droite qui centre le popup sous le pointeur.

    Les autres popups ont une marge fixe, réglée sur la place de leur module.
    Celui-ci est le premier de modules-right : sa position dépend de tout ce
    qui s'affiche à sa droite (tray, témoins, groupes repliés par autofit),
    une constante tomberait donc à côté. Le clic vient d'avoir lieu sur
    l'icône : le pointeur y est encore.
    """
    try:
        cur = json.loads(subprocess.check_output(
            ["hyprctl", "cursorpos", "-j"], text=True, stderr=DEVNULL))
        mons = json.loads(subprocess.check_output(
            ["hyprctl", "monitors", "-j"], text=True, stderr=DEVNULL))
        x = cur["x"]
        for m in mons:
            # Coordonnées logiques : la largeur du moniteur est en pixels réels.
            right = m["x"] + m["width"] / m["scale"]
            if m["x"] <= x < right:
                return max(10, int(right - x - width / 2))
    except Exception:
        pass
    return fallback


class RecordPopup(LayerPopup):
    IC_HIDE = "\U000f0209"     # œil barré
    IC_LEVEL = ""              # le VU-mètre porte son propre libellé
    IC_SCREEN = "\U000f0567"   # caméra : enregistrement d'écran
    IC_FOLDER = "\U000f024b"   # dossier

    def __init__(self):
        super().__init__("Enregistrer", width=WIDTH,
                         margin_right=margin_under_cursor(WIDTH))

        # ── Réunion ──
        # Hors carte : c'est l'action franche du popup, elle a droit à un
        # bouton plein plutôt qu'à une ligne de liste.
        rec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rec.pack_start(section_label("Réunion"), False, False, 0)
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

        # ── Vérifier avant, discrétion pendant ──
        # Le VU-mètre sert de vérification avant de lancer : un micro coupé ou
        # le mauvais périphérique se voient ici, pas après le call.
        opts = self.add_card()
        self.meter = MicMeter()
        opts.control(self.IC_LEVEL, self.meter)
        # Vaut aussi pendant un enregistrement en cours.
        opts.toggle(self.IC_HIDE, "Masquer la durée dans la barre",
                    os.path.exists(REC_HIDEFILE), self._toggle_hide_time,
                    subtitle="Un point rouge remplace le compteur")

        # ── Aller plus loin ──
        more = self.add_card()
        more.action(self.IC_SCREEN, "Enregistrer l'écran",
                    subtitle="Région, fenêtre ou écran, avec ou sans son",
                    chevron=True, on_click=self._open_screen_menu)
        more.action(self.IC_FOLDER, "Dossier des réunions",
                    on_click=self._open_rec_dir)

        self._refresh_record()
        GLib.timeout_add_seconds(1, self._refresh_record)

    # ---- Enregistrement de réunion ----

    def _refresh_record(self):
        """Reflète l'état réel du process : le popup n'est pas seul à pouvoir
        lancer ou arrêter (clic droit sur l'icône, raccourci clavier)."""
        if self.rec_btn.get_parent() is None:          # popup détruit
            return GLib.SOURCE_REMOVE
        recording, secs, name = rec_state()
        ctx = self.rec_btn.get_style_context()
        if recording:
            self.rec_btn.set_label("  Arrêter  ·  %s" % fmt_duration(secs))
            ctx.add_class("danger")
            ctx.remove_class("accent")
            self.rec_entry.set_sensitive(False)
            self.rec_hint.set_markup(
                "<span color='#ff453a'>Enregistrement en cours</span> — %s"
                % GLib.markup_escape_text(name))
        else:
            self.rec_btn.set_label("  Démarrer l'enregistrement")
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

    def _toggle_hide_time(self, sw, _pspec):
        try:
            if sw.get_active():
                os.makedirs(os.path.dirname(REC_HIDEFILE), exist_ok=True)
                open(REC_HIDEFILE, "w").close()
            else:
                os.remove(REC_HIDEFILE)
        except FileNotFoundError:
            pass
        # Réveille l'indicateur, qui sinon ne relit qu'à son prochain tour.
        try:
            with open(REC_WATCHPID) as f:
                os.kill(int(f.read().strip()), signal.SIGRTMIN + 12)
        except (OSError, ValueError):
            pass

    def _open_screen_menu(self, _btn):
        # Un popup à la fois : celui-ci se ferme, l'autre prend sa place.
        subprocess.Popen([SCREEN_MENU], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_rec_dir(self, _btn):
        subprocess.Popen([RECORDER, "dir"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(RecordPopup, "waybar-record-menu")


if __name__ == "__main__":
    main()
