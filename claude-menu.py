#!/usr/bin/env python3
"""Popup Sessions Claude Code pour Waybar — vocabulaire Control Center macOS.

Vue groupée par projet de toutes les sessions Claude Code en cours. Un projet
est un intertitre repliable ; ses sessions sont les lignes d'une même carte,
séparées d'un filet — le même matériau que les autres popups de la barre.

Ce que la fenêtre cherche à dire, dans cet ordre :
  1. combien de sessions réclament une action,
  2. lesquelles, et depuis combien de temps,
  3. ce qu'elles font, sur quel modèle, avec quelle réserve de contexte.

Le reste — répondre, arrêter, reprendre, ouvrir le projet — n'apparaît qu'au
survol ou au focus clavier : dix sessions traînant chacune leur barre d'actions
pesaient plus lourd à l'écran que leur propre contenu.

Navigation :
  - le champ de filtre a le focus à l'ouverture ; taper réduit la liste,
    Entrée saute sur la première session restante
  - clic sur une session      : bascule sur son workspace et lui donne le focus
  - 󰍡  : saisir une réponse et l'envoyer dans la session
  - 󰑓  : rouvrir un terminal sur une session détachée (claude --resume)
  - 󰅖  : terminer la session (un second clic confirme)
  - 󰘖 Rassembler : ramène les sessions à traiter sur ce bureau

Les données viennent des hooks (~/.claude/hooks/claude-session-lib.sh) et des
transcripts, via claude_sessions_data.py. Le contenu se rafraîchit tout seul :
une session qui termine pendant que le popup est ouvert change d'état à
l'écran, sans qu'il faille le refermer.
"""
import os
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from menu_common import Card, LayerPopup, run_popup  # noqa: E402
from claude_sessions_data import (  # noqa: E402
    human_tokens, humanize, load_sessions, short_model,
)


# Palette système macOS, celle des autres popups.
COLORS = {
    "waiting": "#ff9f0a",   # orange — réclame une action
    "done": "#32d74b",      # vert — terminé, à relire
    "running": "#0a84ff",   # bleu accent — travaille
    "idle": "#8e8e93",      # gris — au repos
}
ICONS = {"waiting": "", "done": "",
         "running": "", "idle": ""}
# L'état et son ancienneté disent la même chose : autant les dire d'une
# phrase. « attend une action · 18min » se lisait comme deux informations.
PHRASES = {
    "waiting": "attend depuis {age}",
    "done": "terminé il y a {age}",
    "running": "travaille depuis {age}",
    "idle": "au repos depuis {age}",
}
# Le markup Pango ne connaît pas rgba() : ces gris sont les équivalents
# opaques de #ebebf5 à 55 % et 34 % par-dessus le fond d'une carte.
DIM = "#94949a"
FAINT = "#6c6c72"
STALE = "#ff453a"

POPUP_WIDTH = 560
# Largeur maximale des libellés, en caractères. Indispensable : un label
# ellipsizé sans cette limite demande sa largeur naturelle et élargit la
# fenêtre bien au-delà de POPUP_WIDTH.
WIDTH_CHARS = 44
# Au-delà, une session en attente n'attend plus : elle a été oubliée.
STALE_AFTER = 2 * 3600
# En deçà, un champ de filtre coûterait plus d'attention qu'il n'en fait
# gagner.
FILTER_FROM = 5
REFRESH_SECONDS = 4

