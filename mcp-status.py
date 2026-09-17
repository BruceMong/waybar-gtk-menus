#!/usr/bin/env python3
"""Module Waybar « custom/mcp » : état des serveurs MCP partagés.

Les serveurs MCP que toutes les sessions Claude Code se partagent tournent
dans des unités systemd utilisateur (mcp-shared@<nom>), hébergés par
mcp-proxy — cf. ~/.local/bin/mcp-shared. Le module résume leur état :

    text    :  󰒍        -> tous répondent
               󰒍 3/4    -> 3 serveurs répondent sur 4 (classe « degraded »)
               󰒍 0/4    -> plus rien ne répond (classe « down »)
              (vide)    -> pas de serveur partagé configuré, Waybar masque
                           le module (dépôt public : le montage est optionnel)

Le tooltip détaille chaque serveur. L'état vient de `mcp-shared status
--json`, qui sonde chaque serveur actif par un `initialize` MCP : un proxy
vivant dont le serveur est mort compte comme en panne. Rafraîchi toutes les
30 s et sur SIGRTMIN+16 (envoyé par le popup après une action).
"""
import json
import os
import shutil
import subprocess
import sys

MCP_SHARED = os.path.expanduser("~/.local/bin/mcp-shared")
ICON = "\U000f048d"   # serveur en réseau

STATE_LABELS = {
    "ok": "répond",
    "starting": "démarre",
    "failed": "proxy vivant, serveur muet",
    "down": "arrêté",
}
STATE_ICONS = {"ok": "", "starting": "", "failed": "", "down": ""}


def load_status():
    if not os.access(MCP_SHARED, os.X_OK) or shutil.which("systemctl") is None:
        return None
    try:
        out = subprocess.run([MCP_SHARED, "status", "--json"], text=True,
                             capture_output=True, timeout=25).stdout
        return json.loads(out or "[]")
    except Exception:
        return None


def build_tooltip(servers):
    lines = []
    for s in servers:
        state = s.get("state", "down")
        mem = s.get("memory", 0) // 1048576
        extra = f" — {mem} Mo" if state == "ok" and mem else ""
        restarts = s.get("restarts", 0)
        if restarts:
            extra += f", {restarts} redémarrage{'s' if restarts > 1 else ''}"
        lines.append(f"{STATE_ICONS.get(state, '')}  {s['name']} :{s['port']} — "
                     f"{STATE_LABELS.get(state, state)}{extra}")
    lines.append("")
    lines.append("clic : détail, journal, redémarrage")
    return "\n".join(lines)


def main():
    servers = load_status()
    if not servers:
        print(json.dumps({"text": "", "tooltip": "Aucun serveur MCP partagé"}))
        return

    total = len(servers)
    ok = sum(1 for s in servers if s.get("state") == "ok")
    if ok == total:
        text, cls = ICON, "ok"
    elif ok == 0:
        text, cls = f"{ICON} 0/{total}", "down"
    else:
        text, cls = f"{ICON} {ok}/{total}", "degraded"

    print(json.dumps({
        "text": text,
        "tooltip": build_tooltip(servers),
        "class": cls,
        "alt": cls,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # le module ne doit jamais casser la barre
        print(json.dumps({"text": "", "tooltip": f"mcp-status: {exc}"}))
        sys.exit(0)
