#!/usr/bin/env bash
# Hook UserPromptSubmit de Claude Code : un prompt vient d'être envoyé.
#
# Bascule la session en « travaille » pour le module Waybar custom/claude.
# Aucune notification — c'est purement du suivi d'état, c'est ce qui permet
# de distinguer dans la barre les sessions qui bossent de celles qui ont fini.

source "$HOME/.claude/hooks/claude-session-lib.sh"

payload="$(cat)"
cs_resolve
cs_write running "$payload" "Traitement en cours"

exit 0
