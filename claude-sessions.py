#!/usr/bin/env python3
"""Module Waybar « custom/claude » : suivi des sessions Claude Code.

Résume en un coup d'œil qui travaille, qui attend une action, qui a terminé :

    text    : 󰚩 2/8   -> 2 sessions réclament une action sur 8 ouvertes
              󰚩 8     -> les 8 tournent, rien à faire
              (vide)  -> aucune session, Waybar masque le module

Le tooltip nomme chaque session par son titre. Les titres viennent des
transcripts, mis en cache une minute : le module tourne toutes les 10 s et
relire huit fichiers à chaque tick n'aurait aucun intérêt.

La lecture et l'entretien des états vivent dans claude_sessions_data.py,
partagés avec le popup claude-menu.py.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions_data import (  # noqa: E402
    NEEDS_ATTENTION, cached_titles, humanize, load_sessions,
)

ICONS = {"waiting": "󰥔", "done": "󰄬", "running": "󰑮", "idle": "󰒲"}
LABELS = {
    "waiting": "attend une action",
    "done": "terminé",
    "running": "travaille",
    "idle": "au repos",
}


def build_tooltip(sessions):
    now = time.time()
    lines = []
    for s in sessions:
        status = s.get("status", "idle")
        age = humanize(now - s.get("ts", now))
        # Le titre identifie la session ; le projet seul ne suffit pas quand
        # plusieurs sessions tournent sur le même dépôt.
        name = s.get("title") or s.get("dir", "?")
        project = s.get("dir", "")
        suffix = f"  ({project})" if s.get("title") and project else ""
        lines.append(
            f"{ICONS.get(status, '󰄰')}  {name}{suffix} — "
            f"{LABELS.get(status, status)} ({age})"
        )
    lines.append("")
    lines.append("clic : détail et actions")
    return "\n".join(lines)


def main():
    sessions = load_sessions()
    if not sessions:
        print(json.dumps({"text": "", "tooltip": "Aucune session Claude Code"}))
        return

    cached_titles(sessions)

    total = len(sessions)
    attention = sum(1 for s in sessions
                    if s.get("status") in NEEDS_ATTENTION)
    waiting = sum(1 for s in sessions if s.get("status") == "waiting")

    text = f"󰚩 {attention}/{total}" if attention else f"󰚩 {total}"
    # La classe la plus urgente l'emporte : attente > terminé > en cours.
    if waiting:
        cls = "waiting"
    elif attention:
        cls = "done"
    else:
        cls = "running"

    print(json.dumps({
        "text": text,
        "tooltip": build_tooltip(sessions),
        "class": cls,
        "alt": cls,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # le module ne doit jamais casser la barre
        print(json.dumps({"text": "", "tooltip": f"claude-sessions: {exc}"}))
        sys.exit(0)
