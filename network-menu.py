#!/usr/bin/env python3
"""Popup Réseau pour Waybar (style menu luminosité / notifications).

Fenêtre overlay ancrée en haut à droite avec :
  - interrupteur Wi-Fi (radio on/off)
  - réseau connecté mis en évidence
  - liste des réseaux scannés, cliquables :
      * réseau connu / ouvert  -> connexion directe
      * réseau sécurisé inconnu -> champ mot de passe en ligne
      * clic droit -> se déconnecter / oublier le réseau
  - liens filaires (état du câble, connexion / déconnexion)
  - profils VPN et WireGuard, activables d'un clic
  - bouton « Rafraîchir » (rescan)
  - actions : Connexions (GUI), nmtui, Redémarrer NetworkManager, Recharger driver

Tout appel à nmcli passe par un thread de travail : `nmcli dev wifi list
--rescan yes` prend ~7 s sur cette machine, et le faire dans la boucle GTK
gelait la fenêtre (clics ignorés, fenêtre marquée non-répondante par le
compositeur) — c'était la cause principale des « bugs » du menu.
"""
import os
import re
import shlex
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from menu_common import (Card, LayerPopup,  # noqa: E402
                         caption_label, custom_row, run_popup,
                         section_label)

DEVNULL = subprocess.DEVNULL

# nmcli peut rester bloqué si NetworkManager ne répond pas (D-Bus occupé,
# driver planté) : sans timeout le thread fuit et le menu ne se rafraîchit
# plus jamais.
NMCLI_TIMEOUT = 12
CONNECT_TIMEOUT = 45
CONNECT_WAIT = "30"      # nmcli -w : borne l'attente côté NetworkManager
POLL_SECONDS = 8         # rafraîchissement passif (sans rescan) du menu ouvert


def run(cmd, timeout=NMCLI_TIMEOUT):
    """Exécute une commande et renvoie sa sortie (str), '' en cas d'échec."""
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL,
                                       timeout=timeout)
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


def wired_links():
    """Interfaces filaires réelles, avec leur état. [(device, state, conn)]

    Le module de la barre s'appelle `network` et affiche l'ethernet ; le menu,
    lui, ne parlait que du Wi-Fi. Brancher un câble ne se voyait donc nulle
    part, et il fallait ouvrir nmtui pour savoir si le lien était monté.

    Les ponts et les `veth` de Docker sont écartés : ce sont des interfaces
    ethernet aux yeux de NetworkManager, mais il n'y a rien à y faire depuis un
    menu de barre — et elles noieraient la vraie carte réseau sous dix lignes.
    """
    links = []
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                     "device"]).splitlines():
        f = parse_terse(line)
        if len(f) < 4 or f[1] != "ethernet":
            continue
        if f[2].startswith("unmanaged") or f[0].startswith(("veth", "docker",
                                                            "br-")):
            continue
        links.append((f[0], f[2], f[3]))
    return links


def vpn_connections():
    """Profils VPN / WireGuard connus, actifs d'abord. [(name, uuid, active)]"""
    active = set()
    for line in run(["nmcli", "-t", "-f", "NAME,TYPE",
                     "connection", "show", "--active"]).splitlines():
        f = parse_terse(line)
        if len(f) >= 2 and f[1] in ("vpn", "wireguard"):
            active.add(f[0])
    vpns = []
    for line in run(["nmcli", "-t", "-f", "NAME,UUID,TYPE",
                     "connection", "show"]).splitlines():
        f = parse_terse(line)
        if len(f) >= 3 and f[2] in ("vpn", "wireguard"):
            vpns.append((f[0], f[1], f[0] in active))
    vpns.sort(key=lambda v: (not v[2], v[0].lower()))
    return vpns


def wifi_driver(iface):
    link = "/sys/class/net/%s/device/driver" % iface
    if iface and os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return ""


