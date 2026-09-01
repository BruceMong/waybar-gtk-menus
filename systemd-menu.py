#!/usr/bin/env python3
"""Popup « Unités en échec » pour Waybar (style menu luminosité).

Ouvert par le module systemd-failed-units (icône ). Liste les unités systemd
system + user en état failed, avec pour chacune : le journal, un redémarrage,
un acquittement (reset-failed) et la copie d'un rapport de diagnostic complet
dans le presse-papier, à coller tel quel dans un chat avec un assistant.
"""
import datetime
import os
import re
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

DEVNULL = subprocess.DEVNULL
TERM_CLASS = "journal-view"
# Au-delà, le presse-papier devient impossible à relire : on tronque.
REPORT_MAX = 16000


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


def _run(cmd, limit=None):
    """Exécute une commande et retourne sa sortie (vide si elle échoue)."""
    try:
        out = subprocess.run(cmd, text=True, capture_output=True,
                             timeout=10).stdout.strip()
    except Exception as exc:
        return "(commande indisponible : %s)" % exc
    if limit and len(out) > limit:
        out = out[:limit] + "\n… (tronqué)"
    return out or "(vide)"


def error_report(scope, unit):
    """Rapport de diagnostic autoportant pour une unité en échec.

    Tout ce qu'il faut pour comprendre la panne sans accès à la machine :
    l'état, le journal, la définition de l'unité (drop-ins compris) et les
    fichiers de configuration qu'elle référence.
    """
    user = ["--user"] if scope == "user" else []
    jctl = ["journalctl"] + user

    # Pas de `Environment` : `systemctl show` en rend les valeurs en clair, et
    # ce rapport est fait pour être collé ailleurs — jetons, mots de passe et
    # clés d'API des unités user partiraient avec. `EnvironmentFiles` donne le
    # chemin, ce qui suffit au diagnostic sans rien divulguer.
    props = ("ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,"
             "NRestarts,LoadState,UnitFileState,FragmentPath,DropInPaths,"
             "TriggeredBy,Requires,After,Conflicts,Type,ExecStart,"
             "EnvironmentFiles,WorkingDirectory,User,ConditionResult")

    parts = [
        "=== Rapport d'échec systemd ===",
        "Unité     : %s (portée %s)" % (unit, scope),
        "Généré le : %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Hôte      : %s" % _run(["uname", "-n"]),
        "Noyau     : %s" % _run(["uname", "-r"]),
        "systemd   : %s" % _run(["systemctl", "--version"]).splitlines()[0],
        "",
        "--- systemctl status ---",
        _run(_systemctl(scope, "status", unit, "--no-pager", "-n", "0"), 2000),
        "",
        "--- Propriétés ---",
        _run(_systemctl(scope, "show", unit, "-p", props), 2500),
        "",
        "--- Journal de l'unité (80 dernières lignes) ---",
        _run(jctl + ["-u", unit, "-n", "80", "--no-pager"], 6000),
        "",
        "--- Définition de l'unité et drop-ins (systemctl cat) ---",
        _run(_systemctl(scope, "cat", unit, "--no-pager"), 4000),
    ]

    extra = _config_files(scope, unit)
    if extra:
        parts += ["", "--- Fichiers de configuration référencés "
                      "(valeurs sensibles masquées) ---", extra]

    report = "\n".join(parts)
    if len(report) > REPORT_MAX:
        report = report[:REPORT_MAX] + "\n… (rapport tronqué)"
    return report


# Clés dont la valeur ne doit jamais quitter la machine. Le rapport étant
# destiné à être collé dans un chat, on préfère un faux positif (une ligne
# masquée pour rien) à un secret divulgué.
_SECRET_WORDS = r"pass|pwd|secret|token|api[-_ ]?key|auth|credential|private"
ASSIGN_RE = re.compile(
    r"^(\s*[-\w.]*(?:%s)[-\w.]*\s*[:=]\s*)(\S.*)$" % _SECRET_WORDS, re.I)


def _redact(body):
    """Masque la valeur des lignes `clé = valeur` dont la clé paraît sensible."""
    out = []
    for line in body.splitlines():
        m = ASSIGN_RE.match(line)
        out.append("%s(masqué)" % m.group(1) if m else line)
    return "\n".join(out)


