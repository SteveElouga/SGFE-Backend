"""Métriques métier custom (OpenTelemetry Metrics API) du Campagne Service.

Ces compteurs complètent les métriques RED automatiques déjà exposées via
`opentelemetry-instrument` (traces + métriques HTTP/gRPC) : ils mesurent des
événements métier (ex. campagne démarrée) que l'auto-instrumentation ne peut
pas connaître. Aucune étiquette ici — voir CLAUDE.md, §Observabilité : jamais
un identifiant en label, seules des catégories fermées à faible cardinalité
seraient acceptées, et ces quatre métriques n'en ont pas besoin.
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

campagne_demarree_total = _meter.create_counter(
    name="sgfe.campagne.demarree",
    unit="1",
    description="Nombre de campagnes démarrées (manuellement ou via le cron de démarrage planifié).",
)

campagne_cloturee_total = _meter.create_counter(
    name="sgfe.campagne.cloturee",
    unit="1",
    description="Nombre de campagnes clôturées.",
)

releve_saisi_total = _meter.create_counter(
    name="sgfe.releve.saisi",
    unit="1",
    description="Nombre de relevés d'index saisis.",
)

releve_corrige_total = _meter.create_counter(
    name="sgfe.releve.corrige",
    unit="1",
    description="Nombre de relevés d'index corrigés après saisie.",
)
