#!/usr/bin/env python3
"""Popup Bluetooth pour Waybar — même vocabulaire que les autres menus.

Le module bluetooth était le seul de la barre à ouvrir une application
extérieure (blueman-manager) : une fenêtre de gestion complète, avec sa propre
barre d'outils et ses propres conventions, pour ce qui se résume presque
toujours à « rallume le casque ». Ce popup couvre ce geste-là :

  - interrupteur de l'adaptateur (bluetoothctl power on/off) ;
  - appareils appairés, connectés en tête, avec leur batterie quand ils
    l'exposent — cliquer connecte ou déconnecte ;
  - appareils détectés à proximité pendant un scan, cliquer appaire puis
    connecte dans la foulée ;
  - clic droit sur un appareil appairé : le faire oublier ;
  - blueman-manager reste accessible en bas, pour tout le reste.

Comme dans le menu Wi-Fi, chaque appel à bluetoothctl part dans un thread : un
`connect` prend plusieurs secondes et gelait la fenêtre s'il était lancé depuis
la boucle GTK.
"""
import re
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from menu_common import (Card, LayerPopup,  # noqa: E402
                         caption_label, custom_row, run_popup,
                         section_label)

DEVNULL = subprocess.DEVNULL
BTCTL_TIMEOUT = 10
CONNECT_TIMEOUT = 25
SCAN_SECONDS = 12        # durée d'un scan déclenché par « Rechercher »
POLL_SECONDS = 4         # rafraîchissement passif du menu ouvert

# Icônes freedesktop rendues par bluetoothctl (champ « Icon: ») -> glyphe.
ICON_BY_KIND = {
    "audio-headset": "\U000f02cb",
    "audio-headphones": "\U000f02cb",
    "audio-card": "\U000f057e",
    "input-keyboard": "\U000f030c",
    "input-mouse": "\U000f037d",
    "input-gaming": "\U000f0297",
    "phone": "\U000f011c",
    "computer": "\U000f0322",
}
IC_DEFAULT = "\U000f00af"    # symbole Bluetooth


def run(cmd, timeout=BTCTL_TIMEOUT):
    try:
        return subprocess.check_output(cmd, text=True, stderr=DEVNULL,
                                       timeout=timeout)
    except Exception:
        return ""


def powered():
    return "Powered: yes" in run(["bluetoothctl", "show"])


def _parse_devices(out):
    """« Device AA:BB:… Nom » -> [(mac, nom)], dans l'ordre rendu."""
    found = []
    for line in out.splitlines():
        m = re.match(r"^Device\s+([0-9A-F:]{17})\s+(.*)$", line.strip(), re.I)
        if m:
            found.append((m.group(1), m.group(2).strip()))
    return found


def device_info(mac):
    """Détail d'un appareil : connecté, appairé, batterie, type."""
    out = run(["bluetoothctl", "info", mac], timeout=5)
    battery = None
    m = re.search(r"Battery Percentage:.*\((\d+)\)", out)
    if m:
        battery = int(m.group(1))
    kind = None
    m = re.search(r"^\s*Icon:\s*(\S+)", out, re.M)
    if m:
        kind = m.group(1)
    name = None
    m = re.search(r"^\s*Alias:\s*(.+)$", out, re.M)
    if m:
        name = m.group(1).strip()
    return {
        "connected": "Connected: yes" in out,
        "paired": "Paired: yes" in out,
        "battery": battery,
        "kind": kind,
        "name": name,
    }


def collect_state():
    """Photographie complète (appelée hors boucle GTK : bluetoothctl est lent).

    Les appareils appairés et ceux simplement détectés arrivent par deux
    commandes distinctes ; un appareil appairé qui repasse à portée figure dans
    les deux, et c'est la liste des appairés qui doit gagner — sans quoi il
    apparaîtrait deux fois, une fois « à connecter » et une fois « à appairer ».
    """
    if not powered():
        return {"powered": False, "paired": [], "nearby": [],
                "anonymous": 0}

    paired = []
    for mac, name in _parse_devices(run(["bluetoothctl", "devices", "Paired"])):
        info = device_info(mac)
        info.update(mac=mac, name=info.get("name") or name)
        paired.append(info)
    # Connectés d'abord, puis par nom : on vient presque toujours pour l'un des
    # deux appareils déjà en service.
    paired.sort(key=lambda d: (not d["connected"], d["name"].lower()))

    known = {d["mac"] for d in paired}
    nearby, anonymous = [], 0
    for mac, name in _parse_devices(run(["bluetoothctl", "devices"])):
        if mac in known:
            continue
        # Un appareil qui n'a pas répondu à la demande de nom est listé par
        # bluetoothctl sous sa propre adresse, tirets à la place des deux-
        # points. Sept sur onze dans un salon ordinaire : ce sont des
        # téléphones en adresse aléatoire et des objets muets, sur lesquels
        # cliquer n'a aucun sens. Les afficher noyait les trois appareils
        # nommés — la seule information utile est qu'il y en a.
        if name.replace("-", ":").upper() == mac.upper():
            anonymous += 1
            continue
        nearby.append({"mac": mac, "name": name, "connected": False,
                       "paired": False, "battery": None, "kind": None})
    nearby.sort(key=lambda d: d["name"].lower())
    return {"powered": True, "paired": paired, "nearby": nearby,
            "anonymous": anonymous}


