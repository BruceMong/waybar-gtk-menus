#!/usr/bin/env python3
"""Popup « Enregistrer l'écran » pour Waybar (vocabulaire commun : cartes + lignes).

Ouvert depuis le menu Son, le clic droit sur l'indicateur d'enregistrement,
SUPER+CTRL+SHIFT+R ou la palette de commandes. Il ne fait que choisir :
  - la zone (région, fenêtre, écran entier),
  - le son (aucun, système, micro, les deux),
  - les options (compte à rebours, GIF, Ne pas déranger),
puis délègue tout à screen-recorder.sh.

Les choix sont écrits dans le fichier de réglages du script à chaque clic :
SUPER+SHIFT+R reprend donc exactement la dernière configuration du menu, sans
qu'il faille l'ouvrir.
"""
import os
import shlex
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, GLib  # noqa: E402

from menu_common import (LayerPopup, caption_label,  # noqa: E402
                         run_popup, section_label)

DEVNULL = subprocess.DEVNULL
RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "screen-recorder.sh")
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "screen-recorder")
PREFS = os.path.join(STATE_DIR, "prefs")
LAST = os.path.join(STATE_DIR, "last")

DEFAULTS = {"ZONE": "region", "AUDIO": "none", "DELAY": "0",
            "FORMAT": "mp4", "DND": "0"}
COUNTDOWN = 3   # secondes, quand le compte à rebours est coché

ZONES = [
    ("region", "\U000f019e", "Région", "Une zone à tracer à la souris"),
    ("window", "\U000f05af", "Fenêtre", "Un clic sur la fenêtre voulue"),
    ("screen", "\U000f0379", "Écran", "L'écran qui a le focus"),
]
AUDIOS = [
    ("none", "\U000f0581", "Muet", "Aucun son"),
    ("system", "\U000f057e", "Système", "Ce qui sort des haut-parleurs"),
    ("mic", "\U000f036c", "Micro", "Le micro par défaut"),
    ("both", "\U000f05cb", "Les deux", "Micro et système mixés — pour commenter une démo"),
]

TILE_CSS = b"""
button.tile {
    background-color: rgba(255, 255, 255, 0.065);
    border-radius: 10px;
    padding: 10px 2px 8px;
}
button.tile:hover { background-color: rgba(255, 255, 255, 0.10); }
button.tile.selected { background-color: rgba(10, 132, 255, 0.22); }
button.tile.selected:hover { background-color: rgba(10, 132, 255, 0.30); }
button.tile:disabled { background-color: rgba(255, 255, 255, 0.03); }
.tile .row-icon { font-size: 20px; }
.tile .row-label { font-size: 12px; }
"""


def load_prefs():
    prefs = dict(DEFAULTS)
    try:
        with open(PREFS) as f:
            for line in f:
                key, _, val = line.strip().partition("=")
                if key in prefs:
                    prefs[key] = shlex.split(val)[0] if val else ""
    except (OSError, ValueError, IndexError):
        pass
    return prefs


def save_prefs(prefs):
    # Le fichier est sourcé par le script shell : valeurs citées, et écriture
    # par renommage pour qu'un SUPER+SHIFT+R simultané ne lise jamais un
    # fichier à moitié écrit.
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = PREFS + ".tmp"
    with open(tmp, "w") as f:
        for key in DEFAULTS:
            f.write("%s=%s\n" % (key, shlex.quote(prefs[key])))
    os.replace(tmp, PREFS)


def rec_state():
    """Renvoie (en_cours, secondes_écoulées, nom_du_fichier)."""
    out = subprocess.run([RECORDER, "status"], capture_output=True,
                         text=True).stdout.split(None, 2)
    if not out or out[0] != "recording":
        return False, 0, ""
    try:
        secs = int(subprocess.check_output(
            ["ps", "-o", "etimes=", "-p", out[1]], text=True,
            stderr=DEVNULL).strip())
    except (subprocess.CalledProcessError, ValueError):
        secs = 0
    name = os.path.basename(out[2].strip()) if len(out) > 2 else ""
    return True, secs, name


