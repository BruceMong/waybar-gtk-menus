#!/usr/bin/env python3
"""Ouvre une URL dans un profil Chrome précis, sur un bureau Hyprland donné.

    chrome-open.py <dossier-de-profil> <url> [bureau]
    chrome-open.py --record <dossier-de-profil> <adresse>

Un lien Google Agenda ne s'ouvre que depuis le profil du compte qui possède
l'agenda : partagé vers le compte personnel, l'événement s'affiche bien dans
le popup, mais cliquer dessus depuis le mauvais profil affiche « vous n'avez
pas accès à cet événement ». Le premier argument est ce profil, que l'appelant
déduit de l'identifiant de l'agenda (`calendar_agenda.chrome_profile`).

Deux obstacles, dont le second découle du premier.

**`--profile-directory` ne sert qu'au démarrage.** Quand une instance Chrome
tourne déjà, l'option est ignorée pour une simple ouverture d'URL : le lien
part dans la fenêtre qui a le focus système, quel que soit son profil. Seul
`--new-window` force Chrome à honorer le profil demandé. On s'appuie donc sur
ce que Chrome fait vraiment — suivre le focus — plutôt que sur ce que la
ligne de commande prétend : la fenêtre du profil est mise au premier plan,
puis l'URL est lancée nue, et elle y atterrit.

**Aucune fenêtre ne dit à quel profil elle appartient.** Toutes portent la
classe `google-chrome` (seules les PWA en ont une propre), et Hyprland n'en
sait pas plus. Impossible, donc, ni d'écrire une `windowrule` par profil, ni
de retrouver après coup la fenêtre à réutiliser. On tient la table nous-même,
dans $XDG_RUNTIME_DIR : chaque fenêtre créée ici y est notée sous son profil,
et `--record` permet à startup-layout.sh d'y déclarer les deux fenêtres qu'il
ouvre au démarrage — sans quoi le premier clic les dédoublerait.

Le placement sur le bureau, enfin, reprend le mécanisme de startup-layout.sh :
rendre actif le groupe du bureau visé et le déverrouiller le temps que la
fenêtre s'y range d'elle-même, `group:auto_group` faisant le reste.
"""
import json
import os
import subprocess
import sys
import time

CHROME = "google-chrome-stable"
# `uwsm app -t service` et non un lancement nu. Deux liens rattachent sinon
# Chrome à la barre, et il faut couper les deux :
#   - le cgroup, hérité de l'appelant (waybar.service). L'unité étant en
#     KillMode=control-group, un segfault de la barre emportait le navigateur.
#   - le groupe de processus, qu'un `scope` laisse lui aussi intact : waybar
#     tue ses enfants à chaque rechargement, et Chrome mourait au reload sans
#     que la barre ait crashé.
# `-t service` fait forker le processus par systemd --user : nouveau cgroup,
# nouveau groupe, nouvelle session. `setsid -f` ne coupait que le troisième.
LAUNCH = ["uwsm", "app", "-t", "service", "-S", "both", "--", CHROME]
CLASS = "google-chrome"          # fenêtres ordinaires ; les PWA ont la leur
DEVNULL = subprocess.DEVNULL

# Table profil → adresse de fenêtre. Une adresse ne survit pas à la session :
# $XDG_RUNTIME_DIR, vidé à la déconnexion, est exactement sa durée de vie.
STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                     "waybar-chrome-windows.json")

# Chrome met de l'ordre de la seconde à ouvrir une fenêtre sur un profil
# jamais lancé, beaucoup moins pour un onglet. Au-delà on renonce à placer
# quoi que ce soit plutôt que de laisser un groupe déverrouillé derrière soi.
TIMEOUT = 8.0
POLL = 0.15


def _lua_dispatch(args):
    """Traduit un dispatcher hyprlang en expression Lua.

    Depuis la bascule de la config sur hyprland.lua, `hyprctl dispatch` evalue
    son argument comme du code Lua et l'enveloppe dans hl.dispatch(...). La
    forme historique `dispatch focuswindow address:0x..` produisait
    « ')' expected near 'address' » et ne faisait rien du tout.
    """
    cmd = args[0]
    arg = args[1] if len(args) > 1 else ""
    if cmd == "focuswindow":
        return 'hl.dsp.focus({ window = "%s" })' % arg
    if cmd == "workspace":
        return 'hl.dsp.focus({ workspace = "%s" })' % arg
    if cmd == "lockactivegroup":
        return 'hl.dsp.group.lock("%s")' % arg
    raise ValueError("dispatcher non traduit: %r" % (args,))


