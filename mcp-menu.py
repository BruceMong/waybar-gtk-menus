#!/usr/bin/env python3
"""Popup « MCP » pour Waybar — les serveurs MCP vus par projet.

Ouvert par le module custom/mcp (icône 󰒍). Trois étages, du plus partagé au
plus local, dans un seul défilement :

  1. PARTAGÉS — les unités mcp-shared@<nom> (cf. ~/.local/bin/mcp-shared) :
     état, port, mémoire, depuis quand, combien de projets ouverts s'en
     servent ; journal / relancer / arrêter au survol.
  2. GLOBAUX — ~/.claude.json, ce que toutes les sessions reçoivent : les
     serveurs distants (auth requise ? connecté ?) et les stdio, avec le
     nombre de processus qu'ils font tourner en ce moment, toutes sessions
     confondues. C'est ce fan-out qui a rempli la RAM le 2026-09-16.
  3. Un bloc par PROJET — ceux qui ont une session ouverte d'abord, puis ceux
     dont la config diffère de la globale (.mcp.json, serveur coupé). Chaque
     ligne dit d'où vient le serveur (global / .mcp.json / local), ce qu'il
     coûte ici, et son état résolu ; l'interrupteur l'active ou le coupe
     pour ce projet. Les globaux qui ne diffèrent en rien restent repliés
     derrière une ligne « + N globaux ».

L'état résolu vient de `claude mcp list`, lancé dans le projet, en cache
(~/.cache/waybar-mcp/). Il est relu de lui-même pour les projets ouverts
quand il date de plus de dix minutes ; pour un projet fermé, seulement sur
demande (󰑓 dans son en-tête) — la sonde DÉMARRE les serveurs stdio du projet.

Ce que le popup sait écrire, et où :
  - interrupteur d'un global ou d'un local → ~/.claude.json
    .projects[cwd].disabledMcpServers ;
  - interrupteur d'un serveur de .mcp.json → <projet>/.claude/settings.local.json
    enabledMcpjsonServers / disabledMcpjsonServers ;
  - « Partager » un stdio → ~/.config/mcp-proxy/servers.json + unité, et
    l'entrée Claude rebranchée en http (cf. mcp_data.share_server).
Tout cela vaut pour les sessions à venir : une session ouverte garde ses
serveurs jusqu'à /mcp ou son redémarrage.

Les données viennent de mcp_data.py, partagé avec mcp-status.py.
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp_data as md  # noqa: E402
from menu_common import Card, LayerPopup, custom_row, run_popup  # noqa: E402
from claude_sessions_data import load_sessions  # noqa: E402


def _load_script(name):
    """Importe un script voisin dont le nom porte un tiret."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Les gestes herdr (focus d'un pane, remontée de la fenêtre) et les briques de
# survol sont ceux du popup Sessions Claude : une seule implémentation.
cm = _load_script("claude-menu")
HoverActions, glyph_button, run_detached = cm.HoverActions, cm.glyph_button, cm.run_detached
herdr, show_herdr, esc = cm.herdr, cm.show_herdr, cm.esc

DEVNULL = subprocess.DEVNULL
TERM_CLASS = "journal-view"
POPUP_WIDTH = 600
WIDTH_CHARS = 48
REFRESH_MS = 8000
BAR_SIGNAL = "RTMIN+16"
EDITOR = os.environ.get("MCP_MENU_EDITOR", "code")

COLORS = {
    "ok": "#32d74b", "starting": "#ffd60a", "failed": "#ff453a",
    "down": "#ff453a", "auth": "#ff9f0a", "disabled": "#6c6c72",
    "warn": "#ff9f0a", "unknown": "#6c6c72",
}
DIM, FAINT, ACCENT = "#94949a", "#6c6c72", "#0a84ff"
DOT = "●"          # ● état connu
RING = "○"         # ○ jamais vérifié
IC_JOURNAL, IC_RESTART, IC_STOP, IC_START = "\U000f02fd", "\U000f0450", "\U000f04db", "\U000f040a"
IC_SHARE, IC_MCP, IC_FILE, IC_CHECK = "\U000f048d", "\U000f0d73", "\U000f0219", "\U000f0450"
IC_WARN = "\U000f0026"

SHARED_STATES = {
    "ok": "répond", "starting": "démarre",
    "failed": "proxy vivant, serveur muet", "down": "arrêté",
}
HEALTH_LABELS = {
    "ok": "connecté", "failed": "échec", "auth": "auth requise",
    "disabled": "désactivé ici", "warn": "attention", "unknown": "",
}
SCOPE_LABELS = {"user": "global", "project": ".mcp.json", "local": "local",
                "plugin": "plugin"}

