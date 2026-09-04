#!/usr/bin/env bash
# Bascule une vidéo Chrome en Picture-in-Picture.
#
#   mpris-pip.sh              « sors ce que je regarde »  (clic droit du module)
#   mpris-pip.sh --playing    « sors ce qui joue »        (media-menu.py)
#
# Prérequis (une seule fois, côté Chrome) :
#   1. Installer l'extension « Picture-in-Picture Extension (by Google) »
#   2. chrome://extensions/shortcuts → lui assigner Alt+P, portée « Dans Chrome »
#
# Le clic Waybar n'étant pas un geste utilisateur valide pour la page, on donne
# le focus à Chrome puis on envoie le raccourci de l'extension.
#
# ── Pourquoi deux modes ──────────────────────────────────────────────────────
# Le raccourci agit sur l'onglet ACTIF de la fenêtre focalisée. C'est toute la
# contrainte : rien, depuis l'extérieur, ne permet de désigner un onglet.
#
#   --playing  la vidéo visée est celle que Chrome publie sur MPRIS. On ne peut
#              l'atteindre que si son onglet est au premier plan de sa fenêtre :
#              on cherche donc la fenêtre dont le titre porte le titre MPRIS, et
#              faute de la trouver on prévient au lieu de sortir une autre
#              vidéo. Chrome n'implémente pas Raise() (CanRaise = false) et
#              n'expose pas l'URL du morceau : il n'y a pas de moyen d'activer
#              l'onglet qui joue.
#
#   (défaut)   le geste veut dire « celle que j'ai sous les yeux ». La fenêtre
#              la plus récemment focalisée est la bonne réponse, sans autre
#              condition. C'est ici que se trouvait le bug d'origine :
#              `focuswindow class:^(google-chrome)$` prenait une fenêtre au
#              hasard parmi les deux ouvertes, et le raccourci partait dans
#              celle qui n'affichait aucune vidéo.

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
        target=$(pick_by_title "$title")
        if [ -z "$target" ]; then
            # $'…' pour un vrai saut de ligne : notify-send n'interprète pas \n.
            notify-send -a "Waybar" "Picture-in-Picture" \
                "« $title »"$'\n'"joue dans un onglet en arrière-plan — affiche-le pour pouvoir le sortir en PiP."
            exit 0
        fi
    fi
else
    target=$(pick_focused)
fi

# Chrome n'est pas ouvert, ou aucune de ses fenêtres n'est joignable.
[ -n "$target" ] || exit 0

# Mémorise la fenêtre actuellement au premier plan pour y revenir ensuite.
prev=$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')

hyprctl dispatch focuswindow "address:$target" >/dev/null 2>&1 || exit 0
sleep "$FOCUS_DELAY"

# Raccourci de l'extension PiP (Alt+P).
wtype -M alt -k p -m alt

# Revient à la fenêtre d'origine si ce n'était pas celle-là (PiP reste flottant).
if [ -n "$prev" ] && [ "$prev" != "$target" ]; then
    sleep 0.05
    hyprctl dispatch focuswindow "address:$prev" >/dev/null 2>&1
fi
