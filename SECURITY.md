# Politique de sécurité

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir d'issue publique pour signaler une faille de
sécurité (clé exposée, contournement d'authentification, injection, fuite de
données personnelles, etc.).

Deux façons de la signaler en privé :

1. **Recommandé** — onglet *Security* du dépôt GitHub
   ([SteveElouga/SGFE-Backend](https://github.com/SteveElouga/SGFE-Backend)) →
   *Report a vulnerability* (GitHub Private Vulnerability Reporting). C'est le
   canal le plus direct et le plus traçable.
2. Si ce canal n'est pas activé ou accessible : contacter directement le
   mainteneur via son profil GitHub ([@SteveElouga](https://github.com/SteveElouga)),
   en précisant que le message concerne une vulnérabilité.

Merci d'inclure, dans la mesure du possible :

- le service ou fichier concerné (ex. `services/auth`, `gateway/`, `nginx/`) ;
- les étapes de reproduction ou un `fichier:ligne` si le problème vient de la
  lecture du code ;
- l'impact estimé (accès non autorisé, fuite de PII, déni de service, etc.).

## Délai de réponse

Ce dépôt est maintenu par une seule personne. Le délai indicatif de première
réponse est de **5 jours ouvrés** — c'est un objectif, pas une garantie
contractuelle. Une fois la vulnérabilité confirmée, un correctif est priorisé
selon sa sévérité avant toute divulgation publique.

## Versions supportées

Ce projet n'a pas de schéma de versions publiées (pas de tags ni de releases
séparées) : il est déployé en continu depuis la branche `main`. Seul l'état
courant de `main` (production) est couvert par cette politique ; les branches
de travail (`develop`, `feature/*`, `fix/*`, etc.) ne le sont pas.

## Périmètre

Ce dépôt couvre l'API Gateway (`gateway/`), les microservices (`services/`) et
la configuration `nginx/`/Docker de ce backend. Pour une vulnérabilité côté
frontend Angular, la signaler de la même façon sur le dépôt `SGFE-frontend`.