EXTRA_CSS = """
/* Une session ne se plie pas au vocabulaire « icône · libellé · valeur » des
   autres popups : c'est un titre, un état, une réserve de contexte et un
   dernier prompt — quatre informations de nature différente, empilées. Ce qui
   s'aligne, ce sont les matériaux : mêmes aplats, mêmes rayons, mêmes gris que
   les cartes du reste de la barre.

   La session n'a donc pas de fond propre : elle est transparente sur sa carte,
   comme une `.row`, et ne s'allume qu'au survol. L'accent coloré sur la
   tranche gauche reste la signature du menu — c'est lui qui dit d'un coup
   d'œil laquelle attend une réponse. */
button.session {
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 0;
    padding: 9px 12px 9px 13px;
    transition: background-color 110ms ease-out;
}
button.session:hover  { background-color: rgba(255, 255, 255, 0.10); }
button.session:active { background-color: rgba(255, 255, 255, 0.15); }
button.session:disabled { background-color: transparent; }
.card > button.session:first-child { border-radius: 10px 10px 0 0; }
.card > button.session:last-child  { border-radius: 0 0 10px 10px; }
.card > button.session:only-child  { border-radius: 10px; }

/* La tranche d'état. Posée en box-shadow plutôt qu'en bordure : elle suit les
   coins arrondis des lignes de tête et de pied, et ne décale pas le texte. */
button.session.waiting { box-shadow: inset 3px 0 0 #ff9f0a; }
button.session.done    { box-shadow: inset 3px 0 0 #32d74b; }
button.session.running { box-shadow: inset 3px 0 0 #0a84ff; }
button.session.idle    { box-shadow: inset 3px 0 0 rgba(235, 235, 245, 0.22); }

/* Intertitre de projet : un bouton qui n'en a pas l'air. Le chevron et les
   raccourcis du dépôt sont son seul relief. */
button.project {
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    padding: 2px 4px;
    min-height: 20px;
    border-radius: 6px;
}
button.project:hover { background-color: rgba(255, 255, 255, 0.05); }

/* Actions d'une session ou d'un projet : des glyphes, pas des libellés. Elles
   ne se montrent qu'au survol — dix boutons « Répondre » alignés pesaient plus
   lourd que les dix titres qu'ils accompagnaient. */
button.glyph {
    background-color: transparent;
    background-image: none;
    padding: 0;
    min-width: 26px;
    min-height: 26px;
    border-radius: 13px;
    font-size: 13px;
    color: rgba(235, 235, 245, 0.62);
    transition: background-color 110ms ease-out, color 110ms ease-out;
}
button.glyph:hover {
    background-color: rgba(255, 255, 255, 0.16);
    color: #ffffff;
}
button.glyph:disabled { color: rgba(235, 235, 245, 0.20); }
button.glyph.danger:hover { background-color: #ff453a; color: #ffffff; }
/* Glyphe accompagné d'un libellé : il lui faut ses marges horizontales. */
button.glyph.pill { padding: 0 10px; min-width: 0; font-size: 12px; }

/* Armé : le second clic termine la session. Le bouton s'élargit pour porter
   son libellé — on ne détruit rien sur une icône muette. */
button.glyph.armed {
    background-color: #ff453a;
    color: #ffffff;
    padding: 0 10px;
    font-size: 11px;
    font-weight: 600;
}

/* Réserve de contexte : même trame que les rails de curseur, en plus fin. Une
   jauge pleine largeur criait plus fort que l'état de la session. */
progressbar.ctx { min-height: 3px; }
progressbar.ctx trough {
    background-color: rgba(255, 255, 255, 0.13);
    border-radius: 2px;
    min-height: 3px;
    border: none;
}
progressbar.ctx progress { border-radius: 2px; min-height: 3px; }
progressbar.ctx.ctx-ok progress   { background-color: rgba(235, 235, 245, 0.45); }
progressbar.ctx.ctx-warn progress { background-color: #ff9f0a; }
progressbar.ctx.ctx-full progress { background-color: #ff453a; }

/* Le champ de filtre porte l'icône de recherche du thème système. */
entry.search, entry.reply {
    border-radius: 8px;
    min-height: 20px;
    padding: 6px 8px;
}
entry.search image { color: rgba(235, 235, 245, 0.45); }

.empty { color: rgba(235, 235, 245, 0.45); }
""".encode()


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
    """Dispatch Hyprland, en syntaxe Lua.

    hyprland.lua etant la config active, `hyprctl dispatch` evalue son argument
    comme du code Lua (enveloppe dans hl.dispatch(...)). Les formes hyprlang
    `focuswindow address:0x..` / `movetoworkspacesilent 3,address:0x..` ne
    parsent plus et echouaient silencieusement.
    """
    cmd = args[0]
    arg = args[1] if len(args) > 1 else ""
    if cmd == "focuswindow":
        lua = 'hl.dsp.focus({ window = "%s" })' % arg
    elif cmd == "movetoworkspacesilent":
        ws, _, win = arg.partition(",")
        lua = ('hl.dsp.window.move({ workspace = "%s", window = "%s", '
               'silent = true })' % (ws, win))
    else:
        raise ValueError("dispatcher non traduit: %r" % (args,))
    subprocess.run(["hyprctl", "dispatch", lua], capture_output=True)


