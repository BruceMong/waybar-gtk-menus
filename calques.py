#!/usr/bin/env python3
"""Module custom/calques : les bureaux superposés de SUPER+S et SUPER+A dans la barre.

Les calques sont les special workspaces `magic` (calque 1), `magic2`…`magic10`
pour SUPER+S, `perso`…`perso10` pour SUPER+A — cf. hypr/scripts/ws-key.sh.
Seule la famille affichée est montrée. `ext/workspaces` ne les montre pas : le
protocole ext-workspace ne connaît que les bureaux ordinaires.

Module vide (donc masqué) tant qu'aucun calque n'est affiché. Calque ouvert :
les calques occupés plus le courant, le courant à l'accent. Une ligne JSON
par changement, sur les événements du socket2 d'Hyprland : pas de sondage.
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.dont_write_bytecode = True   # cf. generate-tokens.py : un .pyc périmé relit l'ancienne palette
import tokens  # noqa: E402

EVENTS = (b"activespecial", b"openwindow", b"closewindow", b"movewindow",
          b"focusedmon", b"destroyworkspace", b"createworkspace")


def hypr(*args):
    try:
        return json.loads(subprocess.run(["hyprctl", *args, "-j"], capture_output=True,
                                         text=True, timeout=2).stdout)
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError):
        return []


FAMILLES = {"magic": ("\U000F0328", "SUPER+S"), "perso": ("\U000F09FE", "SUPER+A")}


def numero(nom, famille):
    """special:magic → 1, special:magic7 → 7, autre famille ou autre chose → None."""
    if not nom.startswith("special:" + famille):
        return None
    reste = nom[len("special:" + famille):]
    if reste == "":
        return 1
    return int(reste) if reste.isdigit() and 2 <= int(reste) <= 10 else None


def etat():
    shown = next((m["specialWorkspace"]["name"] for m in hypr("monitors") if m.get("focused")), "")
    famille = next((f for f in FAMILLES if numero(shown, f) is not None), None)
    if famille is None:
        return {"text": "", "class": "ferme"}
    cur = numero(shown, famille)
    icone, touche = FAMILLES[famille]
    occupes = {numero(w["name"], famille) for w in hypr("workspaces") if w.get("windows")}
    occupes.discard(None)
    parts = []
    for n in sorted(occupes | {cur}):
        if n == cur:
            parts.append(f"<span color='{tokens.BLUE}'><b>{n}</b></span>")
        else:
            parts.append(f"<span color='{tokens.INK2 if n in occupes else tokens.INK3}'>{n}</span>")
    return {"text": icone + "  " + "  ".join(parts), "class": "ouvert",
            "tooltip": f"Calque {cur} de {touche} — SUPER+1…0 pour changer, clic pour fermer"}


def emettre(dernier):
    e = json.dumps(etat(), ensure_ascii=False)
    if e != dernier:
        print(e, flush=True)
    return e


def main():
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket2.sock")
    dernier = emettre(None)
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(sock)
                tampon = b""
                while True:
                    bloc = s.recv(4096)
                    if not bloc:
                        break
                    tampon += bloc
                    *lignes, tampon = tampon.split(b"\n")
                    if any(l.split(b">>", 1)[0] in EVENTS for l in lignes):
                        dernier = emettre(dernier)
        except OSError:
            pass
        time.sleep(2)    # Hyprland relancé : se rebrancher
        dernier = emettre(dernier)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(0)