def _config_files(scope, unit):
    """Contenu des fichiers de conf que l'ExecStart de l'unité référence.

    reflector, par exemple, lit @/etc/xdg/reflector/reflector.conf : sans ce
    fichier le rapport ne dit rien de ce que l'unité essayait vraiment de faire.
    """
    execline = _run(_systemctl(scope, "show", unit, "-p", "ExecStart",
                               "--value"))
    blocks = []
    seen = set()
    for token in execline.replace("@/", " /").split():
        path = token.lstrip("@").strip('"\'')
        if not path.startswith("/") or path in seen:
            continue
        if not os.path.isfile(path) or path.startswith(("/usr/bin", "/bin",
                                                        "/usr/sbin", "/sbin")):
            continue
        seen.add(path)
        try:
            with open(path, errors="replace") as fh:
                body = fh.read(2000)
        except Exception as exc:
            body = "(illisible : %s)" % exc
        blocks.append("# %s\n%s" % (path, _redact(body.strip())))
    return "\n\n".join(blocks)


def copy_to_clipboard(text):
    """Pousse le texte dans le presse-papier Wayland.

    wl-copy se détache tout seul pour continuer à servir la sélection après
    la fermeture de la popup : on n'attend surtout pas qu'il se termine.
    """
    try:
        proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE,
                                stdout=DEVNULL, stderr=DEVNULL, text=True)
        proc.communicate(text, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


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
            lbl.set_markup("<span foreground='#32d74b'>  "
                           "Aucune unité en échec</span>")
            self.content.pack_start(lbl, False, False, 0)
            self.content.show_all()
            return

        for scope, unit, desc in units:
            self.content.pack_start(self._unit_row(scope, unit, desc),
                                    False, False, 0)

        if len(units) > 1:
            btn = Gtk.Button(label="  Tout acquitter")
            btn.connect("clicked", lambda *_: self._reset_all(units))
            self.content.pack_start(btn, False, False, 0)

            copy_all = Gtk.Button(label="󰆏  Copier tous les rapports")
            copy_all.connect("clicked",
                             lambda b, u=units: self._copy_all(b, u))
            self.content.pack_start(copy_all, False, False, 0)

        self.content.show_all()

    def _unit_row(self, scope, unit, desc):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        title = Gtk.Label(xalign=0)
        tag = "system" if scope == "system" else "user"
        title.set_markup(
            "<b><span foreground='#ff453a'>%s</span></b>"
            "  <span foreground='#68686f' size='small'>%s</span>"
            % (GLib.markup_escape_text(unit), tag))
        title.set_line_wrap(True)
        frame.pack_start(title, False, False, 0)

        if desc:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#9a9aa2' size='small'>%s</span>"
                           % GLib.markup_escape_text(desc))
            lbl.set_line_wrap(True)
            frame.pack_start(lbl, False, False, 0)

        err = unit_error(scope, unit)
        if err:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#ffd60a' size='x-small'>"
                           "<tt>%s</tt></span>" % GLib.markup_escape_text(err))
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(52)
            lbl.set_selectable(True)
            frame.pack_start(lbl, False, False, 0)

        actions = Gtk.Box(spacing=8, homogeneous=True)
        for label, handler in (
                ("󰋼  Journal", self._journal),
                ("󰑐  Relancer", self._restart),
                ("  Acquitter", self._reset)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked",
                        lambda _b, h=handler, s=scope, u=unit: h(s, u))
            actions.pack_start(btn, True, True, 0)
        frame.pack_start(actions, False, False, 0)

        # Rangée dédiée : le rapport complet est plus qu'un simple « copier »,
        # il mérite un bouton pleine largeur et un libellé explicite.
        copy_btn = Gtk.Button(label="󰆏  Copier le rapport de diagnostic")
        copy_btn.connect("clicked",
                         lambda b, s=scope, u=unit: self._copy_report(b, s, u))
        frame.pack_start(copy_btn, False, False, 0)

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

    def _copy_report(self, button, scope, unit):
        report = error_report(scope, unit)
        self._copied_feedback(button, copy_to_clipboard(report))

    def _copy_all(self, button, units):
        report = "\n\n\n".join(error_report(sc, un) for sc, un, _d in units)
        self._copied_feedback(button, copy_to_clipboard(report))

    def _copied_feedback(self, button, ok):
        """Confirme la copie dans le bouton lui-même, puis rend son libellé.

        Une notification par-dessus la popup lui ferait perdre le focus, donc
        la fermerait : le retour visuel reste à l'intérieur du bouton.
        """
        if getattr(button, "_orig_label", None) is None:
            button._orig_label = button.get_label()
        button.set_label("󰄬  Copié dans le presse-papier" if ok
                         else "󰀦  Échec de la copie (wl-copy absent ?)")
        button.set_sensitive(False)

        def restore():
            if button.get_parent() is not None:
                button.set_label(button._orig_label)
                button.set_sensitive(True)
            return False

        GLib.timeout_add(1800, restore)

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
