"""sgfe_common — code partagé entre les composants gRPC internes de SGFE.

Ce paquet n'est PAS installé (pip) dans les services aujourd'hui — voir
`libs/sgfe_common/README.md`. Il sert de source canonique unique, recopiée
vers chaque service par `scripts/sync-grpc-lib.sh`. Le layout est
volontairement "package-shaped" pour permettre une bascule ultérieure vers un
vrai `pip install -e` sans déplacer les fichiers, si le contexte de build
Docker (aujourd'hui scopé à chaque `services/<nom>/`) est un jour restructuré
pour l'autoriser.
"""
