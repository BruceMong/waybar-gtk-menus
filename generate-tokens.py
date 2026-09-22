#!/usr/bin/env python3
"""Écrit `tokens.css` à côté de chaque feuille qui en dépend.

Pourquoi un fichier GÉNÉRÉ plutôt qu'un fichier partagé
───────────────────────────────────────────────────────
GTK résout `@import url("…")` relativement à la feuille qui importe, et un
chemin absolu est exclu : `waybar/` est publié dans un dépôt public dont le
garde-fou refuse tout `/home/<user>`. Chaque dossier reçoit donc sa copie —
mais une copie ÉCRITE PAR CE SCRIPT, jamais à la main. La source unique reste
`tokens.py`, à côté.

Ce que ça répare, concrètement : avant le 2026-09-22, cinq feuilles
redéclaraient chacune la palette sous des noms différents (`ink` / `text` /
`fg`, `sysBlue` / `blue` / `accent`). Les teintes avaient tenu — la discipline
de celui qui écrivait —, mais les OPACITÉS de surface avaient dérivé : 0.72,
0.74 et 0.76 pour la même intention. Personne ne pouvait le voir, puisque rien
ne les mettait côte à côte.

Chaque feuille garde son vocabulaire : elle importe `tokens.css`, puis pose
ses alias (`@define-color text @ink;`). Il n'y avait donc aucune règle à
réécrire — seulement des déclarations à remplacer.

Usage :
    generate-tokens.py            écrit les fichiers
    generate-tokens.py --check    vérifie qu'ils sont à jour (sortie 1 sinon)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tokens  # noqa: E402

CONFIG = os.path.expanduser("~/.config")

# Les surfaces de la config : barre, notifications, HUD, lanceur, palette.
#
# gtk-3.0/ et gtk-4.0/ sont volontairement absents. Leurs `@define-color` ne
# sont pas notre vocabulaire mais celui de libadwaita (`accent_bg_color`,
# `theme_selected_bg_color`) : ce sont des surcharges de thème, lues par toutes
# les applications GTK de la machine. Un import cassé y casserait l'apparence
# de tout le système, pour trois couleurs. Le rapport ne vaut pas le risque.
# Chaque cible est désignée par une feuille EXISTANTE de son dossier. Le
# `tokens.css` est écrit à côté du fichier RÉEL vers lequel elle pointe, et non
# à côté du lien : sur cette machine les dossiers de `~/.config` sont de vrais
# dossiers remplis de liens par fichier, donc un fichier neuf écrit dans `~` y
# resterait — hors dépôt, invisible à `git status`, perdu à la restauration.
# C'est le même détour que `remote-mode.sh` fait pour `style.css`, et pour la
# même raison. Sur une machine sans Stow, `realpath` rend le fichier lui-même
# et tout se passe dans `~/.config` : le script n'a pas à savoir lequel des
# deux mondes il habite.
CIBLES = [
    "waybar/style-normal.css",
    "swaync/style.css",
    "swayosd/style.css",
    "walker/themes/macos/style.css",
    "walker/themes/menu/style.css",
]

EN_TETE = """/* ══ FICHIER GÉNÉRÉ — ne pas éditer ══════════════════════════════════════
   Source : ~/.config/waybar/tokens.py
   Régénérer : ~/.config/waybar/generate-tokens.py
   Toute modification faite ici est perdue à la prochaine génération.
   ═══════════════════════════════════════════════════════════════════════ */

"""


def contenu():
    return EN_TETE + tokens.css_vars() + "\n"


def main():
    verifier = "--check" in sys.argv
    attendu = contenu()
    perimes = []

    for cible in CIBLES:
        voisin = os.path.join(CONFIG, cible)
        if not os.path.exists(voisin):
            print("absent, ignoré : %s" % voisin, file=sys.stderr)
            continue
        chemin = os.path.join(os.path.dirname(os.path.realpath(voisin)),
                              "tokens.css")
        actuel = None
        if os.path.exists(chemin):
            with open(chemin, encoding="utf-8") as f:
                actuel = f.read()
        if actuel == attendu:
            continue
        if verifier:
            perimes.append(chemin)
            continue
        # Le fichier généré est VERSIONNÉ : sans lui, une restauration
        # partirait avec des feuilles important un fichier absent, donc sans
        # aucune couleur. Après un ajout, `stow` pose le lien dans `~`.
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(attendu)
        print("écrit : %s" % chemin)

    if verifier and perimes:
        print("PÉRIMÉ (relancer generate-tokens.py) :", file=sys.stderr)
        for p in perimes:
            print("  " + p, file=sys.stderr)
        return 1
    if verifier:
        print("tokens.css à jour partout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
