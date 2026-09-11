#!/bin/bash
# État de la caféine pour le module waybar custom/caffeine.
#
# Émet une ligne JSON : icône tant que caffeine.service tourne, texte vide
# sinon — waybar masque alors le module. La caféine ne prend donc de la place
# dans la barre que quand elle a quelque chose à rappeler : qu'on l'a laissée
# allumée. Rafraîchi par SIGRTMIN+15 (caffeine-toggle.sh) et toutes les 60 s
# en filet de sécurité (unité arrêtée à la main, session restaurée).
if systemctl --user -q is-active caffeine; then
    printf '{"text":"\ueb44","tooltip":"Caféine active : veille et verrouillage bloqués\\nclic : rétablir la veille","class":"active"}\n'
else
    printf '{"text":""}\n'
fi
