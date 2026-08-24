#!/usr/bin/env python3
"""Popup Wi-Fi pour Waybar (style menu luminosité / notifications).

Fenêtre overlay ancrée en haut à droite avec :
  - interrupteur Wi-Fi (radio on/off)
  - réseau connecté mis en évidence
  - liste des réseaux scannés, cliquables :
      * réseau connu / ouvert  -> connexion directe
      * réseau sécurisé inconnu -> champ mot de passe en ligne
  - bouton « Rafraîchir » (rescan)
  - actions : Connexions (GUI), nmtui, Redémarrer NetworkManager, Recharger driver
"""
import os
import shlex
import signal
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import LayerPopup  # noqa: E402

DEVNULL = subprocess.DEVNULL


def run(cmd):
    """Exécute une commande et renvoie sa sortie (str), '' en cas d'échec."""
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL)
    except Exception:
        return ""


def parse_terse(line):
    """Découpe une ligne nmcli terse en gérant l'échappement \\: et \\\\."""
    fields, cur, it = [], [], iter(line)
    for ch in it:
        if ch == "\\":
            cur.append(next(it, ""))
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def wifi_iface():
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"]).splitlines():
        dev, _, typ = line.partition(":")
        if typ == "wifi":
            return dev
    return ""


def wifi_driver(iface):
    link = "/sys/class/net/%s/device/driver" % iface
    if iface and os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return ""


def radio_on():
    return run(["nmcli", "radio", "wifi"]).strip() == "enabled"


def known_ssids():
    names = set()
    for line in run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]).splitlines():
        f = parse_terse(line)
        if len(f) >= 2 and "wireless" in f[1]:
            names.add(f[0])
    return names


def scan_networks(rescan=False):
    """Renvoie [(ssid, signal:int, secured:bool, active:bool)] sans doublons."""
    cmd = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
           "dev", "wifi", "list", "--rescan", "yes" if rescan else "no"]
    seen, nets = set(), []
    for line in run(cmd).splitlines():
        f = parse_terse(line)
        if len(f) < 4:
            continue
        in_use, ssid, sig, sec = f[0], f[1], f[2], f[3]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        secured = sec.strip() not in ("", "--")
        try:
            sig = int(sig)
        except ValueError:
            sig = 0
        nets.append((ssid, sig, secured, in_use.strip() == "*"))
    nets.sort(key=lambda n: (not n[3], -n[1]))  # actif d'abord, puis signal
    return nets


def signal_icon(sig):
    if sig >= 80:
        return "󰤨"
    if sig >= 60:
        return "󰤥"
    if sig >= 40:
        return "󰤢"
    if sig >= 20:
        return "󰤟"
    return "󰤯"


