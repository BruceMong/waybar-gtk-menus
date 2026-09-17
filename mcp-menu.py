#!/usr/bin/env python3
"""Popup « Serveurs MCP » pour Waybar (style menu luminosité).

Ouvert par le module custom/mcp (icône 󰒍). Une carte par serveur MCP
partagé (unité mcp-shared@<nom>, cf. ~/.local/bin/mcp-shared) : son état,
son port, sa mémoire, depuis quand il tourne, la cause probable s'il est en
panne, et les trois gestes courants — journal, relancer, arrêter.

L'état vient de `mcp-shared status --json`, relu toutes les 8 s tant que le
popup est ouvert, dans un fil séparé : la sonde MCP d'un serveur qui ne
répond plus met jusqu'à 3 s à rendre la main, et la popup ne doit pas se
figer pendant ce temps.
"""
import datetime
import json
import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import (Card, LayerPopup,  # noqa: E402
                         caption_label, control_row, run_popup)

DEVNULL = subprocess.DEVNULL
MCP_SHARED = os.path.expanduser("~/.local/bin/mcp-shared")
CONF = os.path.expanduser("~/.config/mcp-proxy/servers.json")
TERM_CLASS = "journal-view"
REFRESH_MS = 8000
# Même signal que dans config-full : la barre se met à jour aussitôt après
# une action, sans attendre son intervalle de 30 s.
BAR_SIGNAL = "RTMIN+16"

STATE_CSS = """
.row-label.ok, .row-icon.ok { color: #32d74b; }
.row:disabled .row-label.ok, .row:disabled .row-icon.ok { color: #32d74b; }
""".encode()

# Couleur et libellé par état. Le nom du serveur prend la couleur : c'est
# lui qu'on cherche dans la pile de cartes, pas le mot d'état.
STATES = {
    "ok":       ("#32d74b", "répond"),
    "starting": ("#ffd60a", "démarre"),
    "failed":   ("#ff9f0a", "proxy vivant, serveur muet"),
    "down":     ("#ff453a", "arrêté"),
}
STATE_ICONS = {"ok": "\U000f012c", "starting": "\U000f051b",
               "failed": "\U000f0026", "down": "\U000f0026"}


def load_status():
    try:
        out = subprocess.run([MCP_SHARED, "status", "--json"], text=True,
                             capture_output=True, timeout=25).stdout
        return json.loads(out or "[]")
    except Exception:
        return []