def state_phrase(status, age_seconds):
    """« attend depuis 18min » — l'état et son ancienneté d'une seule voix."""
    return PHRASES.get(status, "{age}").format(age=humanize(age_seconds))


def glyph_button(icon, tooltip, handler=None, danger=False):
    btn = Gtk.Button(label=icon)
    ctx = btn.get_style_context()
    ctx.add_class("glyph")
    if danger:
        ctx.add_class("danger")
    btn.set_tooltip_text(tooltip)
    btn.set_valign(Gtk.Align.CENTER)
    if handler is not None:
        btn.connect("clicked", handler)
    return btn


class HoverActions:
    """Révèle une grappe de boutons au survol ou au focus clavier.

    Deux sources concourent, et aucune ne suffit seule : la souris (survol de
    la ligne) et le clavier (le focus posé sur la ligne ou sur l'un des
    boutons révélés). D'où le jeu de drapeaux — cacher les actions parce que
    la souris a quitté la ligne alors que le focus est resté sur « Répondre »
    ferait disparaître le bouton sous le doigt.
    """

    def __init__(self, revealer):
        self.revealer = revealer
        self._flags = {}

    def watch(self, widget, hover=True):
        key = id(widget)
        self._flags.setdefault(key, {"hover": False, "focus": False})
        widget.connect("focus-in-event", self._set, key, "focus", True)
        widget.connect("focus-out-event", self._set, key, "focus", False)
        if hover:
            widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK
                              | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            widget.connect("enter-notify-event", self._cross, key, True)
            widget.connect("leave-notify-event", self._cross, key, False)
        return widget

    def _cross(self, _w, event, key, value):
        # Entrer dans un enfant (le bouton d'action lui-même) fait sortir du
        # parent : sans ce filtre, la grappe se refermerait sous le pointeur.
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._flags[key]["hover"] = value
        self._sync()
        return False

    def _set(self, _w, _event, key, field, value):
        self._flags[key][field] = value
        self._sync()
        return False

    def _sync(self):
        self.revealer.set_reveal_child(
            any(any(f.values()) for f in self._flags.values())
        )


