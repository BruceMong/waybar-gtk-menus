#!/usr/bin/env python3
"""Popup « Unités en échec » pour Waybar (style menu luminosité).

Ouvert par le module systemd-failed-units (icône 󰀪). Liste les unités systemd
system + user en état failed, avec pour chacune : le journal, un redémarrage
et un acquittement (reset-failed).
"""
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

DEVNULL = subprocess.DEVNULL
TERM_CLASS = "journal-view"


def _systemctl(scope, *args):
    """Construit une commande systemctl pour la portée demandée."""
    base = ["systemctl"]
    if scope == "user":
        base.append("--user")
    return base + list(args)


def failed_units():
    """Retourne [(scope, unit, description)] pour system puis user."""
    units = []
    for scope in ("system", "user"):
        try:
            out = subprocess.check_output(
                _systemctl(scope, "list-units", "--failed",
                           "--no-legend", "--plain", "--no-pager"),
                text=True, stderr=DEVNULL)
        except Exception:
            continue
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            unit = parts[0]
            desc = parts[4] if len(parts) > 4 else ""
            units.append((scope, unit, desc))
    return units


def unit_error(scope, unit):
    """Résumé du journal de l'unité : les dernières lignes d'erreur utiles."""
    cmd = ["journalctl", "-u", unit, "-n", "25", "--no-pager", "-o", "cat",
           "-p", "warning"]
    if scope == "user":
        cmd.insert(1, "--user")
    try:
        out = subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""

    lines = []
    for line in out.splitlines():
        line = line.strip()
        # Les core dumps crachent des stack traces entières : inutile ici.
        if not line or line.startswith("#") or line.startswith("Stack trace"):
            continue
        if len(line) > 90:
            line = line[:89] + "…"
        lines.append(line)

    # Les lignes émises par systemd lui-même ("unit.service: ...") portent la
    # cause réelle de l'échec ; on les privilégie sur le bruit applicatif.
    own = [l for l in lines if l.startswith(unit + ":")]
    keep = own or lines
    return "\n".join(keep[-3:])


class SystemdPopup(LayerPopup):
    def __init__(self):
        super().__init__("Unités en échec", width=420, margin_right=210)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.pack_start(self.content, True, True, 0)
        self._build()

    # ---- Construction ----

    def _build(self):
        for child in self.content.get_children():
            child.destroy()

        units = failed_units()
        if not units:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#a6e3a1'>󰄬  "
                           "Aucune unité en échec</span>")
            self.content.pack_start(lbl, False, False, 0)
            self.content.show_all()
            return

        for scope, unit, desc in units:
            self.content.pack_start(self._unit_row(scope, unit, desc),
                                    False, False, 0)

        if len(units) > 1:
            btn = Gtk.Button(label="󰄬  Tout acquitter")
            btn.connect("clicked", lambda *_: self._reset_all(units))
            self.content.pack_start(btn, False, False, 0)

        self.content.show_all()

    def _unit_row(self, scope, unit, desc):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        title = Gtk.Label(xalign=0)
        tag = "system" if scope == "system" else "user"
        title.set_markup(
            "<b><span foreground='#f38ba8'>%s</span></b>"
            "  <span foreground='#6c7086' size='small'>%s</span>"
            % (GLib.markup_escape_text(unit), tag))
        title.set_line_wrap(True)
        frame.pack_start(title, False, False, 0)

        if desc:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#a6adc8' size='small'>%s</span>"
                           % GLib.markup_escape_text(desc))
            lbl.set_line_wrap(True)
            frame.pack_start(lbl, False, False, 0)

        err = unit_error(scope, unit)
        if err:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#f9e2af' size='x-small'>"
                           "<tt>%s</tt></span>" % GLib.markup_escape_text(err))
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(52)
            lbl.set_selectable(True)
            frame.pack_start(lbl, False, False, 0)

        actions = Gtk.Box(spacing=8, homogeneous=True)
        for label, handler in (
                ("󰋼  Journal", self._journal),
                ("󰑐  Relancer", self._restart),
                ("󰄬  Acquitter", self._reset)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked",
                        lambda _b, h=handler, s=scope, u=unit: h(s, u))
            actions.pack_start(btn, True, True, 0)
        frame.pack_start(actions, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        frame.pack_start(sep, False, False, 0)
        return frame

    # ---- Actions ----

    def _journal(self, scope, unit):
        user = "--user " if scope == "user" else ""
        subprocess.Popen(
            ["setsid", "-f", "kitty", "--class", TERM_CLASS, "-e", "bash", "-c",
             "journalctl %s-u %s -n 200 --no-pager -e; exec bash"
             % (user, unit)],
            stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _restart(self, scope, unit):
        subprocess.run(_systemctl(scope, "restart", unit),
                       stdout=DEVNULL, stderr=DEVNULL)
        GLib.timeout_add(800, self._refresh)

    def _reset(self, scope, unit):
        subprocess.run(_systemctl(scope, "reset-failed", unit),
                       stdout=DEVNULL, stderr=DEVNULL)
        self._refresh()

    def _reset_all(self, units):
        for scope, unit, _desc in units:
            subprocess.run(_systemctl(scope, "reset-failed", unit),
                           stdout=DEVNULL, stderr=DEVNULL)
        self._refresh()

    def _refresh(self):
        self._build()
        self.resize(420, 1)
        return False


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    SystemdPopup().run()


if __name__ == "__main__":
    main()
