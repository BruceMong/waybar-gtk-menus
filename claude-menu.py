#!/usr/bin/env python3
"""Popup Sessions Claude Code pour Waybar (style menu luminosité).

Vue groupée par projet de toutes les sessions Claude Code en cours. Chaque
groupe rappelle l'état git du dépôt et donne accès au projet (lazygit, VS
Code, Thunar) ; chaque session affiche son titre, son état, son workspace,
son contexte consommé et son dernier prompt.

Actions disponibles :
  - clic sur une session    : bascule sur son workspace et lui donne le focus
  - 󰍡 Répondre              : saisit une réponse et l'envoie dans la session
  - 󰅖 Arrêter               : termine la session (double clic de confirmation)
  - 󰘖 Rassembler            : ramène les sessions à traiter sur ce bureau

Les données viennent des hooks (~/.claude/hooks/claude-session-lib.sh) et des
transcripts, via claude_sessions_data.py.
"""
import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from menu_common import LayerPopup  # noqa: E402
from claude_sessions_data import (  # noqa: E402
    human_tokens, humanize, load_sessions, short_model,
)

import time  # noqa: E402


COLORS = {
    "waiting": "#ff9f0a",   # peach — réclame une action
    "done": "#32d74b",      # green — terminé, à relire
    "running": "#5e9cff",   # lavender — travaille
    "idle": "#9a9aa2",      # subtext0 — au repos
}
ICONS = {"waiting": "", "done": "", "running": "", "idle": ""}
LABELS = {
    "waiting": "attend une action",
    "done": "terminé",
    "running": "travaille",
    "idle": "au repos",
}
DIM = "#9a9aa2"
FAINT = "#68686f"

POPUP_WIDTH = 560
# Largeur maximale des libellés, en caractères. Indispensable : un label
# ellipsizé sans cette limite demande sa largeur naturelle et élargit la
# fenêtre bien au-delà de POPUP_WIDTH.
WIDTH_CHARS = 42

# Styles propres au popup, en complément de ceux de menu_common.
EXTRA_CSS = b"""
button.session {
    background-color: rgba(255, 255, 255, 0.09);
    padding: 8px 10px;
    border-radius: 8px;
}
button.session:hover { background-color: rgba(255, 255, 255, 0.16); }
button.session.waiting { box-shadow: inset 3px 0 0 #ff9f0a; }
button.session.done    { box-shadow: inset 3px 0 0 #32d74b; }
button.session.running { box-shadow: inset 3px 0 0 #5e9cff; }
button.session.idle    { box-shadow: inset 3px 0 0 #68686f; }

button.mini {
    background-color: transparent;
    padding: 2px 8px;
    min-height: 22px;
    font-size: 12px;
    color: #9a9aa2;
}
button.mini:hover { background-color: rgba(255, 255, 255, 0.16); color: #ebebf0; }
button.danger:hover { background-color: #ff453a; color: #ffffff; }
button.armed { background-color: #ff453a; color: #ffffff; }

entry {
    background-color: rgba(0, 0, 0, 0.28);
    color: #ebebf0;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 4px 8px;
}
entry:focus { border-color: #ff9f0a; }

progressbar trough {
    background-color: rgba(255, 255, 255, 0.09);
    border-radius: 3px;
    min-height: 4px;
    border: none;
}
progressbar progress { border-radius: 3px; min-height: 4px; }
progressbar.ctx-ok progress   { background-color: #32d74b; }
progressbar.ctx-warn progress { background-color: #ff9f0a; }
progressbar.ctx-full progress { background-color: #ff453a; }

separator { background-color: rgba(255, 255, 255, 0.09); min-height: 1px; }
"""


def esc(text):
    return GLib.markup_escape_text(str(text or ""))