class NetworkPopup(LayerPopup):
    def __init__(self):
        super().__init__("Wi-Fi", width=360, margin_right=110)
        # Accent bleu au lieu du peche : plus lisible dans la liste des reseaux.
        self.get_style_context().add_class("blue")
        self.iface = wifi_iface()

        # -- Interrupteur Wi-Fi --
        row = Gtk.Box(spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>󰖩  Wi-Fi</b>")
        row.pack_start(lbl, True, True, 0)
        self.sw = Gtk.Switch()
        self.sw.set_valign(Gtk.Align.CENTER)
        self.sw.set_active(radio_on())
        self.sw.connect("notify::active", self._on_radio)
        row.pack_end(self.sw, False, False, 0)
        self.box.pack_start(row, False, False, 0)

        # -- Liste scrollable des réseaux --
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(120)
        self.scroller.set_max_content_height(280)
        self.scroller.set_propagate_natural_height(True)
        self.net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.scroller.add(self.net_box)
        self.box.pack_start(self.scroller, True, True, 0)

        # -- Saisie mot de passe (cachée par défaut) --
        self.pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pw_box.set_no_show_all(True)
        self.pw_label = Gtk.Label(xalign=0)
        self.pw_box.pack_start(self.pw_label, False, False, 0)
        self.pw_entry = Gtk.Entry()
        self.pw_entry.set_visibility(False)
        self.pw_entry.set_placeholder_text("Mot de passe")
        self.pw_entry.connect("activate", lambda *_: self._pw_connect())
        self.pw_box.pack_start(self.pw_entry, False, False, 0)
        pw_btns = Gtk.Box(spacing=8, homogeneous=True)
        cancel = Gtk.Button(label="Annuler")
        cancel.connect("clicked", lambda *_: self._pw_hide())
        connect = Gtk.Button(label="Se connecter")
        connect.get_style_context().add_class("accent")
        connect.connect("clicked", lambda *_: self._pw_connect())
        pw_btns.pack_start(cancel, True, True, 0)
        pw_btns.pack_start(connect, True, True, 0)
        self.pw_box.pack_start(pw_btns, False, False, 0)
        self.box.pack_start(self.pw_box, False, False, 0)
        self._pending = None

        # -- Rafraîchir --
        refresh = Gtk.Button(label="󰑓  Rafraîchir")
        refresh.connect("clicked", self._on_refresh)
        self.box.pack_start(refresh, False, False, 0)

        # -- Actions --
        self.box.pack_start(Gtk.Separator(), False, False, 0)
        for label, handler in (
            ("󰖩  Connexions (GUI)", self._open_gui),
            ("  nmtui", self._open_nmtui),
            ("󰜉  Redémarrer NetworkManager", self._restart_nm),
            (self._driver_label(), self._reload_driver),
        ):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", handler)
            self.box.pack_start(btn, False, False, 0)

        self._populate()

    # ---- Construction de la liste ----

    def _driver_label(self):
        drv = wifi_driver(self.iface)
        return "󰑓  Recharger driver Wi-Fi (%s)" % drv if drv else "󰑓  Recharger driver Wi-Fi"

    def _clear_net_box(self):
        for child in self.net_box.get_children():
            self.net_box.remove(child)

    def _populate(self, rescan=False):
        self._clear_net_box()
        if not radio_on():
            info = Gtk.Label(xalign=0)
            info.set_markup("<i>Wi-Fi désactivé</i>")
            self.net_box.pack_start(info, False, False, 0)
            self.net_box.show_all()
            return

        known = known_ssids()
        nets = scan_networks(rescan=rescan)
        if not nets:
            info = Gtk.Label(xalign=0)
            info.set_markup("<i>Aucun réseau détecté</i>")
            self.net_box.pack_start(info, False, False, 0)
            self.net_box.show_all()
            return

        for ssid, sig, secured, active in nets:
            btn = Gtk.Button()
            if active:
                btn.get_style_context().add_class("accent")
            inner = Gtk.Box(spacing=8)
            left = Gtk.Label(xalign=0)
            lock = "  󰍁" if secured else ""
            check = "󰄬 " if active else ""
            left.set_markup("%s%s  %s%s" % (
                check, signal_icon(sig), GLib.markup_escape_text(ssid), lock))
            inner.pack_start(left, True, True, 0)
            pct = Gtk.Label(label="%d%%" % sig, xalign=1)
            inner.pack_end(pct, False, False, 0)
            btn.add(inner)
            btn.connect("clicked", self._on_net_clicked, ssid, secured, active, known)
            self.net_box.pack_start(btn, False, False, 0)
        self.net_box.show_all()

    # ---- Handlers réseau ----

    def _on_radio(self, switch, _param):
        subprocess.run(["nmcli", "radio", "wifi",
                        "on" if switch.get_active() else "off"],
                       stdout=DEVNULL, stderr=DEVNULL)
        GLib.timeout_add(600, lambda: (self._populate(), False)[1])

    def _on_refresh(self, _btn):
        self._clear_net_box()
        info = Gtk.Label(xalign=0)
        info.set_markup("<i>Recherche…</i>")
        self.net_box.pack_start(info, False, False, 0)
        self.net_box.show_all()
        GLib.timeout_add_seconds(2, lambda: (self._populate(rescan=True), False)[1])

    def _on_net_clicked(self, _btn, ssid, secured, active, known):
        if active:
            return
        if secured and ssid not in known:
            self._prompt_password(ssid)
            return
        if ssid in known:
            self._run_connect(["nmcli", "connection", "up", "id", ssid], ssid)
        else:
            self._run_connect(["nmcli", "device", "wifi", "connect", ssid], ssid)

    def _run_connect(self, cmd, ssid, password=None):
        """Lance nmcli en tâche de fond et notifie le résultat.

        Un éventuel mot de passe n'est jamais passé en argument : il transite
        par stdin (`nmcli --ask`), sinon il serait lisible par n'importe quel
        processus de la machine via /proc/<pid>/cmdline.
        """
        quoted = " ".join(shlex.quote(c) for c in cmd)
        safe = ssid.replace('"', '')
        if password is not None:
            # `read` puis `printf` sont des primitives du shell : le mot de
            # passe ne devient la ligne de commande d'aucun processus.
            quoted = "IFS= read -r __pw; printf '%s\\n' \"$__pw\" | " + quoted
        proc = subprocess.Popen([
            "bash", "-c",
            "%s && notify-send -i network-wireless Wi-Fi \"Connecté à %s\" "
            "|| notify-send -i dialog-error Wi-Fi \"Échec de connexion à %s\""
            % (quoted, safe, safe)
        ], stdin=subprocess.PIPE if password is not None else DEVNULL,
           stdout=DEVNULL, stderr=DEVNULL)
        if password is not None:
            try:
                proc.stdin.write((password + "\n").encode())
                proc.stdin.close()
            except OSError:
                pass
        self.close()

    # ---- Mot de passe ----

    def _prompt_password(self, ssid):
        self._pending = ssid
        self.pw_label.set_markup(
            "Mot de passe pour <b>%s</b>" % GLib.markup_escape_text(ssid))
        self.pw_entry.set_text("")
        self.pw_box.show_all()
        self.pw_entry.grab_focus()

    def _pw_hide(self):
        self._pending = None
        self.pw_box.hide()

    def _pw_connect(self):
        if not self._pending:
            return
        pw = self.pw_entry.get_text()
        if not pw:
            return
        self._run_connect(
            ["nmcli", "--ask", "device", "wifi", "connect", self._pending],
            self._pending, password=pw)

    # ---- Actions ----

    def _open_gui(self, _btn):
        subprocess.Popen(["nm-connection-editor"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_nmtui(self, _btn):
        subprocess.Popen(["kitty", "--title", "nmtui", "-e", "nmtui"],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _restart_nm(self, _btn):
        subprocess.Popen([
            "bash", "-c",
            "pkexec systemctl restart NetworkManager "
            "&& notify-send -i network-wireless Réseau 'NetworkManager redémarré' "
            "|| notify-send -i dialog-error Réseau 'Échec du redémarrage de NetworkManager'"
        ], stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _reload_driver(self, _btn):
        drv = wifi_driver(self.iface)
        if not drv:
            subprocess.Popen(["notify-send", "-i", "dialog-error",
                              "Réseau", "Driver Wi-Fi introuvable"])
            return
        if drv == "ath11k_pci":
            inner = ("rmmod ath11k_pci 2>/dev/null; rmmod ath11k 2>/dev/null; "
                     "sleep 1; modprobe ath11k_pci")
        else:
            inner = "modprobe -r %s; sleep 1; modprobe %s" % (drv, drv)
        subprocess.Popen([
            "bash", "-c",
            "pkexec sh -c %s "
            "&& notify-send -i network-wireless Réseau 'Driver %s rechargé' "
            "|| notify-send -i dialog-error Réseau 'Échec du rechargement de %s'"
            % (shlex.quote(inner), drv, drv)
        ], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    NetworkPopup().run()


if __name__ == "__main__":
    main()
