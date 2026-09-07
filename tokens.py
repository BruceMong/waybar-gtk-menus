#!/usr/bin/env python3
"""Palette unique des surfaces Waybar — source de vérité des couleurs.

Pourquoi ce fichier existe
──────────────────────────
La palette est cohérente — audit fait : le bleu système est `#0a84ff` partout,
et les valeurs voisines qu'on croise ont chacune leur raison (les gris de
claude-menu.py sont les équivalents OPAQUES d'un blanc translucide, parce que
le markup Pango ne connaît pas rgba() ; `#f5f5f7` contre `#ebebf0` est la
hiérarchie titre / corps). Rien à réparer, donc.

Ce qui manquait était le NOM. Quatorze scripts recopiaient les mêmes hex sans
qu'aucun ne puisse dire lequel est « l'encre primaire » : la palette tenait
par la discipline de celui qui écrivait, pas par la structure. Ce module la
nomme une fois, pour que le prochain menu l'importe au lieu de la deviner.

Les valeurs ci-dessous sont celles déjà en place, à l'identique — ce fichier
n'a jamais eu vocation à changer un pixel.

Règles d'usage (celles de style-normal.css, reprises telles quelles)
───────────────────────────────────────────────────────────────────
1. La hiérarchie se fait par l'OPACITÉ de l'encre (INK / INK2 / INK3),
   jamais par la teinte.
2. Les couleurs système ne signalent qu'un état ANORMAL : batterie critique,
   surchauffe, enregistrement, unité systemd en échec. Si tout est coloré,
   plus rien n'alerte.
3. Deux tons par accent : le ton plein sert de FOND (bouton, interrupteur),
   le ton éclairci sert de TEXTE. `#0a84ff` en texte sur fond sombre passe
   sous le seuil de contraste — c'est la raison d'être de `BLUE_TEXT`, et la
   même règle qu'appliquent gtk-3.0/gtk.css et gtk-4.0/gtk.css.

Ce module ne dépend de rien : il est importable depuis n'importe quel script
de menu, et `generate-tokens.py` s'en sert pour écrire la feuille CSS que
consomment la barre et les popups.
"""

# ── Encre : hiérarchie par opacité, jamais par teinte ──────────────────────
INK = "#ebebf0"   # primaire   — valeurs, icônes actives, libellés de ligne
INK2 = "#9a9aa2"  # secondaire — sous-titres, contexte, unités
INK3 = "#68686f"  # tertiaire  — inactif, désactivé, séparateurs de texte

# Blanc pur : réservé au texte posé SUR un aplat d'accent (bouton bleu, ligne
# sélectionnée). Ce n'est pas un quatrième niveau d'encre.
ON_ACCENT = "#ffffff"

# ── Accents système Apple (dark mode) — réservés aux anomalies ─────────────
BLUE = "#0a84ff"         # accent : fond de bouton, interrupteur actif
BLUE_TEXT = "#5e9cff"    # accent en TEXTE (cf. règle 3)
BLUE_HOVER = "#409cff"   # accent survolé — éclairci d'un cran

RED = "#ff453a"          # anomalie : échec, arrêt, enregistrement
RED_HOVER = "#ff6961"
GREEN = "#32d74b"        # état sain, tâche terminée
ORANGE = "#ff9f0a"       # état volontaire qui se paie (DND, mode dégradé)
YELLOW = "#ffd60a"
PURPLE = "#bf5af2"

# ── Matériaux ──────────────────────────────────────────────────────────────
# Le flou vient du compositeur (blocs `layerrule` de la config Hyprland), jamais du
# CSS : GTK3 n'a pas de backdrop-filter. Ces valeurs ne sont que la teinte
# posée par-dessus.
WINDOW_BG = "rgba(28, 28, 30, 0.74)"   # popups
BAR_BG = "rgba(30, 30, 32, 0.52)"      # la barre elle-même, plus légère
HAIRLINE = "rgba(255, 255, 255, 0.13)"  # bordure spéculaire d'un pixel
FILL = "rgba(255, 255, 255, 0.10)"      # survol
FILL_HARD = "rgba(255, 255, 255, 0.17)"  # appui / sélection


def css_vars():
    """Palette sous forme de déclarations `@define-color` GTK.

    GTK3 n'accepte pas les variables CSS (`var(--x)`) : `@define-color` est le
    seul mécanisme de nommage disponible, et il doit être déclaré avant tout
    usage dans la même feuille.
    """
    pairs = [
        ("ink", INK), ("ink2", INK2), ("ink3", INK3), ("onAccent", ON_ACCENT),
        ("sysBlue", BLUE), ("sysBlueText", BLUE_TEXT),
        ("sysBlueHover", BLUE_HOVER),
        ("sysRed", RED), ("sysRedHover", RED_HOVER),
        ("sysGreen", GREEN), ("sysOrange", ORANGE),
        ("sysYellow", YELLOW), ("sysPurple", PURPLE),
        ("windowBg", WINDOW_BG), ("barBg", BAR_BG),
        ("hairline", HAIRLINE), ("fill", FILL), ("fillHard", FILL_HARD),
    ]
    return "\n".join("@define-color %s %s;" % (n, v) for n, v in pairs)