def dispatch(*args):
    subprocess.run(["hyprctl", "dispatch", _lua_dispatch(args)],
                   check=False, stdout=DEVNULL, stderr=DEVNULL)


def windows():
    """Adresse → (titre, bureau) des fenêtres Chrome ordinaires."""
    try:
        out = subprocess.run(["hyprctl", "clients", "-j"], check=True,
                             capture_output=True, text=True).stdout
        clients = json.loads(out)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return {}
    return {c["address"]: (c.get("title", ""),
                           c.get("workspace", {}).get("id"))
            for c in clients if c.get("class") == CLASS}


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(table):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(table, f)
    except OSError:
        pass


def record(profile, address):
    table = read_state()
    table[profile] = address
    write_state(table)


def anchor_on(workspace, snapshot):
    """Une fenêtre Chrome déjà posée sur ce bureau, s'il y en a une."""
    for address, (_title, ws) in snapshot.items():
        if ws == workspace:
            return address
    return None


def wait_new(before):
    """Attend la fenêtre que Chrome vient d'ouvrir."""
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL)
        new = [a for a in windows() if a not in before]
        if new:
            return new[0]
    return None


def wait_title(address, before_title):
    """Attend que l'onglet soit arrivé dans la fenêtre — son titre change.

    Rien n'en dépend fonctionnellement : c'est le focus final qui compte, et
    il est déjà bon. Mais rendre la main avant que Chrome ait bougé ferait
    disparaître le popup sur une fenêtre encore vide, et l'utilisateur
    reclique.
    """
    # Une fenetre lancee sous --window-name porte un titre fige, que la page
    # affichee ne change plus (c'est ainsi que les onglets du groupe du bureau
    # 2 sont etiquetes par compte). L'attendre changer, c'est attendre le
    # timeout entier a chaque clic : on se contente d'une pause courte. Le
    # suffixe est le seul marqueur disponible, une fenetre Chrome ordinaire
    # finissant toujours par le nom du navigateur.
    if not before_title.endswith("Google Chrome"):
        time.sleep(1.0)
        return

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL)
        now = windows().get(address)
        if now is None or now[0] != before_title:
            return


def reuse(address, url):
    """Envoie l'URL dans une fenêtre connue : Chrome suit le focus système."""
    before_title = windows().get(address, ("", None))[0]
    dispatch("focuswindow", "address:%s" % address)
    time.sleep(0.3)
    subprocess.Popen(LAUNCH + [url], start_new_session=True,
                     stdout=DEVNULL, stderr=DEVNULL)
    wait_title(address, before_title)
    dispatch("focuswindow", "address:%s" % address)


def create(profile, url, workspace, before):
    """Ouvre une fenêtre pour ce profil et la range dans le groupe du bureau."""
    anchor = anchor_on(workspace, before) if workspace else None
    if workspace:
        if anchor:
            # Rendre le groupe actif ET le déverrouiller : sans le premier la
            # fenêtre s'ouvrirait sur le bureau courant, sans le second elle
            # se poserait à côté du groupe au lieu d'y entrer.
            dispatch("focuswindow", "address:%s" % anchor)
            dispatch("lockactivegroup", "unlock")
        else:
            dispatch("workspace", str(workspace))
        time.sleep(0.25)

    subprocess.Popen(LAUNCH + ["--profile-directory=%s" % profile,
                               "--new-window", url],
                     start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)
    target = wait_new(before)

    if anchor:
        # Toujours refermer le groupe, y compris quand rien n'a été détecté :
        # un groupe laissé ouvert avalerait la prochaine fenêtre venue, et
        # l'oubli ne se verrait qu'au moment où il gêne.
        if target is None:
            dispatch("focuswindow", "address:%s" % anchor)
        dispatch("lockactivegroup", "lock")

    if target:
        record(profile, target)
        dispatch("focuswindow", "address:%s" % target)


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--record"]:
        if len(argv) != 3:
            return 2
        record(argv[1], argv[2])
        return 0
    if len(argv) < 2:
        print("usage: chrome-open.py <profil> <url> [bureau]", file=sys.stderr)
        return 2

    profile, url = argv[0], argv[1]
    try:
        workspace = int(argv[2]) if len(argv) > 2 else 0
    except ValueError:
        workspace = 0

    before = windows()
    known = read_state().get(profile)
    if known in before:
        # La fenêtre du profil est réutilisée là où elle se trouve : elle a
        # été posée sur le bon bureau à sa création, et l'avoir déplacée
        # depuis est une décision de l'utilisateur, pas un accident.
        reuse(known, url)
    else:
        create(profile, url, workspace, before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