def glyph(device):
    return ICON_BY_KIND.get(device.get("kind") or "", IC_DEFAULT)


class BluetoothPopup(LayerPopup):
    """Trois cartes : l'antenne, ce qu'on connaît, ce qui passe à portée."""

    IC_BT = "\U000f00af"
    IC_SCAN = "\U000f0453"      # flèches circulaires
    IC_GUI = "\U000f0493"       # engrenage

    def __init__(self):
        super().__init__("Bluetooth", width=340, margin_right=110)
        self._closed = False
        self._busy = False
        self._scanning = False
        self._scan_proc = None
        self._gen = 0
        self._loading = False
        self._sig = None
        self._sync_switch = False
        self._state = {"powered": False, "paired": [], "nearby": [],
                       "anonymous": 0}
        self.connect("destroy", self._on_destroy)

        radio = self.add_card()
        self.sw = radio.toggle(self.IC_BT, "Bluetooth", False, self._on_power)

        self.status_box = Gtk.Box(spacing=8)
        self.status_box.set_no_show_all(True)
        self.status_box.set_margin_start(4)
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.status_box.pack_start(self.spinner, False, False, 0)
        self.status_label = caption_label("", width_chars=36)
        self.status_box.pack_start(self.status_label, True, True, 0)
        self.box.pack_start(self.status_box, False, False, 0)

        # Le conteneur reste en place d'un rafraîchissement à l'autre ; seules
        # les cartes qu'il porte sont reconstruites.
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=12)
        self.box.pack_start(self.list_box, True, True, 0)

        tools = self.add_card()
        self.scan_row = tools.action(self.IC_SCAN, "Rechercher des appareils",
                                     on_click=self._on_scan)
        tools.action(self.IC_GUI, "Gestionnaire", value="blueman",
                     chevron=True, on_click=self._open_blueman)

        self._set_status("Lecture de l'état…", busy=True)
        self._reload()
        GLib.timeout_add_seconds(POLL_SECONDS, self._on_poll)

    # ---- Plomberie thread ----

    def _on_destroy(self, *_):
        self._closed = True
        self._stop_scan()

    def _in_thread(self, work, done):
        def runner():
            try:
                res = work()
            except Exception as exc:
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
        for child in self.status_box.get_children():
            child.show()
        self.status_box.show()
        if busy:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()

    def _set_busy(self, busy):
        self._busy = busy
        self.list_box.set_sensitive(not busy)
        self.sw.set_sensitive(not busy)
        self.scan_row.set_sensitive(not busy and not self._scanning)

    # ---- Lecture d'état ----

    def _reload(self, quiet=False):
        if self._loading:
            return          # une lecture est déjà en vol : elle fera foi
        self._loading = True
        self._gen += 1
        gen = self._gen

        def done(res):
            self._loading = False
            if gen != self._gen or isinstance(res, Exception):
                if isinstance(res, Exception) and not quiet:
                    self._set_status("Erreur : %s" % res, error=True)
                return
            self._apply_state(res)
            if not quiet and not self._busy:
                self._set_status("")

        self._in_thread(collect_state, done)

    def _on_poll(self):
        if self._closed:
            return False
        if not self._busy:
            self._reload(quiet=True)
        return True

    def _apply_state(self, state):
        self._state = state
        self._sync_switch = True
        self.sw.set_active(state["powered"])
        self._sync_switch = False
        self._render()

    # ---- Rendu ----

    def _render(self):
        state = self._state
        sig = (state["powered"],
               tuple((d["mac"], d["connected"], d["battery"])
                     for d in state["paired"]),
               tuple(d["mac"] for d in state["nearby"]),
               state.get("anonymous", 0))
        if sig == self._sig:
            return          # rien de neuf : ni le focus ni le survol ne bougent
        self._sig = sig

        for child in self.list_box.get_children():
            child.destroy()

        if not state["powered"]:
            card = Card()
            card.add_row(custom_row(caption_label("Bluetooth désactivé.")))
            self.list_box.pack_start(card, False, False, 0)
            self.list_box.show_all()
            return

        if state["paired"]:
            card = Card()
            for device in state["paired"]:
                self._device_row(card, device)
            self._titled(card, "Appareils appairés")

        if state["nearby"]:
            card = Card()
            for device in state["nearby"]:
                self._device_row(card, device)
            self._titled(card, "À proximité", note=self._anon_note(state))
        elif not state["paired"]:
            card = Card()
            card.add_row(custom_row(caption_label(
                "Aucun appareil. Lancez une recherche.")))
            self.list_box.pack_start(card, False, False, 0)

        self.list_box.show_all()

    def _titled(self, card, title, note=None):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.pack_start(section_label(title), False, False, 0)
        wrap.pack_start(card, False, False, 0)
        if note:
            wrap.pack_start(caption_label(note), False, False, 0)
        self.list_box.pack_start(wrap, False, False, 0)

    @staticmethod
    def _anon_note(state):
        count = state.get("anonymous", 0)
        if not count:
            return None
        return ("%d appareil sans nom ignoré" if count == 1
                else "%d appareils sans nom ignorés") % count

    def _device_row(self, card, device):
        bits = []
        if device["connected"]:
            bits.append("connecté")
        if device["battery"] is not None:
            bits.append("%d %%" % device["battery"])
        row = card.action(
            glyph(device), device["name"] or device["mac"],
            value=" · ".join(bits) or None,
            selected=device["connected"],
            tooltip=(device["mac"] + ("\nclic droit : oublier cet appareil"
                                      if device["paired"] else "")))
        row.connect("clicked", self._on_device_clicked, device)
        row.connect("button-press-event", self._on_device_button, device)
        return row

    # ---- Actions ----

    def _on_power(self, switch, _param):
        if self._sync_switch:
            return          # remise en phase après lecture : pas un geste
        want = switch.get_active()
        if want == self._state.get("powered"):
            return          # déjà dans cet état : rien à demander à BlueZ
        self._set_busy(True)
        self._set_status("Activation…" if want else "Désactivation…", busy=True)

        def work():
            subprocess.run(["bluetoothctl", "power", "on" if want else "off"],
                           stdout=DEVNULL, stderr=DEVNULL, timeout=BTCTL_TIMEOUT)
            return collect_state()

        def done(res):
            self._set_busy(False)
            self._set_status("")
            if not isinstance(res, Exception):
                self._sig = None
                self._apply_state(res)

        self._in_thread(work, done)

    def _on_device_button(self, _btn, event, device):
        if event.button != 3 or not device["paired"]:
            return False
        self._forget(device)
        return True

    def _on_device_clicked(self, _btn, device):
        if device["connected"]:
            self._act(device, ["bluetoothctl", "disconnect", device["mac"]],
                      "Déconnexion de %s…" % device["name"])
        elif device["paired"]:
            self._act(device, ["bluetoothctl", "connect", device["mac"]],
                      "Connexion à %s…" % device["name"])
        else:
            # Appairage puis connexion : `pair` seul laisse l'appareil connu
            # mais muet, ce qui n'est jamais ce qu'on voulait en cliquant.
            self._act(device, None, "Appairage de %s…" % device["name"],
                      pair=True)

    def _act(self, device, cmd, message, pair=False):
        self._set_busy(True)
        self._set_status(message, busy=True)
        mac = device["mac"]

        def work():
            if pair:
                for step in (["bluetoothctl", "pair", mac],
                             ["bluetoothctl", "trust", mac],
                             ["bluetoothctl", "connect", mac]):
                    proc = subprocess.run(step, capture_output=True, text=True,
                                          timeout=CONNECT_TIMEOUT)
                    if proc.returncode != 0 and step[1] == "pair":
                        return (False, (proc.stdout or proc.stderr or "").strip())
            else:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=CONNECT_TIMEOUT)
                if proc.returncode != 0:
                    return (False, (proc.stdout or proc.stderr or "").strip())
            return (True, collect_state())

        def done(res):
            self._set_busy(False)
            if isinstance(res, Exception):
                self._set_status("Erreur : %s" % res, error=True)
                return
            ok, payload = res
            if not ok:
                last = payload.splitlines()[-1] if payload else "échec"
                self._set_status("Échec : %s" % last, error=True)
                self._reload(quiet=True)
                return
            self._set_status("")
            self._sig = None
            self._apply_state(payload)

        self._in_thread(work, done)

    def _forget(self, device):
        self._set_busy(True)
        self._set_status("Oubli de %s…" % device["name"], busy=True)
        mac = device["mac"]

        def work():
            subprocess.run(["bluetoothctl", "remove", mac],
                           stdout=DEVNULL, stderr=DEVNULL, timeout=BTCTL_TIMEOUT)
            return collect_state()

        def done(res):
            self._set_busy(False)
            self._set_status("")
            if not isinstance(res, Exception):
                self._sig = None
                self._apply_state(res)

        self._in_thread(work, done)

    # ---- Scan ----

    def _on_scan(self, _btn):
        """Scan borné dans le temps, par `bluetoothctl --timeout`.

        La découverte ne dure que tant que vit le client qui l'a demandée, et
        `bluetoothctl scan on` est un client comme un autre : il lit son stdin
        en attendant. Lancé avec stdin sur /dev/null, il voit un EOF immédiat
        et rend la main — vérifié, code de sortie 1 en moins d'une seconde et
        « Discovering: no ». Le bouton affichait donc un compte à rebours de
        douze secondes pendant lesquelles rien n'était cherché.

        `--timeout` est fait pour ce cas : bluetoothctl garde la découverte
        ouverte le nombre de secondes demandé, puis s'arrête proprement de
        lui-même (code 0). On le tue quand même à la fermeture du popup, sans
        quoi le scan survivrait à la fenêtre et viderait la batterie des
        appareils à portée.
        """
        if self._scanning:
            return
        self._scanning = True
        self.scan_row.set_sensitive(False)
        self._set_status("Recherche d'appareils…", busy=True)
        try:
            self._scan_proc = subprocess.Popen(
                ["bluetoothctl", "--timeout", str(SCAN_SECONDS),
                 "scan", "on"],
                stdout=DEVNULL, stderr=subprocess.PIPE, stdin=DEVNULL,
                text=True)
        except OSError as exc:
            self._scanning = False
            self.scan_row.set_sensitive(True)
            self._set_status("Erreur : %s" % exc, error=True)
            return
        # Une seconde de marge : bluetoothctl doit avoir rendu la main de
        # lui-même quand on relit la liste, sinon on la lit pendant qu'il
        # écrit encore.
        GLib.timeout_add_seconds(SCAN_SECONDS + 1, self._end_scan)
        # Rafraîchissements intermédiaires : les appareils apparaissent au fil
        # du scan, les montrer à la fin seulement donne une fenêtre inerte
        # pendant douze secondes.
        GLib.timeout_add_seconds(4, self._scan_progress)

    def _scan_progress(self):
        """Montre au fil de l'eau ce que le scan trouve."""
        if self._closed or not self._scanning:
            return False
        self._reload(quiet=True)
        return True            # tant que le scan dure

    def _end_scan(self):
        proc = self._scan_proc
        # `--timeout` fait sortir bluetoothctl de lui-même : un code non nul
        # dit que la découverte n'a pas pu démarrer (adaptateur éteint,
        # org.bluez.Error.NotReady). Le taire laisserait croire à un scan
        # infructueux là où c'est le scan lui-même qui a échoué.
        err = ""
        if proc is not None and proc.poll() not in (None, 0):
            try:
                err = (proc.stderr.read() or "").strip().splitlines()[-1]
            except (OSError, ValueError, IndexError):
                err = "la recherche n'a pas pu démarrer"
        self._stop_scan()
        if self._closed:
            return False
        self._scanning = False
        self.scan_row.set_sensitive(not self._busy)
        if err:
            self._set_status("Échec : %s" % err, error=True)
        else:
            self._set_status("")
        self._sig = None
        self._reload(quiet=True)
        return False

    def _stop_scan(self):
        if self._scan_proc is not None and self._scan_proc.poll() is None:
            self._scan_proc.terminate()
        self._scan_proc = None

    def _open_blueman(self, _btn):
        subprocess.Popen(["blueman-manager"], stdout=DEVNULL, stderr=DEVNULL)
        self.close()


def main():
    run_popup(BluetoothPopup, "waybar-bluetooth-menu")


if __name__ == "__main__":
    main()
