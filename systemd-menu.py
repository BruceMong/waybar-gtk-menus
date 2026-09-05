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
import shlex
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import (Card, LayerPopup,  # noqa: E402
                         caption_label, clipboard_copy, control_row, run_popup)

DEVNULL = subprocess.DEVNULL
TERM_CLASS = "journal-view"
OK_CSS = """
.row-label.ok { color: #32d74b; }
.row-icon.ok  { color: #32d74b; }
/* Une ligne rendue inerte reste lisible : ici l'insensibilité ne signale pas
   une action indisponible, mais une ligne qui n'est qu'un constat. */
.row:disabled .row-label.ok, .row:disabled .row-icon.ok { color: #32d74b; }
""".encode()
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

    Délègue à menu_common.clipboard_copy, qui sort wl-copy du control-group
    de waybar — sans quoi le rapport copié disparaissait du presse-papier au
    premier redémarrage de la barre.
    """
    return clipboard_copy(text)


class SystemdPopup(LayerPopup):
    """Une carte par unité en échec : le diagnostic et ses issues d'un bloc.

    Les unités s'enchaînaient auparavant séparées par un simple filet, chacune
    étalant nom, description, extrait de journal et quatre boutons sur toute
    la largeur — sur deux unités, on ne savait plus quel bouton appartenait à
    laquelle. La carte répond à cette question sans qu'on ait à la poser.
    """

    IC_FAILED = "\U000f0026"    # alerte
    IC_OK = "\U000f012c"        # coche
    IC_COPY = "\U000f018f"      # copier
    IC_ACK = "\U000f012c"       # acquitter

    def __init__(self):
        super().__init__("Unités en échec", width=420, margin_right=210)
        provider = Gtk.CssProvider()
        provider.load_from_data(OK_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.pack_start(self.content, True, True, 0)
        self._build()

    # ---- Construction ----

    def _build(self):
        for child in self.content.get_children():
            child.destroy()

        units = failed_units()
        if not units:
            card = Card()
            row = card.action(self.IC_OK, "Aucune unité en échec")
            row.title_label.get_style_context().add_class("ok")
            row.icon_label.get_style_context().add_class("ok")
            row.set_sensitive(False)
            self.content.pack_start(card, False, False, 0)
            self.content.show_all()
            return

        for scope, unit, desc in units:
            self.content.pack_start(self._unit_card(scope, unit, desc),
                                    False, False, 0)

        if len(units) > 1:
            card = Card()
            card.action(self.IC_ACK, "Tout acquitter",
                        on_click=lambda *_: self._reset_all(units))
            row = card.action(self.IC_COPY, "Copier tous les rapports")
            row.connect("clicked", lambda b, u=units: self._copy_all(b, u))
            self.content.pack_start(card, False, False, 0)

        self.content.show_all()

    def _unit_card(self, scope, unit, desc):
        card = Card()

        # -- En-tête : le nom de l'unité, sa portée, ce qu'elle fait --
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        # #77777c et non rgba(235,235,245,0.4) : le markup Pango ne connaît pas
        # rgba(), il rejette l'attribut ET tout le markup avec lui — le titre
        # de la carte s'affichait alors en texte brut, balises comprises
        # (« Failed to set text ... could not be parsed » dans le journal).
        # C'est l'équivalent opaque du gris à 40 % sur le fond d'une carte,
        # calculé comme les DIM/FAINT de claude-menu.py.
        title.set_markup(
            "<b><span foreground='#ff453a'>%s</span></b>"
            "  <span foreground='#77777c' size='small'>%s</span>"
            % (GLib.markup_escape_text(unit), scope))
        title.set_line_wrap(True)
        head.pack_start(title, False, False, 0)
        if desc:
            head.pack_start(caption_label(desc, width_chars=44), False, False, 0)
        card.add_row(control_row(self.IC_FAILED, head))

        # -- Extrait de journal : la cause probable, telle quelle --
        err = unit_error(scope, unit)
        if err:
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("<span foreground='#ffd60a' size='x-small'>"
                           "<tt>%s</tt></span>" % GLib.markup_escape_text(err))
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(52)
            lbl.set_selectable(True)
            card.custom(lbl)

        # -- Les trois gestes courants, côte à côte : ils se comparent --
        actions = Gtk.Box(spacing=8, homogeneous=True)
        for label, handler in (
                ("󰋼  Journal", self._journal),
                ("󰑐  Relancer", self._restart),
                ("  Acquitter", self._reset)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked",
                        lambda _b, h=handler, s=scope, u=unit: h(s, u))
            actions.pack_start(btn, True, True, 0)
        card.custom(actions)

        # -- Le rapport complet : plus qu'un « copier », il mérite sa ligne --
        row = card.action(self.IC_COPY, "Copier le rapport de diagnostic")
        row.connect("clicked",
                    lambda b, s=scope, u=unit: self._copy_report(b, s, u))
        return card

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

    def _copied_feedback(self, row, ok):
        """Confirme la copie dans la ligne elle-même, puis rend son libellé.

        Une notification par-dessus la popup lui ferait perdre le focus, donc
        la fermerait : le retour visuel reste à l'intérieur de la ligne.
        """
        label = row.title_label
        if getattr(row, "_orig_label", None) is None:
            row._orig_label = label.get_text()
        label.set_text("Copié dans le presse-papier" if ok
                       else "Échec de la copie (wl-copy absent ?)")
        row.icon_label.set_text("\U000f012c" if ok else "\U000f0026")
        row.set_sensitive(False)

        def restore():
            if row.get_parent() is not None:
                label.set_text(row._orig_label)
                row.icon_label.set_text(self.IC_COPY)
                row.set_sensitive(True)
            return False

        GLib.timeout_add(1800, restore)

    def _run_async(self, cmds, label, delay=900):
        """Lance des systemctl sans bloquer la boucle GTK, puis reconstruit.

        Une unité de portée *system* passe par polkit : l'agent ouvre sa propre
        fenêtre, ce qui fait perdre le focus au popup — donc le ferme — pendant
        qu'un `subprocess.run` gardait la boucle GTK figée en attendant une
        saisie devenue impossible à voir. Et l'échec (autorisation refusée)
        partait dans /dev/null : le bouton semblait ne rien faire.

        Les commandes sont donc enchaînées dans un shell détaché, et le
        résultat arrive en notification — la seule voie qui survit à la
        fermeture du popup.
        """
        script = " && ".join(" ".join(shlex.quote(a) for a in cmd)
                             for cmd in cmds)
        subprocess.Popen(
            ["bash", "-c",
             "%s || notify-send -i dialog-error 'Unités systemd' %s"
             % (script, shlex.quote("Échec : %s" % label))],
            stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        GLib.timeout_add(delay, self._refresh)

    def _restart(self, scope, unit):
        self._run_async([_systemctl(scope, "restart", unit)],
                        "redémarrage de %s" % unit)

    def _reset(self, scope, unit):
        self._run_async([_systemctl(scope, "reset-failed", unit)],
                        "acquittement de %s" % unit, delay=500)

    def _reset_all(self, units):
        self._run_async(
            [_systemctl(scope, "reset-failed", unit)
             for scope, unit, _desc in units],
            "acquittement des %d unités" % len(units), delay=500)

    def _refresh(self):
        self._build()
        self.resize(420, 1)
        return False


def main():
    run_popup(SystemdPopup, "waybar-systemd-menu")


if __name__ == "__main__":
    main()