def run_detached(argv):
    """Lance une commande sans bloquer le popup ni mourir avec lui."""
    try:
        subprocess.Popen(argv, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def hypr(*args):
    subprocess.run(["hyprctl", "dispatch", *args], capture_output=True)


class ClaudeMenu(LayerPopup):
    def __init__(self, sessions):
        super().__init__("Sessions Claude Code", width=POPUP_WIDTH,
                         margin_right=60)
        self._apply_extra_css()
        self._build(sessions)

    @staticmethod
    def _apply_extra_css():
        provider = Gtk.CssProvider()
        provider.load_from_data(EXTRA_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    # -- Construction -------------------------------------------------------
    def _build(self, sessions):
        if not sessions:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<span foreground='{DIM}'>Aucune session en cours.</span>")
            self.box.pack_start(lbl, False, False, 0)
            return

        self.box.pack_start(self._summary(sessions), False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(620)
        # La largeur de la fenêtre est un minimum pour GTK, pas un maximum :
        # sans cette contrainte, le contenu le plus long l'impose à tous.
        scroller.set_propagate_natural_width(False)
        scroller.set_max_content_width(POPUP_WIDTH - 40)
        scroller.set_size_request(POPUP_WIDTH - 40, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        now = time.time()
        for project, group in self._group_by_project(sessions):
            content.pack_start(self._project_block(project, group, now),
                               False, False, 0)
        scroller.add(content)
        self.box.pack_start(scroller, True, True, 0)

    @staticmethod
    def _group_by_project(sessions):
        """Projets ordonnés par urgence de leur session la plus pressante."""
        groups = {}
        for s in sessions:
            groups.setdefault(s.get("dir", "?"), []).append(s)
        # `sessions` arrive déjà trié par urgence : le premier de chaque groupe
        # donne donc le rang du projet.
        return sorted(groups.items(),
                      key=lambda kv: sessions.index(kv[1][0]))

    def _summary(self, sessions):
        row = Gtk.Box(spacing=8)
        attention = [s for s in sessions
                     if s.get("status") in ("waiting", "done")]

        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(
            f"<span foreground='{DIM}' size='small'>{len(sessions)} session(s) — "
            f"<span foreground='{COLORS['waiting']}'>{len(attention)}</span>"
            f" à traiter</span>"
        )
        row.pack_start(lbl, True, True, 0)

        if attention:
            btn = Gtk.Button(label="󰘖 Rassembler")
            btn.get_style_context().add_class("mini")
            btn.set_tooltip_text(
                "Ramener les sessions à traiter sur le bureau courant"
            )
            btn.connect("clicked", self._on_gather, attention)
            row.pack_end(btn, False, False, 0)
        return row

    def _project_block(self, project, group, now):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        header = Gtk.Box(spacing=6)
        branch = group[0].get("git_branch")
        changes = group[0].get("git_changes")
        detail = ""
        if branch:
            colour = COLORS["waiting"] if changes else DIM
            # La branche est tronquée ici plutôt que par l'ellipse du label :
            # sinon c'est le compteur de modifications, en bout de ligne, qui
            # disparaît — or c'est l'information la plus utile des deux.
            short_branch = branch if len(branch) <= 12 else branch[:11] + "…"
            detail = (f"  <span foreground='{FAINT}'>󰘬</span> "
                      f"<span foreground='{DIM}'>{esc(short_branch)}</span>")
            if changes:
                detail += (f" <span foreground='{colour}'>"
                           f"({changes} modif{'s' if changes > 1 else ''})</span>")

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{esc(project)}</b>{detail}")
        title.set_ellipsize(3)
        title.set_max_width_chars(WIDTH_CHARS - 4)  # place pour les 3 icônes
        header.pack_start(title, True, True, 0)

        cwd = group[0].get("cwd") or ""
        # pack_end empile de droite à gauche : on inverse pour obtenir
        # lazygit, VS Code puis Thunar dans cet ordre à l'écran.
        for icon, tip, argv in reversed((
            ("󰊢", "Ouvrir lazygit", ["kitty", "--class", "waybar.modules",
                                     "-T", f"lazygit — {project}",
                                     "lazygit", "-p", cwd]),
            ("󰨞", "Ouvrir VS Code", ["code", cwd]),
            ("󰉋", "Ouvrir Thunar", ["thunar", cwd]),
        )):
            btn = Gtk.Button(label=icon)
            btn.get_style_context().add_class("mini")
            btn.set_tooltip_text(tip)
            btn.set_sensitive(bool(cwd) and os.path.isdir(cwd))
            btn.connect("clicked", lambda _b, a=argv: (run_detached(a),
                                                       self.close()))
            header.pack_end(btn, False, False, 0)
        box.pack_start(header, False, False, 0)

        for session in group:
            box.pack_start(self._session_row(session, now), False, False, 0)
        return box

    def _session_row(self, session, now):
        status = session.get("status", "idle")
        colour = COLORS.get(status, "#ebebf0")
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # -- Ligne principale : le clic saute sur la session --
        btn = Gtk.Button()
        btn.get_style_context().add_class("session")
        btn.get_style_context().add_class(status)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)

        top = Gtk.Label(xalign=0)
        title = session.get("title") or session.get("dir") or "session"
        ws = session.get("workspace")
        ws_part = (f"   <span foreground='{FAINT}' size='small'>ws {esc(ws)}</span>"
                   if ws else "")
        top.set_markup(
            f"<span foreground='{colour}'>{ICONS.get(status, '')}</span>  "
            f"<b>{esc(title)}</b>{ws_part}"
        )
        # Sans max_width_chars, un label ellipsizé réclame quand même sa
        # largeur naturelle et fait gonfler toute la fenêtre.
        top.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        top.set_max_width_chars(WIDTH_CHARS)
        inner.pack_start(top, False, False, 0)

        meta = Gtk.Label(xalign=0)
        bits = [f"<span foreground='{colour}'>{LABELS.get(status, status)}</span>",
                humanize(now - session.get("ts", now))]
        model = short_model(session.get("model"))
        if model:
            bits.append(esc(model))
        mode = session.get("permission_mode")
        if mode and mode != "normal":
            bits.append(f"<span foreground='{COLORS['waiting']}'>{esc(mode)}</span>")
        meta.set_markup(
            f"<span foreground='{DIM}' size='small'>{' · '.join(bits)}</span>"
        )
        inner.pack_start(meta, False, False, 0)

        # -- Contexte consommé --
        used = session.get("context") or 0
        limit = session.get("context_limit") or 0
        if used and limit:
            ratio = min(1.0, used / limit)
            bar = Gtk.ProgressBar()
            bar.set_fraction(ratio)
            cls = ("ctx-full" if ratio >= 0.85
                   else "ctx-warn" if ratio >= 0.6 else "ctx-ok")
            bar.get_style_context().add_class(cls)
            bar.set_tooltip_text(
                f"Contexte : {used:,} / {limit:,} tokens ({ratio * 100:.0f} %)"
                .replace(",", " ")
            )
            ctx_row = Gtk.Box(spacing=6)
            ctx_lbl = Gtk.Label(xalign=0)
            ctx_lbl.set_markup(
                f"<span foreground='{FAINT}' size='small'>"
                f"{human_tokens(used)}/{human_tokens(limit)}</span>"
            )
            ctx_row.pack_start(bar, True, True, 0)
            ctx_row.pack_end(ctx_lbl, False, False, 0)
            bar.set_valign(Gtk.Align.CENTER)
            inner.pack_start(ctx_row, False, False, 2)

        prompt = (session.get("last_prompt") or "").strip().replace("\n", " ")
        if prompt:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(
                f"<span foreground='{FAINT}' size='small'>{esc(prompt[:110])}</span>"
            )
            lbl.set_ellipsize(3)
            lbl.set_max_width_chars(WIDTH_CHARS)
            inner.pack_start(lbl, False, False, 0)

        btn.add(inner)
        addr = session.get("addr", "")
        btn.connect("clicked", self._on_jump, addr)
        if not addr:
            btn.set_sensitive(False)
            btn.set_tooltip_text("Fenêtre introuvable (session détachée ?)")
        wrap.pack_start(btn, False, False, 0)

        # -- Barre d'actions --
        wrap.pack_start(self._actions(session), False, False, 0)
        return wrap

    def _actions(self, session):
        row = Gtk.Box(spacing=4)
        row.set_margin_start(6)

        reveal = Gtk.Revealer()
        reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        reply = Gtk.Button(label="󰍡 Répondre")
        reply.get_style_context().add_class("mini")
        reply.set_tooltip_text("Écrire une réponse et l'envoyer à cette session")
        reply.set_sensitive(bool(session.get("addr")))
        reply.connect("clicked", lambda _b: self._toggle_reply(reveal))
        row.pack_start(reply, False, False, 0)

        kill = Gtk.Button(label="󰅖 Arrêter")
        kill.get_style_context().add_class("mini")
        kill.get_style_context().add_class("danger")
        kill.set_tooltip_text("Terminer la session (un second clic confirme)")
        kill._armed = False
        kill.connect("clicked", self._on_kill, session)
        row.pack_end(kill, False, False, 0)

        # Zone de saisie révélée par « Répondre ».
        entry_box = Gtk.Box(spacing=4)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Réponse à envoyer…")
        entry.connect("activate", self._on_send, session, entry)
        send = Gtk.Button(label="󰒊")
        send.get_style_context().add_class("mini")
        send.set_tooltip_text("Envoyer")
        send.connect("clicked", lambda _b: self._on_send(None, session, entry))
        entry_box.pack_start(entry, True, True, 0)
        entry_box.pack_end(send, False, False, 0)
        entry_box.set_margin_top(4)
        reveal.add(entry_box)
        reveal._entry = entry

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrap.pack_start(row, False, False, 0)
        wrap.pack_start(reveal, False, False, 0)
        return wrap

    # -- Actions ------------------------------------------------------------
    def _toggle_reply(self, revealer):
        opening = not revealer.get_reveal_child()
        revealer.set_reveal_child(opening)
        if opening:
            revealer._entry.grab_focus()

    def _on_jump(self, _btn, addr):
        if addr:
            hypr("focuswindow", f"address:{addr}")
        self.close()

    def _on_gather(self, _btn, sessions):
        """Ramène les fenêtres à traiter sur le bureau courant."""
        try:
            out = subprocess.run(["hyprctl", "activeworkspace", "-j"],
                                 capture_output=True, text=True, timeout=2).stdout
            import json
            ws = json.loads(out).get("id")
        except Exception:
            ws = None
        if ws is None:
            return
        for s in sessions:
            if s.get("addr"):
                hypr("movetoworkspacesilent", f"{ws},address:{s['addr']}")
        self.close()

    def _on_kill(self, btn, session):
        """Premier clic : arme le bouton. Second clic : termine la session."""
        if not btn._armed:
            btn._armed = True
            btn.set_label("󰅖 Confirmer ?")
            btn.get_style_context().add_class("armed")
            GLib.timeout_add_seconds(4, self._disarm, btn)
            return
        pid = session.get("pid")
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            pass
        self.close()

    @staticmethod
    def _disarm(btn):
        if btn.get_realized() and btn._armed:
            btn._armed = False
            btn.set_label("󰅖 Arrêter")
            btn.get_style_context().remove_class("armed")
        return False  # ne pas répéter

    def _on_send(self, _widget, session, entry):
        """Envoie le texte dans la session : focus, puis frappe via wtype.

        Le texte passe par l'environnement plutôt que par la ligne de commande :
        aucun risque d'interprétation par le shell, quels que soient les
        guillemets ou accents saisis.
        """
        text = entry.get_text().strip()
        addr = session.get("addr")
        if not text or not addr:
            return
        env = dict(os.environ, CS_REPLY=text, CS_ADDR=addr)
        script = (
            'hyprctl dispatch focuswindow "address:$CS_ADDR" >/dev/null; '
            'sleep 0.35; '
            'wtype -- "$CS_REPLY"; '
            'sleep 0.15; '
            'wtype -k Return'
        )
        try:
            subprocess.Popen(["bash", "-c", script], env=env,
                             start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass
        self.close()


def main():
    ClaudeMenu(load_sessions(enrich=True)).run()


if __name__ == "__main__":
    main()