def unit_error(name):
    """Dernières lignes parlantes du journal d'un serveur en panne.

    mcp-proxy crache des traces Python entières ; ce qui renseigne, c'est la
    ligne du serveur lui-même (« Authentication failed », « token exchange
    failed »), puis celle de systemd (« start-limit-hit »). Les lignes du
    serveur passent d'abord : c'est la cause, systemd n'en est que l'écho.
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
        # Traces Python et Node : cadres, sources, chevrons — aucun ne dit
        # pourquoi, seule la ligne du message le dit.
        if not line or line.startswith((
                "Traceback", "Exception Group", "ExceptionGroup", "File \"",
                "^", "+", "<", "raise ", "at ", "throw ", "node:", "npm notice",
                "status:", "signal:", "output:", "stdout:", "stderr:", "pid:")):
            continue
        if line.startswith("{"):
            # Journaux JSON (slack-mcp-server) : le message et l'erreur suffisent.
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
            continue                       # « Connection closed » : conséquence, pas cause
        if len(line) > 96:
            line = line[:95] + "…"
        target = sysd if line.startswith(("mcp-shared@", "Failed to start")) else app
        if line not in target:
            target.append(line)
    return "\n".join(app[-2:] + sysd[-1:])


def since_label(ts):
    """« depuis 12 min » à partir de l'horodatage systemd."""
    if not ts:
        return ""
    try:
        # « Wed 2026-09-16 19:56:21 CEST » : on ignore le jour et le fuseau.
        when = datetime.datetime.strptime(" ".join(ts.split()[1:3]),
                                          "%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
    secs = int((datetime.datetime.now() - when).total_seconds())
    if secs < 60:
        return "depuis quelques secondes"
    if secs < 3600:
        return f"depuis {secs // 60} min"
    if secs < 86400:
        return f"depuis {secs // 3600} h"
    return f"depuis {secs // 86400} j"


class McpPopup(LayerPopup):
    """Une carte par serveur, et les gestes qui s'y rapportent dessous."""

    IC_SERVER = "\U000f048d"
    IC_OK = "\U000f012c"
    IC_RESTART = "\U000f0450"
    IC_CONF = "\U000f0493"

    def __init__(self):
        super().__init__("Serveurs MCP partagés", width=420, margin_right=210)
        provider = Gtk.CssProvider()
        provider.load_from_data(STATE_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.pack_start(self.content, True, True, 0)
        self._refresh_src = 0
        self._loading = False
        self._render(load_status())
        self._refresh_src = GLib.timeout_add(REFRESH_MS, self._refresh)
        self.connect("destroy", self._stop_refresh)

    # ---- Rafraîchissement ----

    def _stop_refresh(self, *_):
        if self._refresh_src:
            GLib.source_remove(self._refresh_src)
            self._refresh_src = 0

    def _refresh(self):
        if not self._loading:
            self._loading = True
            threading.Thread(target=self._load_async, daemon=True).start()
        return True

    def _load_async(self):
        servers = load_status()
        GLib.idle_add(self._render, servers)

    def _render(self, servers):
        self._loading = False
        for child in self.content.get_children():
            child.destroy()

        if not os.path.isfile(CONF):
            card = Card()
            card.info(self.IC_CONF, "Aucun serveur MCP partagé",
                      subtitle="config attendue : ~/.config/mcp-proxy/servers.json")
            self.content.pack_start(card, False, False, 0)
            self.content.show_all()
            return False

        for s in servers:
            self.content.pack_start(self._server_card(s), False, False, 0)

        foot = Card()
        ok = sum(1 for s in servers if s.get("state") == "ok")
        if not servers:
            foot.info(self.IC_CONF, "mcp-shared ne répond pas",
                      subtitle="voir : mcp-shared status")
        elif ok == len(servers):
            row = foot.action(self.IC_OK, f"{ok} serveur{'s' if ok > 1 else ''}, tous répondent")
            row.title_label.get_style_context().add_class("ok")
            row.icon_label.get_style_context().add_class("ok")
            row.set_sensitive(False)
        foot.action(self.IC_RESTART, "Tout redémarrer",
                    subtitle="les sessions Claude reconnectent au prochain appel",
                    on_click=lambda *_: self._ctl("restart", "all"))
        self.content.pack_start(foot, False, False, 0)
        self.content.show_all()
        return False

    # ---- Une carte par serveur ----

    def _server_card(self, s):
        card = Card()
        name, state = s["name"], s.get("state", "down")
        color, label = STATES.get(state, STATES["down"])

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        title.set_markup(
            "<b><span foreground='%s'>%s</span></b>"
            "  <span foreground='#77777c' size='small'>%s</span>"
            % (color, GLib.markup_escape_text(name), label))
        head.pack_start(title, False, False, 0)

        parts = [f"port {s['port']}"]
        mem = s.get("memory", 0) // 1048576
        if mem:
            parts.append(f"{mem} Mo")
        since = since_label(s.get("since"))
        if since and state != "down":
            parts.append(since)
        restarts = s.get("restarts", 0)
        if restarts:
            parts.append(f"{restarts} redémarrage{'s' if restarts > 1 else ''}")
        head.pack_start(caption_label(" · ".join(parts), width_chars=44),
                        False, False, 0)
        row = card.add_row(control_row(STATE_ICONS.get(state, self.IC_SERVER), head))
        if state == "ok":
            row.icon_label.get_style_context().add_class("ok")

        if state != "ok":
            err = unit_error(name)
            if err:
                lbl = Gtk.Label(xalign=0)
                lbl.set_markup("<span foreground='#ffd60a' size='x-small'>"
                               "<tt>%s</tt></span>" % GLib.markup_escape_text(err))
                lbl.set_line_wrap(True)
                lbl.set_max_width_chars(52)
                lbl.set_selectable(True)
                card.custom(lbl)

        actions = Gtk.Box(spacing=8, homogeneous=True)
        stop_or_start = ("\U000f040a  Démarrer", "start") if state == "down" \
            else ("\U000f04db  Arrêter", "stop")
        for text, verb in (("\U000f02fd  Journal", "logs"),
                           ("\U000f0450  Relancer", "restart"),
                           stop_or_start):
            btn = Gtk.Button(label=text)
            if verb == "logs":
                btn.connect("clicked", lambda _b, n=name: self._journal(n))
            else:
                btn.connect("clicked", lambda _b, v=verb, n=name: self._ctl(v, n))
            actions.pack_start(btn, True, True, 0)
        card.custom(actions)
        return card

    # ---- Actions ----

    def _ctl(self, verb, name):
        subprocess.Popen([MCP_SHARED, verb, name], stdout=DEVNULL, stderr=DEVNULL)
        subprocess.Popen(["pkill", f"-{BAR_SIGNAL}", "waybar"],
                         stdout=DEVNULL, stderr=DEVNULL)
        # Le relancement prend quelques secondes : une relecture immédiate
        # montrerait l'ancien état. Un seul rappel différé — `_refresh` rend
        # True pour le cycle périodique, ce qui ferait de ce rappel une
        # seconde boucle permanente à chaque clic.
        GLib.timeout_add(2500, lambda: (self._refresh(), False)[1])

    def _journal(self, name):
        subprocess.Popen(
            ["setsid", "-f", "kitty", "--class", TERM_CLASS, "-e", "bash", "-c",
             "journalctl --user -u mcp-shared@%s.service -n 200 --no-pager -e; exec bash"
             % name],
            stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(McpPopup, "waybar-mcp-menu")


if __name__ == "__main__":
    main()
