"""Métriques métier custom (OpenTelemetry Metrics API) du service Abonné.

Complète les métriques RED automatiques déjà exposées via
`opentelemetry-instrument` (traces + métriques HTTP/gRPC, voir
`docker-compose.yml`) : celles-ci mesurent la santé technique du service
(latence, débit, taux d'erreur) mais ne savent rien du métier — combien
d'abonnés ont été créés, suspendus, réactivés, résiliés, ou combien de
compteurs ont été remplacés. Chaque compteur ci-dessous est incrémenté
juste après le succès réel de l'action correspondante dans
`abonnes/services.py`, au même endroit que l'appel à `enregistrer_audit`
déjà présent.

Aucune étiquette ne porte d'identifiant (abonné, compteur...) : seules des
catégories fermées à faible cardinalité seraient acceptées, et aucune de
ces métriques n'en a besoin.
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

abonne_cree_total = _meter.create_counter(
    name="sgfe.abonne.cree",
    unit="1",
    description="Nombre d'abonnés créés.",
)

abonne_suspendu_total = _meter.create_counter(
    name="sgfe.abonne.suspendu",
    unit="1",
    description=(
        "Nombre d'abonnés suspendus, quelle que soit l'origine de la demande "
        "(admin ou gRPC depuis le service Paiement)."
    ),
)

abonne_reactive_total = _meter.create_counter(
    name="sgfe.abonne.reactive",
    unit="1",
    description="Nombre d'abonnés réactivés.",
)

abonne_resilie_total = _meter.create_counter(
    name="sgfe.abonne.resilie",
    unit="1",
    description="Nombre d'abonnés résiliés.",
)

abonne_compteur_remplace_total = _meter.create_counter(
    name="sgfe.abonne.compteur_remplace",
    unit="1",
    description="Nombre de compteurs remplacés.",
)

compteur_position_maj_total = _meter.create_counter(
    name="sgfe.abonne.compteur_position_maj",
    unit="1",
    description=(
        "Nombre de coordonnées de compteur mises à jour (import CSV en masse, "
        "voir CompteurService.importer_coordonnees) — une unité par ligne importée avec succès."
    ),
)
