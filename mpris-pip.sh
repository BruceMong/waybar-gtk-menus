#!/usr/bin/env bash
# Bascule une vidéo Chrome en Picture-in-Picture.
#
#   mpris-pip.sh              « sors ce que je regarde »  (clic droit du module)
#   mpris-pip.sh --playing    « sors ce qui joue »        (media-menu.py)
#
# Prérequis (une seule fois, côté Chrome) :
#   1. chrome://extensions → mode développeur → « Charger l'extension non
#      empaquetée » → ~/.config/waybar/pip-extension
#   2. chrome://extensions/shortcuts → lui assigner Alt+Shift+P, portée
#      « Dans Chrome ». Alt+P reste libre pour l'extension de Google, qui peut
#      donc rester installée : les deux se complètent au clavier.
#
# Pourquoi pas SUPER+P : Chrome n'accepte que Ctrl, Alt et Shift dans les
# raccourcis d'extension — pas Meta — et refuse Ctrl+Alt (AltGr). SUPER est de
# toute façon le mainMod d'Hyprland, qui intercepte avant Chrome.
#
# Le clic Waybar n'étant pas un geste utilisateur valide pour la page, on donne
# le focus à Chrome puis on envoie le raccourci de l'extension.
#
# ── Pourquoi une extension maison ────────────────────────────────────────────
# Le raccourci arrive dans la fenêtre focalisée ; rien, depuis l'extérieur, ne
# permet de désigner un ONGLET. L'extension de Google s'en tenait donc à
# l'onglet actif : vidéo passée en arrière-plan, le clic droit ne faisait rien,
# et rien ne le disait. Celle du dépôt (pip-extension/) cherche elle-même
# l'onglet qui porte une vidéo, dans toutes les fenêtres, et ne bascule dessus
# que si la page refuse le PiP depuis l'arrière-plan.
#
# Ce script garde malgré tout la charge de choisir la FENÊTRE : c'est elle qui
# reçoit Alt+P, et une page d'une fenêtre masquée est « hidden » pour Chrome,
# donc fondée à refuser le PiP. Viser juste évite ce détour.
#
# ── Ce que les deux modes veulent dire ───────────────────────────────────────
#   --playing  « sors ce qui joue » : la fenêtre dont le titre porte celui que
#              Chrome publie sur MPRIS. Faute de la trouver — l'onglet qui joue
#              n'est pas au premier plan de sa fenêtre — on retombe sur la
#              fenêtre la plus récente et on laisse l'extension viser l'onglet.
#              Chrome n'implémente pas Raise() (CanRaise = false) et n'expose
#              pas l'URL du morceau : il n'y a pas mieux de ce côté-ci.
#
#   (défaut)   « sors celle que j'ai sous les yeux » : la fenêtre la plus
#              récemment focalisée, sans autre condition. C'est ici que se
#              trouvait le bug d'origine : `focuswindow class:^(google-chrome)$`
#              prenait une fenêtre au hasard parmi les deux ouvertes.

CHROME_CLASS='google-chrome'

# Marques de direction que YouTube enrobe autour des noms de chaîne : elles
# sont dans le titre de la fenêtre mais pas dans celui de MPRIS, et feraient
# échouer la comparaison.
BIDI='[‎‏‪-‮⁦-⁩]'

# Laisse au focus le temps de s'établir avant d'envoyer la touche : sous ce
# seuil, Chrome reçoit le raccourci alors qu'il n'a pas encore la main.
FOCUS_DELAY=0.35

mode="watching"
[ "$1" = "--playing" ] && mode="playing"

clients=$(hyprctl clients -j 2>/dev/null) || exit 0

# Une fenêtre PiP est déjà là : on s'arrête. Le raccourci de l'extension est
# une bascule, donc l'envoyer maintenant refermerait le PiP — un clic droit
# « mets en PiP » qui retire le PiP est un piège, d'autant que la fenêtre porte
# déjà sa croix pour ça. Même motif que la règle `pip-float` de hyprland.lua :
# les deux désignent la même fenêtre, elles doivent le dire pareil.
if jq -e 'any(.[]; .title | test("picture.in.picture"; "i"))' >/dev/null <<<"$clients"; then
    exit 0
fi

# `min_by(.focusHistoryID)` = la fenêtre la plus récemment focalisée.
pick_focused() {
    jq -r --arg cls "$CHROME_CLASS" '
        [ .[] | select(.class == $cls) ]
        | if length == 0 then empty else (min_by(.focusHistoryID) | .address) end
    ' <<<"$clients"
}

pick_by_title() {
    jq -r --arg cls "$CHROME_CLASS" --arg want "$1" --arg bidi "$BIDI" '
        def clean: gsub($bidi; "");
        [ .[] | select(.class == $cls)
              | select((.title | clean) | contains($want | clean)) ]
        | if length == 0 then empty else (min_by(.focusHistoryID) | .address) end
    ' <<<"$clients"
}

if [ "$mode" = "playing" ]; then
    player=$(playerctl -l 2>/dev/null | grep -m1 -E '^(chromium|chrome)')
    title=""
    [ -n "$player" ] && title=$(playerctl -p "$player" metadata --format '{{title}}' 2>/dev/null)

    if [ -z "$title" ]; then
        target=$(pick_focused)          # rien sur MPRIS : au moins viser l'écran
    else
        # Aucune fenêtre ne porte ce titre : l'onglet qui joue est en
        # arrière-plan. On vise la fenêtre la plus récente et l'extension ira
        # chercher l'onglet — c'est exactement ce pour quoi elle existe.
        target=$(pick_by_title "$title")
        [ -n "$target" ] || target=$(pick_focused)
    fi
else
    target=$(pick_focused)
fi

# Chrome n'est pas ouvert, ou aucune de ses fenêtres n'est joignable.
[ -n "$target" ] || exit 0

# Mémorise la fenêtre actuellement au premier plan pour y revenir ensuite.
prev=$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')

hyprctl dispatch "hl.dsp.focus({ window = \"address:$target\" })" >/dev/null 2>&1 || exit 0
sleep "$FOCUS_DELAY"

# Raccourci de l'extension du dépôt (Alt+Shift+P). Au-delà, c'est elle qui
# choisit l'onglet, et qui notifie si aucun n'a de vidéo.
wtype -M alt -M shift -k p -m shift -m alt

# Revient à la fenêtre d'origine si ce n'était pas celle-là (PiP reste flottant).
if [ -n "$prev" ] && [ "$prev" != "$target" ]; then
    sleep 0.05
    hyprctl dispatch "hl.dsp.focus({ window = \"address:$prev\" })" >/dev/null 2>&1
fi