class ClaudeMenu(LayerPopup):
    def __init__(self, sessions):
        super().__init__("Sessions Claude Code", width=POPUP_WIDTH,
                         margin_right=60)
        self._apply_extra_css()
        self.sessions = sessions
        self.filter_text = ""
        # Un projet dont rien ne réclame d'attention s'ouvre replié : trois
        # sessions au repos n'ont pas à pousser hors de l'écran celle qui
        # attend une réponse.
        self.collapsed = {}
        self._rows = []          # (session, widgets) pour le rafraîchissement
        self._open_replies = set()
        self._alive = True
        self._filter_entry = None
        self._body = None
        self._summary_label = None
        self._gather_btn = None

        self.connect("destroy", lambda *_: setattr(self, "_alive", False))
        self._build_shell()
        self._populate()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)

    @staticmethod
    def _apply_extra_css():
        provider = Gtk.CssProvider()
        provider.load_from_data(EXTRA_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    # -- Ossature ----------------------------------------------------------
    def _build_shell(self):
        """Ce qui ne bouge pas d'un rafraîchissement à l'autre.

        Le filtre et la ligne de résumé survivent aux reconstructions : les
        replacer à chaque tick reviendrait à effacer la saisie en cours.
        """
        self._filter_entry = Gtk.SearchEntry()
        self._filter_entry.get_style_context().add_class("search")
        self._filter_entry.set_placeholder_text("Filtrer par projet, titre…")
        self._filter_entry.connect("search-changed", self._on_filter)
        self._filter_entry.connect("activate", self._on_filter_activate)
        self.box.pack_start(self._filter_entry, False, False, 0)

        row = Gtk.Box(spacing=8)
        row.set_margin_start(4)
        row.set_margin_end(2)
        self._summary_label = Gtk.Label(xalign=0)
        row.pack_start(self._summary_label, True, True, 0)
        self._gather_btn = Gtk.Button(label="󰘖 Rassembler")
        self._gather_btn.get_style_context().add_class("glyph")
        self._gather_btn.get_style_context().add_class("pill")
        self._gather_btn.set_size_request(-1, 24)
        self._gather_btn.set_tooltip_text(
            "Ramener les sessions à traiter sur le bureau courant"
        )
        self._gather_btn.connect("clicked", self._on_gather)
        row.pack_end(self._gather_btn, False, False, 0)
        self._summary_row = row
        self.box.pack_start(row, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_overlay_scrolling(True)
        scroller.set_max_content_height(600)
        # La largeur de la fenêtre est un minimum pour GTK, pas un maximum :
        # sans cette contrainte, le contenu le plus long l'impose à tous.
        scroller.set_propagate_natural_width(False)
        scroller.set_max_content_width(POPUP_WIDTH - 28)
        scroller.set_size_request(POPUP_WIDTH - 28, -1)
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroller.add(self._body)
        self.box.pack_start(scroller, True, True, 0)

    def _populate(self):
        for child in self._body.get_children():
            child.destroy()
        self._rows = []

        visible = self._visible_sessions()
        self._update_summary(visible)
        self._filter_entry.set_visible(len(self.sessions) >= FILTER_FROM)
        self._filter_entry.set_no_show_all(len(self.sessions) < FILTER_FROM)

        if not self.sessions:
            self._body.pack_start(self._placeholder("Aucune session en cours."),
                                  False, False, 0)
        elif not visible:
            self._body.pack_start(
                self._placeholder("Aucune session ne correspond au filtre."),
                False, False, 0)
        else:
            now = time.time()
            for cwd, group in self._group_by_project(visible):
                self._body.pack_start(self._project_block(cwd, group, now),
                                      False, False, 0)
        self._body.show_all()

    @staticmethod
    def _placeholder(text):
        lbl = Gtk.Label(xalign=0, label=text)
        lbl.get_style_context().add_class("empty")
        lbl.set_margin_top(6)
        lbl.set_margin_start(4)
        return lbl

    def _visible_sessions(self):
        needle = self.filter_text.strip().lower()
        if not needle:
            return list(self.sessions)
        return [s for s in self.sessions if needle in " ".join(
            str(s.get(k) or "") for k in
            ("dir", "cwd", "title", "last_prompt", "git_branch")
        ).lower()]

    def _update_summary(self, visible):
        attention = [s for s in visible
                     if s.get("status") in ("waiting", "done")]
        total = len(visible)
        if attention:
            text = (f"<span foreground='{COLORS['waiting']}'>{len(attention)}"
                    f"</span> à traiter sur {total}")
        else:
            text = f"{total} session{'s' if total > 1 else ''}, rien à traiter"
        self._summary_label.set_markup(
            f"<span foreground='{DIM}' size='small'>{text}</span>")
        self._gather_btn.set_sensitive(bool(attention))
        self._gather_attention = attention

    @staticmethod
    def _group_by_project(sessions):
        """Projets ordonnés par urgence de leur session la plus pressante.

        La clé est le chemin complet, pas son basename : deux dépôts homonymes
        rangés dans des arbres différents se retrouvaient fondus dans un seul
        bloc, sous l'état git du premier des deux.
        """
        rank = {id(s): i for i, s in enumerate(sessions)}
        groups = {}
        for s in sessions:
            groups.setdefault(s.get("cwd") or s.get("dir", "?"), []).append(s)
        # `sessions` arrive déjà trié par urgence : le premier de chaque groupe
        # donne donc le rang du projet.
        return sorted(groups.items(), key=lambda kv: rank[id(kv[1][0])])

    # -- Bloc projet -------------------------------------------------------
    def _project_block(self, cwd, group, now):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name = group[0].get("dir") or os.path.basename(cwd) or "?"

        # Replié par défaut quand rien n'y réclame d'attention — mais jamais
        # contre une décision déjà prise par l'utilisateur.
        quiet = all(s.get("status") == "idle" for s in group)
        collapsed = self.collapsed.setdefault(cwd, quiet and len(group) > 1)

        header = Gtk.Box(spacing=4)
        header.set_margin_start(2)
        header.set_margin_end(2)

        toggle = Gtk.Button()
        toggle.get_style_context().add_class("project")
        head_lbl = Gtk.Label(xalign=0)
        head_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        head_lbl.set_max_width_chars(WIDTH_CHARS - 6)
        toggle.add(head_lbl)
        header.pack_start(toggle, True, True, 0)

        # Les raccourcis du dépôt ne servent qu'une fois le projet identifié :
        # ils attendent le survol plutôt que d'occuper la ligne en permanence.
        actions = Gtk.Revealer()
        actions.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        actions.set_transition_duration(90)
        arow = Gtk.Box(spacing=0)
        hover = HoverActions(actions)
        for icon, tip, argv in (
            ("󰊢", "Ouvrir lazygit", ["kitty", "--class", "waybar.modules",
                                     "-T", f"lazygit — {name}",
                                     "lazygit", "-p", cwd]),
            ("󰨞", "Ouvrir VS Code", ["code", cwd]),
            ("󰉋", "Ouvrir Thunar", ["thunar", cwd]),
        ):
            btn = glyph_button(icon, tip,
                               lambda _b, a=argv: (run_detached(a), self.close()))
            btn.set_sensitive(bool(cwd) and os.path.isdir(cwd))
            arow.pack_start(hover.watch(btn), False, False, 0)
        actions.add(arow)
        header.pack_end(actions, False, False, 0)
        hover.watch(toggle)
        hover.watch(header, hover=True)
        wrap.pack_start(header, False, False, 0)

        card = Card()
        reveal = Gtk.Revealer()
        reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        reveal.set_transition_duration(140)
        reveal.add(card)
        reveal.set_reveal_child(not collapsed)
        wrap.pack_start(reveal, False, False, 0)

        for i, session in enumerate(group):
            card.add_row(self._session_row(session, now), separator=i > 0)

        def render_head(*_):
            chevron = "󰅀" if reveal.get_reveal_child() else "󰅂"
            branch = group[0].get("git_branch")
            changes = group[0].get("git_changes") or 0
            detail = ""
            if branch:
                # La branche est tronquée ici plutôt que par l'ellipse du
                # label : sinon c'est le compteur de modifications, en bout de
                # ligne, qui disparaît — or c'est le plus utile des deux.
                short = branch if len(branch) <= 14 else branch[:13] + "…"
                detail = (f"  <span foreground='{FAINT}'>󰘬 "
                          f"{esc(short)}</span>")
                if changes:
                    detail += (f" <span foreground='{COLORS['waiting']}'>"
                               f"{changes}</span>")
            count = ""
            if not reveal.get_reveal_child():
                count = (f"  <span foreground='{FAINT}'>"
                         f"{len(group)} session{'s' if len(group) > 1 else ''}"
                         f"</span>")
            head_lbl.set_markup(
                f"<span foreground='{FAINT}' size='small'>{chevron}</span>  "
                f"<span foreground='{DIM}' size='small' weight='600'>"
                f"{esc(name.upper())}</span>{detail}{count}"
            )

        def on_toggle(_btn):
            opened = not reveal.get_reveal_child()
            reveal.set_reveal_child(opened)
            self.collapsed[cwd] = not opened
            render_head()

        toggle.connect("clicked", on_toggle)
        tip = cwd
        if group[0].get("git_branch"):
            tip += f"\nBranche {group[0]['git_branch']}"
            if group[0].get("git_changes"):
                tip += f" — {group[0]['git_changes']} fichiers modifiés"
        toggle.set_tooltip_text(tip)
        render_head()
        return wrap

    # -- Ligne de session --------------------------------------------------
    def _session_row(self, session, now):
        status = session.get("status", "idle")
        colour = COLORS.get(status, "#ebebf0")
        addr = session.get("addr", "")

        btn = Gtk.Button()
        ctx = btn.get_style_context()
        ctx.add_class("session")
        ctx.add_class(status)
        if session.get("msg"):
            btn.set_tooltip_text(session["msg"])

        # Colonne d'icône puis colonne de texte, comme les lignes des autres
        # popups : glisser le glyphe dans le markup du titre décalait ce seul
        # titre vers la droite, l'état et le prompt restant à gauche sous lui.
        row = Gtk.Box(spacing=9)
        icon_lbl = Gtk.Label()
        icon_lbl.set_size_request(18, -1)
        icon_lbl.set_valign(Gtk.Align.START)
        icon_lbl.set_margin_top(1)
        row.pack_start(icon_lbl, False, False, 0)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # ── Ligne 1 : état, titre, workspace, actions ──
        line1 = Gtk.Box(spacing=8)
        title_lbl = Gtk.Label(xalign=0)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.set_max_width_chars(WIDTH_CHARS - 8)
        line1.pack_start(title_lbl, True, True, 0)

        actions = Gtk.Revealer()
        actions.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        actions.set_transition_duration(90)
        arow = Gtk.Box(spacing=0)
        hover = HoverActions(actions)

        reply_reveal = Gtk.Revealer()
        reply_reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        if addr:
            reply = glyph_button("󰍡", "Écrire une réponse et l'envoyer",
                                 lambda _b: self._toggle_reply(reply_reveal))
            arow.pack_start(hover.watch(reply), False, False, 0)
        else:
            # Session sans fenêtre : le geste utile n'est pas de sauter dessus,
            # c'est de la rouvrir là où elle s'est arrêtée.
            resume = glyph_button("󰑓", "Rouvrir cette session dans un terminal",
                                  lambda _b, s=session: self._on_resume(s))
            resume.set_sensitive(bool(session.get("session_id")))
            arow.pack_start(hover.watch(resume), False, False, 0)

        kill = glyph_button("󰅖", "Terminer la session (un second clic confirme)",
                            danger=True)
        kill._armed = False
        kill.connect("clicked", self._on_kill, session)
        arow.pack_start(hover.watch(kill), False, False, 0)
        actions.add(arow)
        line1.pack_end(actions, False, False, 0)
        inner.pack_start(line1, False, False, 0)

        # ── Ligne 2 : état daté, modèle, mode, réserve de contexte ──
        line2 = Gtk.Box(spacing=8)
        meta_lbl = Gtk.Label(xalign=0)
        meta_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        meta_lbl.set_max_width_chars(WIDTH_CHARS - 14)
        line2.pack_start(meta_lbl, True, True, 0)

        used = session.get("context") or 0
        limit = session.get("context_limit") or 0
        ctx_bar = ctx_lbl = None
        if used and limit:
            ratio = min(1.0, used / limit)
            ctx_bar = Gtk.ProgressBar()
            ctx_bar.get_style_context().add_class("ctx")
            ctx_bar.set_valign(Gtk.Align.CENTER)
            ctx_bar.set_size_request(52, -1)
            ctx_lbl = Gtk.Label(xalign=1)
            ctx_lbl.set_width_chars(4)
            line2.pack_end(ctx_lbl, False, False, 0)
            line2.pack_end(ctx_bar, False, False, 0)
        inner.pack_start(line2, False, False, 0)

        # ── Ligne 3 : dernier prompt ──
        prompt_lbl = None
        prompt = (session.get("last_prompt") or "").strip().replace("\n", " ")
        if prompt:
            prompt_lbl = Gtk.Label(xalign=0)
            prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            prompt_lbl.set_max_width_chars(WIDTH_CHARS)
            prompt_lbl.set_margin_top(1)
            inner.pack_start(prompt_lbl, False, False, 0)

        # ── Zone de réponse, révélée par 󰍡 ──
        entry_box = Gtk.Box(spacing=6)
        entry = Gtk.Entry()
        entry.get_style_context().add_class("reply")
        entry.set_placeholder_text("Réponse à envoyer…")
        entry.connect("activate", self._on_send, session, entry, reply_reveal)
        send = glyph_button("󰒊", "Envoyer",
                            lambda _b: self._on_send(None, session, entry,
                                                     reply_reveal))
        entry_box.pack_start(entry, True, True, 0)
        entry_box.pack_end(send, False, False, 0)
        entry_box.set_margin_top(6)
        reply_reveal.add(entry_box)
        reply_reveal._entry = entry
        hover.watch(entry)
        hover.watch(send)
        inner.pack_start(reply_reveal, False, False, 0)

        row.pack_start(inner, True, True, 0)
        btn.add(row)
        hover.watch(btn)
        btn.connect("clicked", self._on_jump, addr)
        if not addr:
            btn.set_tooltip_text("Fenêtre introuvable — session détachée")

        widgets = {"icon": icon_lbl, "title": title_lbl, "meta": meta_lbl,
                   "prompt": prompt_lbl, "ctx_bar": ctx_bar,
                   "ctx_lbl": ctx_lbl, "button": btn}
        self._rows.append((session, widgets))
        self._render_row(session, widgets, now)
        return btn

    def _render_row(self, session, w, now):
        """Peint une ligne. Séparé de sa construction pour le rafraîchissement."""
        status = session.get("status", "idle")
        colour = COLORS.get(status, "#ebebf0")
        title = session.get("title")
        if not title:
            title = f"<span style='italic' foreground='{DIM}'>session sans titre</span>"
        else:
            title = f"<span weight='600'>{esc(title)}</span>"
        ws = session.get("workspace")
        ws_part = (f"   <span foreground='{FAINT}' size='small'>{esc(ws)}</span>"
                   if ws else "")
        w["icon"].set_markup(
            f"<span foreground='{colour}' size='large'>"
            f"{ICONS.get(status, '')}</span>")
        w["title"].set_markup(f"{title}{ws_part}")

        age = now - session.get("ts", now)
        stale = status == "waiting" and age > STALE_AFTER
        phrase_colour = STALE if stale else colour
        bits = [f"<span foreground='{phrase_colour}'>"
                f"{esc(state_phrase(status, age))}</span>"]
        model = short_model(session.get("model"))
        if model:
            bits.append(esc(model))
        mode = session.get("permission_mode")
        if mode and mode != "normal":
            bits.append(f"<span foreground='{COLORS['waiting']}'>{esc(mode)}</span>")
        w["meta"].set_markup(
            f"<span foreground='{DIM}' size='small'>{' · '.join(bits)}</span>")
        w["meta"].set_tooltip_text(
            "Session en attente depuis plus de deux heures" if stale else None)

        if w["ctx_bar"] is not None:
            used = session.get("context") or 0
            limit = session.get("context_limit") or 1
            ratio = min(1.0, used / limit)
            w["ctx_bar"].set_fraction(ratio)
            bar_ctx = w["ctx_bar"].get_style_context()
            for cls in ("ctx-ok", "ctx-warn", "ctx-full"):
                bar_ctx.remove_class(cls)
            bar_ctx.add_class("ctx-full" if ratio >= 0.85
                              else "ctx-warn" if ratio >= 0.7 else "ctx-ok")
            w["ctx_bar"].set_tooltip_text(
                f"Contexte : {used:,} / {limit:,} tokens ({ratio * 100:.0f} %)"
                .replace(",", " "))
            w["ctx_lbl"].set_markup(
                f"<span foreground='{FAINT}' size='small'>"
                f"{human_tokens(used)}</span>")

        if w["prompt"] is not None:
            prompt = (session.get("last_prompt") or "").strip().replace("\n", " ")
            w["prompt"].set_markup(
                f"<span foreground='{FAINT}' size='small'>"
                f"{esc(prompt[:120])}</span>")

    # -- Rafraîchissement --------------------------------------------------
    @staticmethod
    def _signature(sessions):
        """Ce qui, en changeant, impose de rebâtir la liste plutôt que de la
        repeindre : une session apparaît, disparaît, ou change d'état."""
        return tuple(sorted((s.get("session_id"), s.get("status"),
                             bool(s.get("addr")), s.get("title"))
                            for s in sessions))

    def _tick(self):  # noqa: D401
        """Relit l'état en tâche de fond, toutes les REFRESH_SECONDS.

        La lecture enrichie ouvre une dizaine de transcripts et lance autant de
        `git status` : la faire dans la boucle GTK figerait la fenêtre à chaque
        battement. Le thread ne touche rien de l'interface — il repasse la main
        au fil principal par GLib.idle_add.
        """
        if not self._alive:
            return False
        threading.Thread(target=self._reload_worker, daemon=True).start()
        return True

    def _reload_worker(self):
        try:
            sessions = load_sessions(enrich=True)
        except Exception:
            return
        GLib.idle_add(self._apply_reload, sessions)

    def _apply_reload(self, sessions):
        if not self._alive:
            return False
        now = time.time()
        if self._signature(sessions) != self._signature(self.sessions):
            # Une réponse à moitié écrite ne se sacrifie pas à un rafraîchis-
            # sement : la liste attendra le prochain battement.
            if self._reply_open():
                return False
            self.sessions = sessions
            self._open_replies.clear()
            self._populate()
            return False

        by_id = {s.get("session_id"): s for s in sessions}
        for session, widgets in self._rows:
            fresh = by_id.get(session.get("session_id"))
            if fresh:
                session.update(fresh)
            self._render_row(session, widgets, now)
        self.sessions = sessions
        self._update_summary(self._visible_sessions())
        return False

    def _reply_open(self):
        return bool(self._open_replies)

    # -- Filtre ------------------------------------------------------------
    def _on_filter(self, entry):
        self.filter_text = entry.get_text()
        self._populate()

    def _on_filter_activate(self, _entry):
        """Entrée dans le filtre : saute sur la première session restante."""
        visible = self._visible_sessions()
        if visible and visible[0].get("addr"):
            self._on_jump(None, visible[0]["addr"])

    # -- Actions -----------------------------------------------------------
    def _toggle_reply(self, revealer):
        opening = not revealer.get_reveal_child()
        revealer.set_reveal_child(opening)
        (self._open_replies.add if opening
         else self._open_replies.discard)(id(revealer))
        if opening:
            revealer._entry.grab_focus()

    def _on_jump(self, _btn, addr):
        if addr:
            hypr("focuswindow", f"address:{addr}")
        self.close()

    def _on_gather(self, _btn):
        """Ramène les fenêtres à traiter sur le bureau courant."""
        import json
        try:
            out = subprocess.run(["hyprctl", "activeworkspace", "-j"],
                                 capture_output=True, text=True,
                                 timeout=2).stdout
            ws = json.loads(out).get("id")
        except Exception:
            ws = None
        if ws is None:
            return
        for s in getattr(self, "_gather_attention", []):
            if s.get("addr"):
                hypr("movetoworkspacesilent", f"{ws},address:{s['addr']}")
        self.close()

    def _on_resume(self, session):
        """Rouvre une session détachée dans un terminal, là où elle s'est
        arrêtée. `claude --resume <id>` retrouve la conversation ; le hook
        réinscrit la session dès le premier tour."""
        cwd = session.get("cwd") or os.path.expanduser("~")
        sid = session.get("session_id")
        if not sid:
            return
        run_detached(["kitty", "--class", "waybar.modules",
                      "--directory", cwd,
                      "-T", f"claude — {session.get('dir', '')}",
                      "claude", "--resume", sid])
        self.close()

    def _on_kill(self, btn, session):
        """Premier clic : arme le bouton. Second clic : termine la session."""
        if not btn._armed:
            btn._armed = True
            btn.set_label("Confirmer")
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
            btn.set_label("󰅖")
            btn.get_style_context().remove_class("armed")
        return False  # ne pas répéter

    def _on_send(self, _widget, session, entry, revealer):
        """Envoie le texte dans la session : focus, puis frappe via wtype.

        Le texte passe par l'environnement plutôt que par la ligne de commande :
        aucun risque d'interprétation par le shell, quels que soient les
        guillemets ou accents saisis.

        La frappe attend que la fenêtre ait vraiment le focus au lieu de parier
        sur un délai fixe : sous charge, un tiers de seconde ne suffisait pas
        toujours et la réponse partait dans la fenêtre précédente.
        """
        text = entry.get_text().strip()
        addr = session.get("addr")
        if not text or not addr:
            return
        env = dict(os.environ, CS_REPLY=text, CS_ADDR=addr)
        script = (
            # Syntaxe Lua : cf. hypr() plus haut, la forme hyprlang ne parse plus.
            'hyprctl dispatch '
            '"hl.dsp.focus({ window = \\"address:$CS_ADDR\\" })" >/dev/null; '
            'for i in $(seq 40); do '
            '  cur=$(hyprctl activewindow -j | jq -r ".address // empty"); '
            '  [ "$cur" = "$CS_ADDR" ] && break; '
            '  sleep 0.05; '
            'done; '
            '[ "$cur" = "$CS_ADDR" ] || exit 1; '
            'sleep 0.12; '
            'wtype -- "$CS_REPLY"; '
            'sleep 0.1; '
            'wtype -k Return'
        )
        try:
            subprocess.Popen(["bash", "-c", script], env=env,
                             start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass
        self._open_replies.discard(id(revealer))
        self.close()

    # -- Focus initial -----------------------------------------------------
    def _grab_first_focus(self):
        """Le filtre prend la main : taper trois lettres puis Entrée est le
        chemin le plus court vers une session, à dix sessions ouvertes."""
        if self._filter_entry.get_visible():
            self._filter_entry.grab_focus()
            return
        super()._grab_first_focus()


def main():
    run_popup(lambda: ClaudeMenu(load_sessions(enrich=True)),
              "waybar-claude-menu")


if __name__ == "__main__":
    main()