def wifi_ready():
    """Vrai dès qu'une interface Wi-Fi est utilisable (sortie d'`unavailable`)."""
    for line in run(["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                    timeout=5).splitlines():
        f = parse_terse(line)
        if len(f) >= 2 and f[0] == "wifi" and f[1] not in ("unavailable",
                                                           "unmanaged"):
            return True
    return False


def radio_on():
    return run(["nmcli", "radio", "wifi"]).strip() == "enabled"


def known_connections():
    """Renvoie {ssid: uuid} des profils Wi-Fi enregistrés.

    Le SSID est lu dans le profil (`802-11-wireless.ssid`), pas déduit du nom
    de la connexion : NetworkManager suffixe les doublons (« C-3PO 1 »), et
    un profil renommé à la main ne porte plus le nom du réseau. Se fier au nom
    faisait passer un réseau connu pour inconnu — donc redemander le mot de
    passe au lieu de réutiliser le profil.
    """
    wifi = []
    for line in run(["nmcli", "-t", "-f", "UUID,TYPE",
                     "connection", "show"]).splitlines():
        f = parse_terse(line)
        if len(f) >= 2 and "wireless" in f[1]:
            wifi.append(f[0])
    known = {}
    for uuid in wifi:
        ssid = run(["nmcli", "-t", "-g", "802-11-wireless.ssid",
                    "connection", "show", "uuid", uuid], timeout=5).strip()
        if ssid:
            known.setdefault(ssid, uuid)
    return known


def scan_networks(rescan=False):
    """Renvoie [(ssid, signal, secured, active)], un seul item par SSID.

    Les points d'accès d'un même réseau (2.4 / 5 GHz, répéteurs) sont fusionnés
    en gardant le meilleur signal et en propageant le drapeau « actif ».
    Garder simplement la première ligne perdait le `*` quand la borne associée
    n'était pas la plus puissante : le réseau connecté s'affichait alors comme
    déconnecté, et le clic relançait une connexion inutile.
    """
    cmd = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
           "dev", "wifi", "list", "--rescan", "yes" if rescan else "no"]
    agg = {}
    for line in run(cmd, timeout=NMCLI_TIMEOUT if not rescan else 25).splitlines():
        f = parse_terse(line)
        if len(f) < 4:
            continue
        in_use, ssid, sig, sec = f[0], f[1], f[2], f[3]
        if not ssid:
            continue
        try:
            sig = int(sig)
        except ValueError:
            sig = 0
        secured = sec.strip() not in ("", "--")
        active = in_use.strip() == "*"
        prev = agg.get(ssid)
        if prev is None:
            agg[ssid] = [ssid, sig, secured, active]
        else:
            prev[1] = max(prev[1], sig)
            prev[2] = prev[2] or secured
            prev[3] = prev[3] or active
    nets = [tuple(v) for v in agg.values()]
    nets.sort(key=lambda n: (not n[3], -n[1]))  # actif d'abord, puis signal
    return nets


def collect_state(rescan=False):
    """Photographie complète de l'état réseau (appelée hors thread GTK)."""
    state = {"wired": wired_links(), "vpn": vpn_connections()}
    if not radio_on():
        state.update(radio=False, nets=[], known={})
        return state
    state.update(radio=True, nets=scan_networks(rescan),
                 known=known_connections())
    return state


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


def reveal(box):
    """Affiche un conteneur marqué `no-show-all`.

    `gtk_widget_show_all()` retourne immédiatement sur un widget dont la
    propriété no-show-all est vraie : appeler `box.show_all()` sur ces blocs
    (mot de passe, bandeau d'état) ne les affichait donc jamais — cliquer sur
    un réseau sécurisé inconnu ne produisait rien du tout. On montre les
    enfants, puis le conteneur.
    """
    for child in box.get_children():
        child.show_all()
    box.show()


def notify(icon, title, body):
    subprocess.Popen(["notify-send", "-i", icon, title, body],
                     stdout=DEVNULL, stderr=DEVNULL)


class NetworkPopup(LayerPopup):
    """Une carte par question : l'antenne, les réseaux, les outils.

    La liste des réseaux est elle-même une carte, reconstruite à chaque scan.
    C'est ce qui la distingue enfin des actions du bas : avant, réseaux et
    boutons de dépannage formaient une seule colonne de rectangles identiques,
    et rien ne disait où finissait l'un et où commençait l'autre.
    """

    IC_WIFI = "\U000f05a9"      # antenne
    IC_REFRESH = "\U000f0453"   # flèches circulaires
    # Les glyphes de la plage E7xx (icônes « dev ») sont dessinés pour une
    # barre de statut : dans une colonne d'icônes de 22 px ils se lisent comme
    # des taches. On reste sur les Material Design, plus nets à cette taille.
    IC_GUI = "\U000f0493"       # engrenage
    IC_TUI = "\U000f018d"       # console
    IC_RESTART = "\U000f0709"   # redémarrage du service
    IC_DRIVER = "\U000f0a0b"    # puce : le pilote, pas un simple rafraîchissement
    IC_LOCK = "\U000f0341"      # cadenas
    IC_WIRED = "\U000f0200"     # prise réseau
    IC_VPN = "\U000f0582"       # tunnel chiffré

    def __init__(self):
        super().__init__("Wi-Fi", width=360, margin_right=110)
        self.iface = ""
        self._closed = False
        self._scan_gen = 0       # invalide les scans dont le résultat arrive tard
        self._busy = False       # opération bloquante (connexion, radio, oubli)
        self._scanning = False   # scan en cours : la liste reste utilisable
        self._sig = None         # signature de la liste affichée (anti-clignotement)
        self._state = {"radio": False, "nets": [], "known": {},
                       "wired": [], "vpn": []}
        self._extras_sig = None  # signature des cartes filaire / VPN
        self._pending = None     # SSID en attente de mot de passe
        self._auth_failed = False  # le dernier échec venait-il du secret ?
        self._sync_switch = False
        self.connect("destroy", self._on_destroy)

        # -- Interrupteur Wi-Fi --
        radio = self.add_card()
        self.sw = radio.toggle(self.IC_WIFI, "Wi-Fi", False, self._on_radio)

        # -- Bandeau d'état (scan / connexion / erreur) --
        self.status_box = Gtk.Box(spacing=8)
        self.status_box.set_no_show_all(True)
        self.status_box.set_margin_start(4)
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.status_box.pack_start(self.spinner, False, False, 0)
        self.status_label = caption_label("", width_chars=38)
        self.status_box.pack_start(self.status_label, True, True, 0)
        self.box.pack_start(self.status_box, False, False, 0)

        # -- Liste scrollable des réseaux --
        # Le conteneur reste en place d'un scan à l'autre ; c'est la carte
        # qu'il porte qui est reconstruite, pour ne pas faire clignoter la
        # barre de défilement à chaque rafraîchissement passif.
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(120)
        self.scroller.set_max_content_height(300)
        self.scroller.set_propagate_natural_height(True)
        self.net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.scroller.add(self.net_box)
        self.box.pack_start(self.scroller, True, True, 0)

        # -- Filaire et VPN --
        # Hors du défilement des réseaux : ce sont des liens qu'on a ou qu'on
        # n'a pas, pas une liste où choisir. Les cartes n'apparaissent que si
        # la machine a effectivement une carte ethernet ou un profil VPN.
        self.extras_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=12)
        self.box.pack_start(self.extras_box, False, False, 0)

        # -- Actions sur un réseau (clic droit), cachées par défaut --
        # Un Gtk.Menu contextuel se positionne mal au-dessus d'une surface
        # layer-shell ; un panneau en ligne, comme la saisie de mot de passe,
        # évite le problème et reste atteignable au clavier.
        self.act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.act_box.set_no_show_all(True)
        self.act_label = Gtk.Label(xalign=0)
        self.act_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.act_label.get_style_context().add_class("section-title")
        self.act_box.pack_start(self.act_label, False, False, 0)
        self.act_btns = Gtk.Box(spacing=8, homogeneous=True)
        self.act_box.pack_start(self.act_btns, False, False, 0)
        self.box.pack_start(self.act_box, False, False, 0)

        # -- Saisie mot de passe (cachée par défaut) --
        self.pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pw_box.set_no_show_all(True)
        self.pw_label = caption_label("")
        self.pw_box.pack_start(self.pw_label, False, False, 0)
        self.pw_entry = Gtk.Entry()
        self.pw_entry.set_visibility(False)
        self.pw_entry.set_placeholder_text("Mot de passe")
        self.pw_entry.connect("activate", lambda *_: self._pw_connect())
        self.pw_box.pack_start(self.pw_entry, False, False, 0)
        pw_btns = Gtk.Box(spacing=8, homogeneous=True)
        cancel = Gtk.Button(label="Annuler")
        cancel.connect("clicked", lambda *_: self._pw_hide())
        self.pw_ok = Gtk.Button(label="Se connecter")
        self.pw_ok.get_style_context().add_class("accent")
        self.pw_ok.connect("clicked", lambda *_: self._pw_connect())
        pw_btns.pack_start(cancel, True, True, 0)
        pw_btns.pack_start(self.pw_ok, True, True, 0)
        self.pw_box.pack_start(pw_btns, False, False, 0)
        self.box.pack_start(self.pw_box, False, False, 0)

        # -- Rafraîchir et outils --
        tools = self.add_card()
        self.refresh_row = tools.action(self.IC_REFRESH, "Rafraîchir",
                                        on_click=self._on_refresh)
        tools.action(self.IC_GUI, "Connexions", value="GUI", chevron=True,
                     on_click=self._open_gui)
        tools.action(self.IC_TUI, "nmtui", chevron=True,
                     on_click=self._open_nmtui)

        # -- Dépannage --
        # Séparé du reste : ces deux actions coupent le réseau le temps de
        # repartir. Les mêler aux raccourcis ci-dessus revenait à les proposer
        # avec le même entrain qu'« ouvrir nmtui ».
        fix = self.add_card("Dépannage")
        fix.action(self.IC_RESTART, "Redémarrer NetworkManager",
                   on_click=self._restart_nm)
        self.driver_row = fix.action(self.IC_DRIVER, "Recharger le driver Wi-Fi",
                                     on_click=self._reload_driver)

        # Le cache de NetworkManager répond en ~20 ms : on l'affiche tout de
        # suite, puis le rescan complet (~7 s) met la liste à jour en fond.
        self._set_status("Recherche des réseaux…", busy=True)
        self._start_scan(rescan=False, first=True, quiet=True, then_rescan=True)
        GLib.timeout_add_seconds(POLL_SECONDS, self._on_poll)

    # ---- Plomberie thread ----

    def _on_destroy(self, *_):
        self._closed = True

    def _in_thread(self, work, done):
        """Exécute `work()` hors boucle GTK, puis `done(result)` dans la boucle."""
        def runner():
            try:
                res = work()
            except Exception as exc:  # remonté tel quel au callback
                res = exc
            GLib.idle_add(self._deliver, done, res)
        threading.Thread(target=runner, daemon=True).start()

    def _deliver(self, done, res):
        if not self._closed:
            done(res)
        return False

    # ---- Bandeau d'état ----

    def _set_status(self, text, busy=False, error=False):
        if not text:
            self.spinner.stop()
            self.status_box.hide()
            return
        markup = GLib.markup_escape_text(text)
        if error:
            markup = "<span foreground='#ff6961'>%s</span>" % markup
        self.status_label.set_markup(markup)
        reveal(self.status_box)
        if busy:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()

    def _set_busy(self, busy):
        """Opération bloquante : la liste et l'interrupteur passent inertes."""
        self._busy = busy
        self.net_box.set_sensitive(not busy)
        self.sw.set_sensitive(not busy)
        self.refresh_row.set_sensitive(not busy and not self._scanning)

    def _set_scanning(self, scanning):
        """Scan en cours : seul « Rafraîchir » se verrouille.

        Le rescan dure ~7 s ; geler la liste pendant ce temps aurait rendu le
        menu inutilisable juste après son ouverture, alors que les réseaux du
        cache sont déjà affichés et parfaitement cliquables.
        """
        self._scanning = scanning
        self.refresh_row.set_sensitive(not scanning and not self._busy)

    # ---- Scan ----

    def _start_scan(self, rescan=False, first=False, quiet=False,
                    then_rescan=False):
        self._scan_gen += 1
        gen = self._scan_gen
        if not quiet:
            self._set_scanning(True)

        def work():
            state = collect_state(rescan=rescan)
            if first:
                state["iface"] = wifi_iface()
                state["driver"] = wifi_driver(state["iface"])
            return state

        def done(res):
            if gen != self._scan_gen:
                return          # un scan plus récent a pris la main
            if not quiet:
                self._set_scanning(False)
            if isinstance(res, Exception):
                if not self._busy:
                    self._set_status("Erreur : %s" % res, error=True)
                return
            if first:
                self.iface = res.get("iface", "")
                drv = res.get("driver", "")
                if drv:
                    # Le nom du pilote est une précision, pas le libellé de
                    # l'action : il descend en valeur, à droite de la ligne.
                    self.driver_row.value_label.set_text(drv)
                    self.driver_row.value_label.set_visible(True)
            self._apply_state(res)
            if then_rescan:
                self._start_scan(rescan=True)
            elif not quiet and not self._busy:
                # Ne pas effacer le message d'une opération en cours (une
                # connexion lancée pendant le scan, par exemple).
                self._set_status("")

        self._in_thread(work, done)

    def _on_poll(self):
        """Rafraîchissement passif : garde signal et réseau actif à jour.

        Sans rescan (lecture du cache de NetworkManager, ~20 ms) et suspendu
        pendant une opération ou une saisie de mot de passe, pour ne pas
        reconstruire la liste sous les doigts de l'utilisateur.
        """
        if self._closed:
            return False
        if not self._busy and not self._scanning and self._pending is None:
            self._start_scan(rescan=False, quiet=True)
        return True

    def _on_refresh(self, _btn):
        self._set_status("Recherche des réseaux…", busy=True)
        self._start_scan(rescan=True)

    # ---- Rendu ----

    def _apply_state(self, state):
        self._state = state
        self._sync_switch = True
        self.sw.set_active(state["radio"])
        self._sync_switch = False
        self._render_nets()
        self._render_extras()

    def _render_extras(self):
        """Cartes « Filaire » et « VPN », reconstruites seulement si besoin."""
        state = self._state
        wired = state.get("wired") or []
        vpn = state.get("vpn") or []
        sig = (tuple(wired), tuple(vpn))
        if sig == self._extras_sig:
            return
        self._extras_sig = sig

        for child in self.extras_box.get_children():
            child.destroy()

        if wired:
            card = Card()
            for dev, st, conn in wired:
                up = st.startswith("connect")
                card.action(
                    self.IC_WIRED, conn if up else dev,
                    value=dev if up else self._wired_label(st),
                    selected=up,
                    tooltip=("Connecté en filaire — clic : déconnecter" if up
                             else "Cliquer pour activer ce lien"),
                    on_click=(lambda _b, d=dev, u=up: self._toggle_wired(d, u)))
            self._add_extra("Filaire", card)

        if vpn:
            card = Card()
            for name, uuid, active in vpn:
                card.action(
                    self.IC_VPN, name, selected=active,
                    value="actif" if active else None,
                    tooltip=("Cliquer pour se déconnecter" if active
                             else "Cliquer pour se connecter"),
                    on_click=(lambda _b, u=uuid, a=active:
                              self._toggle_vpn(u, a)))
            self._add_extra("VPN", card)

        self.extras_box.show_all()

    @staticmethod
    def _wired_label(state):
        if state.startswith("unavailable"):
            return "câble débranché"
        if state.startswith("disconnected"):
            return "inactif"
        return state.split(" ")[0]

    def _add_extra(self, title, card):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.pack_start(section_label(title), False, False, 0)
        wrap.pack_start(card, False, False, 0)
        self.extras_box.pack_start(wrap, False, False, 0)

    def _toggle_wired(self, device, up):
        cmd = (["nmcli", "device", "disconnect", device] if up
               else ["nmcli", "-w", CONNECT_WAIT, "device", "connect", device])
        self._simple_action(cmd, "Déconnexion de %s…" % device if up
                            else "Connexion filaire…")

    def _toggle_vpn(self, uuid, active):
        verb = "down" if active else "up"
        self._simple_action(
            ["nmcli", "-w", CONNECT_WAIT, "connection", verb, "uuid", uuid],
            "Déconnexion du VPN…" if active else "Connexion au VPN…")

    def _simple_action(self, cmd, message):
        """Commande nmcli courte dont on ne veut que le succès ou l'échec."""
        self._set_busy(True)
        self._set_status(message, busy=True)

        def work():
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=CONNECT_TIMEOUT)
            if proc.returncode != 0:
                out = (proc.stdout or proc.stderr or "").strip().splitlines()
                return (False, out[-1] if out else "échec")
            return (True, collect_state(rescan=False))

        def done(res):
            self._set_busy(False)
            if isinstance(res, Exception):
                self._set_status("Erreur : %s" % res, error=True)
                return
            ok, payload = res
            if not ok:
                self._set_status("Échec : %s" % payload, error=True)
                return
            self._set_status("")
            self._sig = None
            self._extras_sig = None
            self._apply_state(payload)

        self._in_thread(work, done)

    def _render_nets(self):
        state = self._state
        sig = (state["radio"],
               tuple((n[0], n[3], n[2], n[1] // 5) for n in state["nets"]))
        if sig == self._sig:
            return          # rien de neuf : on ne casse ni le focus ni le scroll
        self._sig = sig

        focused = self.get_focus()
        focus_ssid = getattr(focused, "_ssid", None)
        adj = self.scroller.get_vadjustment()
        scroll = adj.get_value() if adj else 0

        for child in self.net_box.get_children():
            self.net_box.remove(child)

        if not state["radio"]:
            self._placeholder("Wi-Fi désactivé")
            return
        if not state["nets"]:
            self._placeholder("Aucun réseau détecté")
            return

        known = state["known"]
        card = Card()
        restore = None
        for ssid, level, secured, active in state["nets"]:
            # Le gras signale un profil enregistré — une nuance, pas une
            # étiquette : un sous-titre « Enregistré » sur la moitié des
            # lignes doublerait leur hauteur pour une information que l'infobulle
            # donne déjà.
            name = GLib.markup_escape_text(ssid)
            if ssid in known:
                name = "<b>%s</b>" % name
            value = "%s %d %%" % (self.IC_LOCK if secured else " ", level)
            row = card.action(
                signal_icon(level), name, markup=True, value=value,
                selected=active,
                tooltip=("Connecté — clic droit : se déconnecter" if active else
                         ("Réseau enregistré — clic droit : oublier le profil"
                          if ssid in known else "Cliquer pour se connecter")))
            row._ssid = ssid
            row.connect("clicked", self._on_net_clicked, ssid, secured)
            row.connect("button-press-event", self._on_net_button, ssid)
            if ssid == focus_ssid:
                restore = row
        self.net_box.pack_start(card, False, False, 0)
        self.net_box.show_all()
        if restore is not None:
            restore.grab_focus()
        if adj:
            GLib.idle_add(lambda: (adj.set_value(scroll), False)[1])

    def _placeholder(self, text):
        """Carte d'une seule ligne : la liste vide garde la forme d'une liste."""
        card = Card()
        card.add_row(custom_row(caption_label(text)))
        self.net_box.pack_start(card, False, False, 0)
        self.net_box.show_all()

    # ---- Handlers réseau ----

    def _on_radio(self, switch, _param):
        if self._sync_switch:
            return          # remise en phase après un scan : pas une action utilisateur
        want = switch.get_active()
        self._set_busy(True)
        self._set_status("Activation du Wi-Fi…" if want else "Désactivation…",
                         busy=True)

        def work():
            subprocess.run(["nmcli", "radio", "wifi", "on" if want else "off"],
                           stdout=DEVNULL, stderr=DEVNULL, timeout=NMCLI_TIMEOUT)
            if not want:
                return collect_state(rescan=False)
            # Le device met une poignée de secondes à quitter l'état
            # `unavailable` : scanner tout de suite renvoyait « Aucun réseau
            # détecté », ce qui donnait un menu vide juste après l'activation.
            deadline = time.time() + 8
            while time.time() < deadline and not wifi_ready():
                time.sleep(0.5)
            return collect_state(rescan=True)

        def done(res):
            self._set_busy(False)
            if isinstance(res, Exception):
                self._set_status("Erreur : %s" % res, error=True)
                return
            self._set_status("")
            self._sig = None
            self._apply_state(res)

        self._in_thread(work, done)

    def _on_net_button(self, _btn, event, ssid):
        if event.button != 3:
            return False
        self._show_actions(ssid)
        return True

    def _show_actions(self, ssid):
        """Panneau d'actions du réseau : déconnexion, oubli du profil."""
        active = any(n[0] == ssid and n[3] for n in self._state["nets"])
        uuid = self._state["known"].get(ssid)
        for child in self.act_btns.get_children():
            self.act_btns.remove(child)
        if not active and not uuid:
            self._hide_actions()
            return
        self.act_label.set_markup(GLib.markup_escape_text(ssid))
        if active:
            btn = Gtk.Button(label="Se déconnecter")
            btn.connect("clicked", lambda *_: (self._hide_actions(),
                                               self._disconnect()))
            self.act_btns.pack_start(btn, True, True, 0)
        if uuid:
            btn = Gtk.Button(label="Oublier")
            btn.connect("clicked", lambda *_: (self._hide_actions(),
                                               self._forget(ssid, uuid)))
            self.act_btns.pack_start(btn, True, True, 0)
        close = Gtk.Button(label="Annuler")
        close.connect("clicked", lambda *_: self._hide_actions())
        self.act_btns.pack_start(close, True, True, 0)
        reveal(self.act_box)
        self.act_btns.get_children()[0].grab_focus()

    def _hide_actions(self):
        self.act_box.hide()

    def _on_net_clicked(self, _btn, ssid, secured):
        self._hide_actions()
        if any(n[0] == ssid and n[3] for n in self._state["nets"]):
            return          # déjà connecté (déconnexion = clic droit)
        uuid = self._state["known"].get(ssid)
        if uuid:
            self._connect(ssid, cmd=["nmcli", "-w", CONNECT_WAIT,
                                     "connection", "up", "uuid", uuid])
        elif secured:
            self._prompt_password(ssid)
        else:
            self._connect(ssid, cmd=self._connect_cmd(ssid))

    def _connect_cmd(self, ssid, ask=False):
        cmd = ["nmcli", "-w", CONNECT_WAIT]
        if ask:
            cmd.append("--ask")
        cmd += ["device", "wifi", "connect", ssid]
        if self.iface:
            cmd += ["ifname", self.iface]
        return cmd

    # nmcli ne distingue pas les causes par son code de retour (4 couvre tout
    # échec d'activation) : le motif du message est le seul indice fiable d'un
    # secret refusé, par opposition à un point d'accès qui redémarre.
    AUTH_FAIL_RE = re.compile(
        r"secrets? (were|was) required|no secrets provided|"
        r"802\.1x supplicant|invalid.*(password|key)|"
        r"authentication|clé.*(incorrect|invalide)|mot de passe",
        re.I)

    def _connect(self, ssid, cmd, password=None, delete_uuid=None):
        """Lance nmcli hors boucle GTK et rend compte dans la fenêtre.

        Le mot de passe n'est jamais passé en argument : il transite par stdin
        (`nmcli --ask`), sinon il serait lisible par n'importe quel processus
        de la machine via /proc/<pid>/cmdline.
        """
        self._set_busy(True)
        self.pw_box.hide()
        self._set_status("Connexion à %s…" % ssid, busy=True)
        if delete_uuid:
            # L'uuid ne vaudra plus rien dans un instant : l'oublier tout de
            # suite, sinon un second clic rapide relancerait `connection up`
            # sur un profil supprimé.
            self._state["known"].pop(ssid, None)

        def work():
            if delete_uuid:
                subprocess.run(["nmcli", "connection", "delete",
                                "uuid", delete_uuid],
                               stdout=DEVNULL, stderr=DEVNULL,
                               timeout=NMCLI_TIMEOUT)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if password is not None else DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                out, _ = proc.communicate(
                    (password + "\n") if password is not None else None,
                    timeout=CONNECT_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return (False, "délai dépassé")
            if proc.returncode == 0:
                return (True, "")
            msg = (out or "").strip().splitlines()
            last = msg[-1] if msg else "échec de connexion"
            return (False, last, bool(self.AUTH_FAIL_RE.search(out or "")))

        def done(res):
            self._set_busy(False)
            if isinstance(res, Exception):
                self._set_status("Erreur : %s" % res, error=True)
                return
            ok, msg = res[0], res[1]
            self._auth_failed = res[2] if len(res) > 2 else False
            if ok:
                notify("network-wireless", "Wi-Fi", "Connecté à %s" % ssid)
                self.close()
                return
            # Échec : on garde la fenêtre ouverte pour réessayer — refermer
            # obligeait à tout recommencer depuis l'icône de la barre.
            self._set_status("Échec : %s" % msg, error=True)
            self._sig = None
            self._start_scan(rescan=False, quiet=True)
            if password is not None or self._state["known"].get(ssid):
                self._prompt_password(ssid, retry=True)

        self._in_thread(work, done)

    def _disconnect(self):
        if not self.iface:
            return
        self._set_busy(True)
        self._set_status("Déconnexion…", busy=True)

        def work():
            subprocess.run(["nmcli", "device", "disconnect", self.iface],
                           stdout=DEVNULL, stderr=DEVNULL, timeout=NMCLI_TIMEOUT)
            return collect_state(rescan=False)

        def done(res):
            self._set_busy(False)
            self._set_status("")
            if not isinstance(res, Exception):
                self._sig = None
                self._apply_state(res)

        self._in_thread(work, done)

    def _forget(self, ssid, uuid):
        self._set_busy(True)
        self._set_status("Suppression du profil %s…" % ssid, busy=True)

        def work():
            subprocess.run(["nmcli", "connection", "delete", "uuid", uuid],
                           stdout=DEVNULL, stderr=DEVNULL, timeout=NMCLI_TIMEOUT)
            return collect_state(rescan=False)

        def done(res):
            self._set_busy(False)
            self._set_status("")
            if not isinstance(res, Exception):
                self._sig = None
                self._apply_state(res)

        self._in_thread(work, done)

    # ---- Mot de passe ----

    def _prompt_password(self, ssid, retry=False):
        self._hide_actions()
        self._pending = ssid
        label = ("Mot de passe incorrect ? Ressaisir pour <b>%s</b>" if retry
                 else "Mot de passe pour <b>%s</b>")
        self.pw_label.set_markup(label % GLib.markup_escape_text(ssid))
        self.pw_entry.set_text("")
        reveal(self.pw_box)
        self.pw_entry.grab_focus()

    def _pw_hide(self):
        self._pending = None
        self.pw_box.hide()
        self._set_status("")

    def _pw_connect(self):
        ssid = self._pending
        if not ssid:
            return
        pw = self.pw_entry.get_text()
        if not pw:
            return
        self._pending = None
        # Profil existant dont le mot de passe a changé : le supprimer avant de
        # recréer, sinon `connection up` réutilise l'ancien secret et échoue en
        # boucle. La suppression a lieu dans le thread de connexion, pour ne pas
        # courir contre le nmcli suivant.
        #
        # Mais SEULEMENT si le secret a vraiment été refusé : un point d'accès
        # qui redémarre fait lui aussi échouer `connection up` et rouvrir cette
        # invite, et détruire le profil dans ce cas-là perdrait pour de bon un
        # mot de passe correct — l'échec suivant serait identique.
        uuid = self._state["known"].get(ssid) if self._auth_failed else None
        self._connect(ssid, cmd=self._connect_cmd(ssid, ask=True), password=pw,
                      delete_uuid=uuid)

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
            notify("dialog-error", "Réseau", "Driver Wi-Fi introuvable")
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
    run_popup(NetworkPopup, "waybar-network-menu")


if __name__ == "__main__":
    main()