EXTRA_CSS = cm.EXTRA_CSS + """
/* Interrupteur d'une ligne de serveur : plus petit que celui des réglages
   système, il n'est pas le sujet de la ligne. */
switch.mcp { min-height: 18px; min-width: 34px; }
switch.mcp slider { min-height: 14px; min-width: 14px; }
""".encode()


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def since_label(ts):
    """« depuis 12 min » à partir de l'horodatage systemd."""
    if not ts:
        return ""
    try:
        when = datetime.datetime.strptime(" ".join(ts.split()[1:3]),
                                          "%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
    return "depuis " + ago(int((datetime.datetime.now() - when).total_seconds()))


def ago(secs):
    if secs < 60:
        return "quelques secondes"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} j"


def mo(ko):
    return f"{ko // 1024} Mo"


def plural(n, word, many=None):
    """« 3 projets ouverts » — le pluriel se passe en clair quand un « s »
    en bout de mot ne suffit pas."""
    if n <= 1:
        return f"{n} {word}"
    return f"{n} {many or word + 's'}"


def unit_error(name):
    """Dernières lignes parlantes du journal d'un serveur partagé en panne.

    mcp-proxy crache des traces Python entières ; ce qui renseigne, c'est la
    ligne du serveur lui-même (« Authentication failed »), puis celle de
    systemd (« start-limit-hit »). Le serveur d'abord : c'est la cause.
    """
    try:
        out = subprocess.check_output(
            ["journalctl", "--user", "-u", f"mcp-shared@{name}.service",
             "-n", "120", "--no-pager", "-o", "cat"],
            text=True, stderr=DEVNULL, timeout=5)
    except Exception:
        return ""
    app, sysd = [], []
    for line in out.splitlines():
        line = line.strip().lstrip("|").strip()
        if not line or line.startswith((
                "Traceback", "Exception Group", "ExceptionGroup", "File \"",
                "^", "+", "<", "raise ", "at ", "throw ", "node:", "npm notice",
                "status:", "signal:", "output:", "stdout:", "stderr:", "pid:")):
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                line = " — ".join(str(obj[k]) for k in ("message", "error") if obj.get(k))
            except Exception:
                pass
        low = line.lower()
        if not any(w in low for w in ("error", "fatal", "failed", "invalid",
                                      "exception", "refused", "denied")):
            continue
        if low.startswith("mcp.shared.exceptions"):
            continue
        if len(line) > 96:
            line = line[:95] + "…"
        target = sysd if line.startswith(("mcp-shared@", "Failed to start")) else app
        if line not in target:
            target.append(line)
    return "\n".join(app[-2:] + sysd[-1:])