def last_recording():
    try:
        with open(LAST) as f:
            path = f.read().strip()
    except OSError:
        return None
    return path if os.path.isfile(path) else None


def fmt_duration(secs):
    if secs >= 3600:
        return "%d:%02d:%02d" % (secs // 3600, secs % 3600 // 60, secs % 60)
    return "%02d:%02d" % (secs // 60, secs % 60)


def fmt_size(path):
    size = os.path.getsize(path)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return ("%d %s" if unit == "o" else "%.1f %s") % (size, unit)
        size /= 1024.0


class ScreenPopup(LayerPopup):
    IC_TIMER = "\U000f13ab"
    IC_GIF = "\U000f0d78"
    IC_DND = "\U000f009b"
    IC_VIDEO = "\U000f0567"
    IC_FOLDER = "\U000f024b"

    def __init__(self):
        super().__init__("Enregistrer l'écran", width=360, margin_right=70)
        self.prefs = load_prefs()
        self.zone_rows = {}
        self.audio_rows = {}
        self._inputs = []       # tout ce qui se fige pendant l'enregistrement

        self._apply_tile_css()

        # ── Zone et son ──
        # Des tuiles côte à côte plutôt que des lignes empilées : sept lignes
        # faisaient dépasser le popup du bas d'un écran de 800 px logiques.
        # Le choix se lit d'un coup d'œil, comme dans une barre d'outils.
        self._add_tiles("Zone", "ZONE", ZONES, self.zone_rows)
        # Le GIF est muet par nature : les tuiles du son restent en place pour
        # garder la mise en page stable, mais se grisent.
        self._add_tiles("Son", "AUDIO", AUDIOS, self.audio_rows)

        # ── Options ──
        opts = self.add_card("Options")
        self.sw_delay = opts.toggle(
            self.IC_TIMER, "Compte à rebours", self.prefs["DELAY"] != "0",
            self._on_delay, subtitle="%d s pour se préparer" % COUNTDOWN)
        self.sw_gif = opts.toggle(
            self.IC_GIF, "Exporter en GIF", self.prefs["FORMAT"] == "gif",
            self._on_gif, subtitle="Muet, 15 i/s, 960 px de large au plus")
        self.sw_dnd = opts.toggle(
            self.IC_DND, "Ne pas déranger", self.prefs["DND"] == "1",
            self._on_dnd, subtitle="Aucune notification dans la vidéo")
        self._inputs += [self.sw_delay, self.sw_gif, self.sw_dnd]

        # ── Action ──
        # Hors carte, comme l'enregistrement de réunion du menu Son : c'est
        # l'action franche du popup, elle a droit à un bouton plein.
        rec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.rec_hint = caption_label("")
        rec.pack_start(self.rec_hint, False, False, 0)
        self.rec_entry = Gtk.Entry()
        self.rec_entry.set_placeholder_text("Nom (facultatif) — ex. démo facture")
        self.rec_entry.connect("activate", self._toggle_record)
        rec.pack_start(self.rec_entry, False, False, 0)
        self._inputs.append(self.rec_entry)
        self.rec_btn = Gtk.Button()
        self.rec_btn.connect("clicked", self._toggle_record)
        rec.pack_start(self.rec_btn, False, False, 0)
        self.box.pack_start(rec, False, False, 0)

        # ── Fichiers ──
        files = self.add_card()
        last = last_recording()
        if last:
            files.action(self.IC_VIDEO, "Dernier enregistrement",
                         subtitle="%s — %s" % (os.path.basename(last),
                                               fmt_size(last)),
                         on_click=self._open_last)
        files.action(self.IC_FOLDER, "Dossier des vidéos", value="~/Videos",
                     on_click=self._open_dir)

        self._sync_audio_sensitivity()
        self._refresh_record()
        GLib.timeout_add_seconds(1, self._refresh_record)

    # ---- Construction ----

    @staticmethod
    def _apply_tile_css():
        provider = Gtk.CssProvider()
        provider.load_from_data(TILE_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def _add_tiles(self, title, key, choices, rows):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.pack_start(section_label(title), False, False, 0)
        strip = Gtk.Box(spacing=6, homogeneous=True)
        for value, icon, name, tip in choices:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(tip)
            ctx = btn.get_style_context()
            ctx.add_class("row")
            ctx.add_class("tile")
            if self.prefs[key] == value:
                ctx.add_class("selected")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            ic = Gtk.Label(label=icon)
            ic.get_style_context().add_class("row-icon")
            lbl = Gtk.Label(label=name)
            lbl.get_style_context().add_class("row-label")
            inner.pack_start(ic, False, False, 0)
            inner.pack_start(lbl, False, False, 0)
            btn.add(inner)
            btn.connect("clicked", lambda _b, k=key, v=value: self._pick(k, v))
            strip.pack_start(btn, True, True, 0)
            rows[value] = btn
            self._inputs.append(btn)
        wrap.pack_start(strip, False, False, 0)
        self.box.pack_start(wrap, False, False, 0)

    # ---- Réglages ----

    def _pick(self, key, value):
        self.prefs[key] = value
        save_prefs(self.prefs)
        rows = self.zone_rows if key == "ZONE" else self.audio_rows
        for k, row in rows.items():
            ctx = row.get_style_context()
            if k == value:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _on_delay(self, sw, _p):
        self.prefs["DELAY"] = str(COUNTDOWN) if sw.get_active() else "0"
        save_prefs(self.prefs)

    def _on_gif(self, sw, _p):
        self.prefs["FORMAT"] = "gif" if sw.get_active() else "mp4"
        save_prefs(self.prefs)
        self._sync_audio_sensitivity()

    def _on_dnd(self, sw, _p):
        self.prefs["DND"] = "1" if sw.get_active() else "0"
        save_prefs(self.prefs)

    def _sync_audio_sensitivity(self):
        recording = rec_state()[0]
        gif = self.prefs["FORMAT"] == "gif"
        for row in self.audio_rows.values():
            row.set_sensitive(not gif and not recording)

    # ---- Enregistrement ----

    def _refresh_record(self):
        """Reflète l'état réel : le raccourci et l'indicateur de la barre
        démarrent et arrêtent aussi, sans passer par ce popup."""
        if self.rec_btn.get_parent() is None:          # popup détruit
            return GLib.SOURCE_REMOVE
        recording, secs, name = rec_state()
        ctx = self.rec_btn.get_style_context()
        for widget in self._inputs:
            widget.set_sensitive(not recording)
        if recording:
            self.rec_btn.set_label("  Arrêter  ·  %s" % fmt_duration(secs))
            ctx.add_class("danger")
            ctx.remove_class("accent")
            self.rec_hint.set_markup(
                "<span color='#ff453a'>Enregistrement en cours</span> — %s"
                % GLib.markup_escape_text(name))
        else:
            self.rec_btn.set_label("  Démarrer l'enregistrement")
            ctx.add_class("accent")
            ctx.remove_class("danger")
            self._sync_audio_sensitivity()
            self.rec_hint.set_text(
                "SUPER+SHIFT+R reprend ces réglages sans ouvrir le menu.")
        return GLib.SOURCE_CONTINUE

    def _toggle_record(self, _widget):
        if rec_state()[0]:
            subprocess.Popen([RECORDER, "stop"], stdout=DEVNULL, stderr=DEVNULL)
            GLib.timeout_add(600, lambda: (self._refresh_record(), False)[1])
            return
        # Le popup se ferme AVANT la sélection : il serait sinon dans la
        # vidéo, et par-dessus la zone qu'on veut tracer. Le script est
        # détaché (setsid) pour survivre à la sortie du popup.
        label = self.rec_entry.get_text().strip()
        subprocess.Popen(["setsid", "-f", RECORDER, "start", label],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_last(self, _btn):
        subprocess.Popen([RECORDER, "open-last"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_dir(self, _btn):
        subprocess.Popen([RECORDER, "dir"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(ScreenPopup, "waybar-screen-menu")


if __name__ == "__main__":
    main()
