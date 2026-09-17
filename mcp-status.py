#!/usr/bin/env python3
"""Module Waybar « custom/mcp » : état des serveurs MCP.

Deux choses à dire, et la barre ne dit que ce qui cloche :

    󰒍            tout va bien
    󰒍 3/4        un serveur partagé (mcp-shared@<nom>) ne répond pas
    󰒍 ·24        fan-out : 24 processus MCP stdio, un par session et par
                 serveur — c'est ce qui a rempli la RAM le 2026-09-16
                 (classe « fanout », seuil FANOUT_WARN)
    󰒍 !          un serveur réclame une authentification (/mcp)
    (vide)       ni serveur partagé, ni session Claude : module masqué

Le tooltip détaille : chaque partagé (état, port, mémoire), le fan-out par
serveur, les projets ouverts. Rafraîchi toutes les 30 s et sur SIGRTMIN+16
(envoyé par le popup après une action). Les données viennent de mcp_data.py,
le même module que le popup.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_data as md  # noqa: E402
from claude_sessions_data import load_sessions  # noqa: E402

ICON = "\U000f048d"   # serveur en réseau
FANOUT_WARN = 20

STATE_LABELS = {
    "ok": "répond",
    "starting": "démarre",
    "failed": "proxy vivant, serveur muet",
    "down": "arrêté",
}
STATE_ICONS = {"ok": "", "starting": "", "failed": "", "down": ""}


def build_tooltip(shared, ov, sessions):
    lines = []
    if shared:
        lines.append("Partagés (mcp-shared)")
        for s in shared:
            state = s.get("state", "down")
            mem = s.get("memory", 0) // 1048576
            extra = f" — {mem} Mo" if state == "ok" and mem else ""
            if s.get("restarts"):
                extra += f", {s['restarts']} redémarrage{'s' if s['restarts'] > 1 else ''}"
            lines.append(f"  {STATE_ICONS.get(state, '')}  {s['name']} :{s['port']} — "
                         f"{STATE_LABELS.get(state, state)}{extra}")
    if ov["stdio"]:
        lines.append("")
        lines.append(f"Stdio : {ov['stdio']} processus, {ov['stdio_rss_ko'] // 1024} Mo, "
                     f"dans {ov['sessions']} sessions")
        for name, n in sorted(ov["stdio_by_name"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name} ×{n}")
    if ov["auth"]:
        lines.append("")
        lines.append("Auth requise : " + ", ".join(ov["auth"]))
    cwds = sorted({os.path.basename(s.get("cwd") or "") for s in sessions} - {""})
    if cwds:
        lines.append("")
        lines.append("Projets ouverts : " + ", ".join(cwds))
    lines.append("")
    lines.append("clic : serveurs par projet, journal, redémarrage")
    return "\n".join(lines)


def main():
    sessions = load_sessions()
    shared = md.shared_status()
    if not shared and not sessions:
        print(json.dumps({"text": "", "tooltip": "Aucun serveur MCP"}))
        return
    ov = md.overview(sessions)

    classes = []
    text = ICON
    if shared:
        ok = sum(1 for s in shared if s.get("state") == "ok")
        if ok < len(shared):
            text += f" {ok}/{len(shared)}"
            classes.append("down" if ok == 0 else "degraded")
    if ov["stdio"] >= FANOUT_WARN:
        text += f" ·{ov['stdio']}"
        classes.append("fanout")
    # Un serveur de plugin qui réclame une auth ne mérite pas un point
    # d'exclamation permanent dans la barre : on ne l'a pas forcément voulu.
    # Le popup, lui, le montre.
    if any(not n.startswith("plugin:") for n in ov["auth"]):
        text += " !"
        classes.append("auth")
    if not classes:
        classes.append("ok")

    print(json.dumps({
        "text": text,
        "tooltip": build_tooltip(shared, ov, sessions),
        "class": classes,
        "alt": classes[0],
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # le module ne doit jamais casser la barre
        print(json.dumps({"text": "", "tooltip": f"mcp-status: {exc}"}))
        sys.exit(0)