def host_of(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or url
    except Exception:
        return url


def caption(text, color=FAINT, size="small", width=WIDTH_CHARS, mono=False):
    lbl = Gtk.Label(xalign=0)
    body = f"<tt>{esc(text)}</tt>" if mono else esc(text)
    lbl.set_markup(f"<span foreground='{color}' size='{size}'>{body}</span>")
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(width)
    return lbl


# ---------------------------------------------------------------------------
# Modèle : tout ce que le popup affiche, calculé hors du fil GTK
# ---------------------------------------------------------------------------


class Model:
    def __init__(self):
        self.sessions = []
        self.ucfg = {}
        self.projects = {}          # cwd -> ouvert ?
        self.servers = {}           # cwd -> [serveurs résolus]
        self.procs = {}             # pid session -> {nom: [(pid, rss)]}
        self.shared = None          # None : pas encore sondé
        self.health = {}            # cwd -> cache claude mcp list

    def load(self, with_shared=True):
        self.sessions = load_sessions()
        self.ucfg = md.user_config()
        self.projects = md.candidate_projects(
            [s.get("cwd") for s in self.sessions], self.ucfg)
        self.servers = {cwd: md.project_servers(cwd, self.ucfg)
                        for cwd in self.projects}
        self.procs = md.stdio_processes(self.sessions, self.servers)
        self.health = {cwd: md.health(cwd) for cwd in self.projects}
        if with_shared:
            self.shared = md.shared_status()
        return self

    # -- Agrégats ----------------------------------------------------------
    def sessions_of(self, cwd):
        return [s for s in self.sessions if s.get("cwd") == cwd]

    def procs_of(self, cwd, name):
        """[(pid, rss)] des processus d'un serveur dans les sessions d'un projet."""
        out = []
        for s in self.sessions_of(cwd):
            out += self.procs.get(s.get("pid"), {}).get(name, [])
        return out

    def procs_total(self, name):
        out = []
        for per in self.procs.values():
            out += per.get(name, [])
        return out

    def stdio_total(self):
        n, rss = 0, 0
        for per in self.procs.values():
            for lst in per.values():
                n += len(lst)
                rss += sum(r for _, r in lst)
        return n, rss

    def health_of(self, cwd, name):
        h = self.health.get(cwd) or {}
        return (h.get("servers") or {}).get(name)

    def global_state(self, name):
        """État d'un global, au mieux de ce qu'en disent les projets vérifiés :
        connecté quelque part = connecté ; sinon la pire nouvelle connue."""
        states = set()
        for cwd in self.projects:
            st = self.health_of(cwd, name)
            if st and st["state"] != "disabled":
                states.add(st["state"])
        for s in ("ok", "auth", "failed", "warn"):
            if s in states:
                return s
        return "unknown"

    def plugin_servers(self):
        """Serveurs qu'un plugin ajoute (plugin:vercel:vercel) : absents des
        fichiers de config, ils ne sont connus que par `claude mcp list`."""
        names = {}
        for cwd, h in self.health.items():
            for name, st in ((h or {}).get("servers") or {}).items():
                if name.startswith("plugin:") or " " in name:
                    names.setdefault(name, st)
        return names

    def users_of_shared(self, shared_name):
        n = 0
        for cwd, open_ in self.projects.items():
            if open_ and any(e["shared"] == shared_name and e["enabled"]
                             for e in self.servers[cwd]):
                n += 1
        return n

    def a_pane(self, cwd=None):
        """Un pane herdr où envoyer /mcp : dans le projet si possible, sinon
        n'importe où — l'OAuth d'un global vaut pour toutes les sessions."""
        for s in self.sessions_of(cwd) if cwd else []:
            if s.get("herdr_pane"):
                return s["herdr_pane"]
        for s in self.sessions:
            if s.get("herdr_pane"):
                return s["herdr_pane"]
        return None


# ---------------------------------------------------------------------------
# Popup
# ---------------------------------------------------------------------------


class McpMenu(LayerPopup):
    def __init__(self):
        super().__init__("MCP", width=POPUP_WIDTH, margin_right=150)
        provider = Gtk.CssProvider()
        provider.load_from_data(EXTRA_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        self.model = Model().load(with_shared=False)
        self.filter_text = ""
        self.collapsed = {}         # clé de bloc -> replié ?
        self.expanded_globals = set()
        self.checking = set()       # cwd en cours de `claude mcp list`
        self._check_queue = []
        self._check_lock = threading.Lock()
        self._alive = True
        self._loading = False
        self._armed = {}

        self.connect("destroy", lambda *_: setattr(self, "_alive", False))
        self._build_shell()
        self._render()
        # Premier tour tout de suite (sonde des partagés, jusqu'à 3 s par
        # serveur muet — hors du fil GTK), puis périodique.
        self._refresh()
        GLib.timeout_add(REFRESH_MS, self._refresh)
        self._queue_stale_checks()

    # -- Ossature ----------------------------------------------------------
    def _build_shell(self):
        self._filter = Gtk.SearchEntry()
        self._filter.get_style_context().add_class("search")
        self._filter.set_placeholder_text("Filtrer par serveur, projet…")
        self._filter.connect("search-changed", self._on_filter)
        self.box.pack_start(self._filter, False, False, 0)

        self._summary = Gtk.Label(xalign=0)
        self._summary.set_margin_start(4)
        self._summary.set_line_wrap(True)
        self._summary.set_max_width_chars(WIDTH_CHARS)
        self.box.pack_start(self._summary, False, False, 0)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_propagate_natural_height(True)
        self._scroller.set_overlay_scrolling(True)
        self._scroller.set_max_content_height(600)
        self._scroller.set_propagate_natural_width(False)
        self._scroller.set_max_content_width(POPUP_WIDTH - 28)
        self._scroller.set_size_request(POPUP_WIDTH - 28, -1)
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._scroller.add(self._body)
        self.box.pack_start(self._scroller, True, True, 0)

    # -- Rafraîchissement --------------------------------------------------
    def _refresh(self):
        if not self._alive:
            return False
        if not self._loading:
            self._loading = True
            threading.Thread(target=self._load_worker, daemon=True).start()
        return True

    def _load_worker(self):
        try:
            model = Model().load()
        except Exception:
            model = None
        GLib.idle_add(self._apply_model, model)

    def _apply_model(self, model):
        self._loading = False
        if model is None or not self._alive:
            return False
        self.model = model
        self._render()
        return False

    def _queue_stale_checks(self):
        for cwd, open_ in self.model.projects.items():
            if open_ and md.health_is_stale(cwd):
                self._queue_check(cwd)

    def _queue_check(self, cwd):
        """`claude mcp list` un projet à la fois : chaque sonde lance les
        serveurs stdio du projet, deux sondes de front doubleraient la note."""
        with self._check_lock:
            if cwd in self.checking or cwd in self._check_queue:
                return
            self._check_queue.append(cwd)
            start = not self.checking
        if start:
            self._next_check()

    def _next_check(self):
        with self._check_lock:
            if not self._check_queue:
                return
            cwd = self._check_queue.pop(0)
            self.checking.add(cwd)
        self._render_headers_only()
        threading.Thread(target=self._check_worker, args=(cwd,),
                         daemon=True).start()

    def _check_worker(self, cwd):
        md.refresh_health(cwd)
        with self._check_lock:
            self.checking.discard(cwd)
        GLib.idle_add(self._after_check)

    def _after_check(self):
        self._next_check()
        self._refresh()
        return False

    def _render_headers_only(self):
        # Le seul changement est « vérification… » dans un en-tête : une
        # reconstruction complète ferait sauter le défilement pour rien.
        for cb in getattr(self, "_head_renderers", []):
            cb()

    def _bar_signal(self):
        subprocess.Popen(["pkill", f"-{BAR_SIGNAL}", "waybar"],
                         stdout=DEVNULL, stderr=DEVNULL)

    # -- Rendu -------------------------------------------------------------
    def _render(self):
        adj = self._scroller.get_vadjustment()
        scroll = adj.get_value()
        for child in self._body.get_children():
            child.destroy()
        self._head_renderers = []
        m = self.model

        self._render_summary()
        q = self.filter_text

        blocks = []
        if m.shared is None or m.shared or os.path.isfile(md.SHARED_CONF):
            blocks.append(self._shared_block())
        blocks.append(self._globals_block())

        open_ = sorted((c for c, o in m.projects.items() if o),
                       key=lambda c: (-len(m.sessions_of(c)), c))
        closed = sorted(c for c, o in m.projects.items() if not o)
        for cwd in open_ + closed:
            blocks.append(self._project_block(cwd))
        # Un bloc vidé par le filtre rend une boîte vide : on ne l'empile pas.
        blocks = [b for b in blocks if b is not None and b.get_children()]
        for b in blocks:
            self._body.pack_start(b, False, False, 0)

        if q and not blocks:
            lbl = Gtk.Label(xalign=0, label="Rien ne correspond au filtre.")
            lbl.get_style_context().add_class("empty")
            self._body.pack_start(lbl, False, False, 0)
        self._body.show_all()
        GLib.idle_add(lambda: (adj.set_value(min(scroll, adj.get_upper())), False)[1])

    def _render_summary(self):
        m = self.model
        parts = []
        if m.shared:
            ok = sum(1 for s in m.shared if s.get("state") == "ok")
            col = COLORS["ok"] if ok == len(m.shared) else COLORS["failed"]
            parts.append(f"partagés <span foreground='{col}'>{ok}/{len(m.shared)}</span>")
        elif m.shared is None and os.path.isfile(md.SHARED_CONF):
            parts.append("partagés …")
        n, rss = m.stdio_total()
        if n:
            col = COLORS["auth"] if n >= 20 else DIM
            parts.append(f"<span foreground='{col}'>{plural(n, 'processus stdio', 'processus stdio')}</span>"
                         f" ({mo(rss)}) dans {plural(len(m.sessions), 'session')}")
        elif m.sessions:
            parts.append(f"{plural(len(m.sessions), 'session')}, aucun stdio")
        auth = {name for cwd in m.projects
                for name, st in ((m.health.get(cwd) or {}).get("servers") or {}).items()
                if st.get("state") == "auth"}
        if auth:
            parts.append(f"<span foreground='{COLORS['auth']}'>"
                         f"{plural(len(auth), 'auth requise', 'auth requises')}</span>")
        self._summary.set_markup(
            f"<span foreground='{DIM}' size='small'>{' · '.join(parts) or 'rien à signaler'}</span>")

    def _matches(self, *words):
        q = self.filter_text
        return not q or any(q in (w or "").lower() for w in words)

    # -- Blocs -------------------------------------------------------------
    def _block(self, key, title, detail_fn, actions=(), collapsed=False, tooltip=""):
        """Intertitre repliable + carte, le gabarit des blocs projet du popup
        Sessions Claude. Rend (wrap, card, render_head)."""
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header = Gtk.Box(spacing=4)
        header.set_margin_start(2)
        header.set_margin_end(2)
        toggle = Gtk.Button()
        toggle.get_style_context().add_class("project")
        head_lbl = Gtk.Label(xalign=0)
        head_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        head_lbl.set_max_width_chars(WIDTH_CHARS - 4)
        toggle.add(head_lbl)
        if tooltip:
            toggle.set_tooltip_text(tooltip)
        header.pack_start(toggle, True, True, 0)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        revealer.set_transition_duration(90)
        arow = Gtk.Box(spacing=0)
        hover = HoverActions(revealer)
        for icon, tip, handler in actions:
            arow.pack_start(hover.watch(glyph_button(icon, tip, handler)), False, False, 0)
        revealer.add(arow)
        header.pack_end(revealer, False, False, 0)
        hover.watch(toggle)
        hover.watch(header, hover=True)
        wrap.pack_start(header, False, False, 0)

        card = Card()
        reveal = Gtk.Revealer()
        reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        reveal.set_transition_duration(140)
        reveal.add(card)
        reveal.set_reveal_child(not self.collapsed.setdefault(key, collapsed))
        wrap.pack_start(reveal, False, False, 0)

        def render_head(*_):
            chevron = "󰅀" if reveal.get_reveal_child() else "󰅂"
            head_lbl.set_markup(
                f"<span foreground='{FAINT}' size='small'>{chevron}</span>  "
                f"<span foreground='{DIM}' size='small' weight='600'>{esc(title)}</span>"
                f"<span size='small'>{detail_fn(reveal.get_reveal_child())}</span>")

        def on_toggle(_b):
            opened = not reveal.get_reveal_child()
            reveal.set_reveal_child(opened)
            self.collapsed[key] = not opened
            render_head()

        toggle.connect("clicked", on_toggle)
        render_head()
        self._head_renderers.append(render_head)
        return wrap, card

    def _server_row(self, card, state, title_markup, subtitle, actions=(),
                    switch=None, note=None, tooltip=None):
        """Ligne : pastille d'état, titre + sous-titre, interrupteur,
        actions au survol, éventuelle note dessous (erreur, avertissement)."""
        row = Gtk.Box(spacing=9)
        dot = Gtk.Label()
        dot.set_markup(f"<span foreground='{COLORS.get(state, FAINT)}'>"
                       f"{RING if state == 'unknown' else DOT}</span>")
        dot.set_size_request(14, -1)
        dot.set_valign(Gtk.Align.CENTER)
        row.pack_start(dot, False, False, 0)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(xalign=0)
        t.set_markup(title_markup)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.set_max_width_chars(WIDTH_CHARS)
        texts.pack_start(t, False, False, 0)
        if subtitle:
            s = Gtk.Label(xalign=0)
            s.set_markup(f"<span foreground='{FAINT}' size='small'>{esc(subtitle)}</span>")
            s.set_ellipsize(Pango.EllipsizeMode.END)
            s.set_max_width_chars(WIDTH_CHARS)
            texts.pack_start(s, False, False, 0)
        row.pack_start(texts, True, True, 0)

        if actions:
            revealer = Gtk.Revealer()
            revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
            revealer.set_transition_duration(90)
            arow = Gtk.Box(spacing=0)
            hover = HoverActions(revealer)
            for btn in actions:
                arow.pack_start(hover.watch(btn), False, False, 0)
            revealer.add(arow)
            row.pack_end(revealer, False, False, 0)
            hover.watch(row, hover=True)
        if switch is not None:
            switch.set_valign(Gtk.Align.CENTER)
            switch.get_style_context().add_class("mcp")
            row.pack_end(switch, False, False, 0)

        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        stack.pack_start(row, False, False, 0)
        if note is not None:
            note.set_margin_start(23)
            stack.pack_start(note, False, False, 0)
        line = custom_row(stack)
        if tooltip:
            line.set_tooltip_text(tooltip)
        card.add_row(line)
        return line

    # -- 1. Partagés -------------------------------------------------------
    def _shared_block(self):
        m = self.model
        rows = [s for s in (m.shared or []) if self._matches(s["name"], "partagé")]
        if m.shared is not None and not rows:
            return Gtk.Box()

        def detail(_opened):
            if m.shared is None:
                return f"  <span foreground='{FAINT}'>sonde en cours…</span>"
            ok = sum(1 for s in m.shared if s.get("state") == "ok")
            col = COLORS["ok"] if ok == len(m.shared) else COLORS["failed"]
            return f"  <span foreground='{col}'>{ok}/{len(m.shared)}</span>"

        wrap, card = self._block(
            "shared", "PARTAGÉS · mcp-shared", detail,
            actions=[(IC_RESTART, "Tout relancer (les sessions reconnectent au prochain appel)",
                      lambda _b: self._ctl("restart", "all"))],
            tooltip="Une unité systemd par serveur : mcp-shared@<nom>.service\n"
                    "Source : ~/.config/mcp-proxy/servers.json")
        if m.shared is None:
            card.info(None, "mcp-shared status…")
            return wrap
        for s in rows:
            self._shared_row(card, s)
        return wrap

    def _shared_row(self, card, s):
        m = self.model
        name, state = s["name"], s.get("state", "down")
        col = COLORS.get(state, COLORS["down"])
        title = (f"<b>{esc(name)}</b>  <span foreground='{col}' size='small'>"
                 f"{SHARED_STATES.get(state, state)}</span>")
        parts = [f":{s['port']}"]
        mem = s.get("memory", 0) // 1048576
        if mem:
            parts.append(f"{mem} Mo")
        since = since_label(s.get("since"))
        if since and state != "down":
            parts.append(since)
        if s.get("restarts"):
            parts.append(plural(s["restarts"], "redémarrage"))
        users = m.users_of_shared(name)
        parts.append(f"{plural(users, 'projet ouvert', 'projets ouverts')}" if users else "aucun projet ouvert")
        note = None
        if state not in ("ok", "starting"):
            err = unit_error(name)
            if err:
                note = caption(err, color=COLORS["starting"], size="x-small", mono=True)
                note.set_selectable(True)
        stop = (IC_START, "Démarrer", "start") if state == "down" else (IC_STOP, "Arrêter", "stop")
        actions = [
            glyph_button(IC_JOURNAL, "Journal", lambda _b, n=name: self._journal(n)),
            glyph_button(IC_RESTART, "Relancer", lambda _b, n=name: self._ctl("restart", n)),
            glyph_button(stop[0], stop[1], lambda _b, v=stop[2], n=name: self._ctl(v, n)),
        ]
        self._server_row(card, state, title, " · ".join(parts), actions, note=note)

    # -- 2. Globaux --------------------------------------------------------
    def _globals_block(self):
        m = self.model
        entries = [(name, e) for name, e in (m.ucfg.get("mcpServers") or {}).items()
                   if not md.shared_name_of(e)]
        plugins = list(m.plugin_servers().items())
        rows = [(n, e) for n, e in entries if self._matches(n, "global")]
        prow = [(n, st) for n, st in plugins if self._matches(n, "plugin")]
        if not rows and not prow:
            return Gtk.Box()
        n_stdio, rss = 0, 0
        for name, e in entries:
            if md.transport_of(e) == "stdio":
                lst = m.procs_total(name)
                n_stdio += len(lst)
                rss += sum(r for _, r in lst)

        def detail(opened):
            d = f"  <span foreground='{FAINT}'>{len(entries) + len(plugins)} serveurs</span>"
            if n_stdio:
                col = COLORS["auth"] if n_stdio >= 20 else FAINT
                d += f"  <span foreground='{col}'>{n_stdio} processus stdio · {mo(rss)}</span>"
            return d

        wrap, card = self._block(
            "globals", "GLOBAUX · ~/.claude.json", detail,
            actions=[(IC_FILE, "Ouvrir ~/.claude.json",
                      lambda _b: self._open_file(md.CLAUDE_JSON))],
            tooltip="Reçus par toutes les sessions, sauf ceux qu'un projet coupe.")
        for name, e in rows:
            self._global_row(card, name, e)
        for name, st in prow:
            title = (f"<b>{esc(name)}</b>  <span foreground='{FAINT}' size='small'>plugin</span>"
                     f"  <span foreground='{COLORS[st['state']]}' size='small'>"
                     f"{HEALTH_LABELS.get(st['state'], '')}</span>")
            actions = []
            if st["state"] == "auth":
                actions.append(self._mcp_button(None))
            self._server_row(card, st["state"], title, st.get("detail") or "", actions)
        return wrap

    def _global_row(self, card, name, e):
        m = self.model
        tr = md.transport_of(e)
        state = m.global_state(name)
        label = HEALTH_LABELS.get(state, "")
        title = f"<b>{esc(name)}</b>"
        if label:
            title += f"  <span foreground='{COLORS[state]}' size='small'>{label}</span>"
        actions = []
        if tr == "stdio":
            lst = m.procs_total(name)
            sub = f"stdio · {os.path.basename(e.get('command') or '?')}"
            if lst:
                col = COLORS["auth"] if len(lst) >= 10 else FAINT
                title += (f"  <span foreground='{col}' size='small'>"
                          f"{plural(len(lst), 'processus', 'processus')} · {mo(sum(r for _, r in lst))}</span>")
            else:
                sub += " · aucun processus"
            actions.append(self._share_button(None, {"name": name, "entry": e,
                                                     "transport": tr, "scope": "user"}))
        else:
            sub = f"{tr} · {host_of(e.get('url') or '')}"
        # Seul un global coupé compte : un .mcp.json homonyme non approuvé
        # est l'affaire du projet, pas de ce serveur-ci.
        off_in = [os.path.basename(c) for c, o in m.projects.items()
                  if any(x["name"] == name and x["scope"] != "project"
                         and not x["enabled"] for x in m.servers[c])]
        if off_in:
            sub += f" · coupé dans {', '.join(off_in[:3])}{'…' if len(off_in) > 3 else ''}"
        detail = ""
        for cwd in m.projects:
            st = m.health_of(cwd, name)
            if st and st["state"] == state and st["state"] not in ("ok", "disabled"):
                detail = st.get("detail", "")
                break
        if state == "auth":
            actions.append(self._mcp_button(None))
        note = caption(detail, color=COLORS[state], size="x-small") if detail and state != "ok" else None
        self._server_row(card, state, title, sub, actions, note=note)

    # -- 3. Projets --------------------------------------------------------
    def _project_block(self, cwd):
        m = self.model
        name = os.path.basename(cwd)
        servers = m.servers[cwd]
        sessions = m.sessions_of(cwd)
        open_ = m.projects[cwd]
        health = m.health.get(cwd)

        def interesting(e):
            if e["scope"] != "user" or not e["enabled"]:
                return True
            if e["transport"] == "stdio" and m.procs_of(cwd, e["name"]):
                return True
            st = m.health_of(cwd, e["name"])
            return bool(st and st["state"] not in ("ok", "disabled"))

        if self.filter_text:
            if self._matches(name):
                shown, hidden = servers, []
            else:
                shown = [e for e in servers if self._matches(e["name"])]
                hidden = []
                if not shown:
                    return None
        elif cwd in self.expanded_globals:
            shown, hidden = servers, []
        else:
            shown = [e for e in servers if interesting(e)]
            hidden = [e for e in servers if not interesting(e)]

        # Ce que le projet lancerait par session, une fois ouvert : les stdio
        # actifs, plus ceux d'un .mcp.json qu'on n'a pas encore refusés.
        n_stdio_conf = sum(1 for e in servers if e["transport"] == "stdio"
                           and (e["enabled"] or e["why"] == "pas encore approuvé"))
        procs = [p for e in servers for p in m.procs_of(cwd, e["name"])]

        def detail(opened):
            parts = []
            if sessions:
                parts.append(f"<span foreground='{FAINT}'>{plural(len(sessions), 'session')}</span>")
            else:
                parts.append(f"<span foreground='{FAINT}'>fermé</span>")
            if procs:
                parts.append(f"<span foreground='{FAINT}'>{len(procs)} stdio · "
                             f"{mo(sum(r for _, r in procs))}</span>")
            if not sessions and n_stdio_conf >= md.STDIO_WARN:
                parts.append(f"<span foreground='{COLORS['warn']}'>{IC_WARN} "
                             f"{n_stdio_conf} stdio par session</span>")
            if cwd in self.checking:
                parts.append(f"<span foreground='{ACCENT}'>vérification…</span>")
            elif health:
                parts.append(f"<span foreground='{FAINT}'>vérifié il y a "
                             f"{ago(int(time.time() - health.get('ts', 0)))}</span>")
            elif open_:
                parts.append(f"<span foreground='{FAINT}'>jamais vérifié</span>")
            if not opened:
                parts.append(f"<span foreground='{FAINT}'>{len(servers)} serveurs</span>")
            return "  " + f"<span foreground='{FAINT}'> · </span>".join(parts)

        conf_files = [p for p in (os.path.join(cwd, ".mcp.json"),
                                  md.project_settings_path(cwd)) if os.path.isfile(p)]
        actions = [(IC_CHECK,
                    "Vérifier : claude mcp list dans ce projet"
                    + ("" if open_ else "\n(lance ses serveurs stdio le temps de la sonde)"),
                    lambda _b, c=cwd: self._queue_check(c))]
        for p in conf_files:
            actions.append((IC_FILE, f"Ouvrir {os.path.relpath(p, cwd)}",
                            lambda _b, path=p: self._open_file(path)))
        wrap, card = self._block(
            cwd, name.upper(), detail, actions=actions,
            collapsed=not open_, tooltip=cwd)

        for e in shown:
            self._project_row(card, cwd, e)
        if hidden:
            ok = sum(1 for e in hidden if (m.health_of(cwd, e["name"]) or {}).get("state") == "ok")
            txt = f"+ {plural(len(hidden), 'global', 'globaux')}"
            if health:
                txt += ", tous connectés" if ok == len(hidden) else f", {ok} connectés"
            row = card.action("", txt, chevron=True,
                              on_click=lambda _b, c=cwd: (self.expanded_globals.add(c),
                                                          self._render()))
            row.title_label.set_markup(f"<span foreground='{DIM}' size='small'>{esc(txt)}</span>")
        elif cwd in self.expanded_globals and not self.filter_text:
            row = card.action("", "replier les globaux", chevron=True,
                              on_click=lambda _b, c=cwd: (self.expanded_globals.discard(c),
                                                          self._render()))
            row.title_label.set_markup(
                f"<span foreground='{DIM}' size='small'>replier les globaux</span>")
        return wrap

    def _project_row(self, card, cwd, e):
        m = self.model
        name = e["name"]
        st = m.health_of(cwd, name)
        if not e["enabled"]:
            state = "disabled"
        elif st:
            state = st["state"]
        else:
            state = "unknown"
        title = (f"<b>{esc(name)}</b>  <span foreground='{FAINT}' size='small'>"
                 f"{SCOPE_LABELS.get(e['scope'], e['scope'])}</span>")
        label = e["why"] if not e["enabled"] else HEALTH_LABELS.get(state, "")
        if label:
            title += f"  <span foreground='{COLORS[state]}' size='small'>{label}</span>"

        actions = []
        if e["transport"] == "stdio":
            lst = m.procs_of(cwd, name)
            sub = f"stdio · {os.path.basename(e['target'] or '?')}"
            if e["tokens"] and e["tokens"][0] != os.path.basename(e["target"] or ""):
                sub = f"stdio · {e['tokens'][0]}"
            if lst:
                title += (f"  <span foreground='{FAINT}' size='small'>"
                          f"{plural(len(lst), 'processus', 'processus')} · {mo(sum(r for _, r in lst))}</span>")
            if e["enabled"]:
                actions.append(self._share_button(cwd, e))
        elif e["shared"]:
            sub = f"{e['transport']} · partagé :{(e['entry'].get('url') or '').split(':')[-1].split('/')[0]}"
        else:
            sub = f"{e['transport']} · {host_of(e['target'])}"
        if state == "auth":
            actions.append(self._mcp_button(cwd))

        sw = Gtk.Switch()
        sw.set_active(e["enabled"])
        sw.set_tooltip_text(
            "Coupé pour ce projet via ~/.claude.json (disabledMcpServers)"
            if e["scope"] in ("user", "local") else
            "Approuvé via .claude/settings.local.json (enabledMcpjsonServers)")
        sw.connect("notify::active", self._on_switch, cwd, e)

        note = None
        if st and st["state"] in ("failed", "warn") and e["enabled"] and st.get("detail"):
            note = caption(st["detail"], color=COLORS[st["state"]], size="x-small")
        tip = f"{e['origin']}"
        if e["transport"] != "stdio":
            tip += f"\n{e['target']}"
        self._server_row(card, state, title, sub, actions, switch=sw, note=note, tooltip=tip)

    # -- Boutons partagés --------------------------------------------------
    def _mcp_button(self, cwd):
        pane = self.model.a_pane(cwd)
        btn = glyph_button(IC_MCP, "Envoyer /mcp à une session"
                           + (f" ({pane})" if pane else " — aucune session herdr"),
                           lambda _b, p=pane: self._send_mcp(p))
        btn.set_sensitive(bool(pane))
        return btn

    def _share_button(self, cwd, e):
        """Deux clics : le premier arme, le second partage. On ne réécrit pas
        ~/.claude.json et servers.json sur un clic malheureux."""
        btn = glyph_button(IC_SHARE, "Passer en partagé (mcp-shared) : un seul "
                           "processus pour toutes les sessions")
        key = (cwd, e["name"])

        def on_click(_b):
            if self._armed.get(key):
                btn.set_sensitive(False)
                threading.Thread(target=self._share_worker, args=(cwd, e),
                                 daemon=True).start()
                return
            self._armed[key] = True
            btn.set_label("Partager ?")
            btn.get_style_context().add_class("armed")
            GLib.timeout_add(4000, lambda: (self._disarm(btn, key), False)[1])

        btn.connect("clicked", on_click)
        return btn

    def _disarm(self, btn, key):
        self._armed.pop(key, None)
        if btn.get_realized():
            btn.set_label(IC_SHARE)
            btn.get_style_context().remove_class("armed")

    def _share_worker(self, cwd, e):
        ok, msg = md.share_server(cwd or md.HOME, e)
        GLib.idle_add(self._notify, ("MCP partagé" if ok else "Partage refusé"), msg, ok)
        GLib.idle_add(self._refresh)
        self._bar_signal()

    # -- Actions -----------------------------------------------------------
    def _on_switch(self, sw, _pspec, cwd, e):
        on = sw.get_active()
        try:
            if e["scope"] == "project":
                md.set_mcpjson_enabled(cwd, e["name"], on)
            else:
                md.set_user_server_disabled(cwd, e["name"], not on)
        except OSError as exc:
            self._notify("MCP", f"écriture impossible : {exc}", False)
        self._refresh()

    def _ctl(self, verb, name):
        subprocess.Popen([md.MCP_SHARED, verb, name], stdout=DEVNULL, stderr=DEVNULL)
        self._bar_signal()
        GLib.timeout_add(2500, lambda: (self._refresh(), False)[1])

    def _journal(self, name):
        run_detached(["setsid", "-f", "kitty", "--class", TERM_CLASS, "-e", "bash", "-c",
                      "journalctl --user -u mcp-shared@%s.service -n 200 --no-pager -e; exec bash"
                      % name])
        self.close()

    def _open_file(self, path):
        run_detached([EDITOR, path])
        self.close()

    def _send_mcp(self, pane):
        if not pane:
            return
        # Même chemin que herdr-clear : la commande part dans le pane sans
        # frappe simulée, puis on montre la session, où le dialogue s'ouvre.
        run_detached(["herdr", "agent", "prompt", pane, "/mcp"])
        herdr("agent", "focus", pane)
        show_herdr()
        self.close()

    @staticmethod
    def _notify(title, body, ok=True):
        run_detached(["notify-send", "-a", "MCP", "-i",
                      "dialog-information" if ok else "dialog-error", title, body])
        return False

    def _on_filter(self, entry):
        self.filter_text = entry.get_text().strip().lower()
        self._render()


def main():
    run_popup(McpMenu, "waybar-mcp-menu")


if __name__ == "__main__":
    main()
