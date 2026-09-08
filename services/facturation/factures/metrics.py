"""Métriques métier custom (OpenTelemetry Metrics API) du service Facturation.

Ces compteurs complètent les métriques RED automatiques déjà exportées par
`opentelemetry-instrument` (traces + métriques HTTP/gRPC) : ils mesurent des
événements métier qu'une instrumentation générique ne peut pas connaître —
combien de factures ont été générées, annulées ou régénérées.

Aucun de ces compteurs ne porte de label : le catalogue de métriques ne
prévoit pas de dimension pour ce service (voir CLAUDE.md racine — jamais un
identifiant en étiquette, seules des catégories fermées à faible cardinalité
seraient acceptées, et il n'y en a pas ici).
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

facture_generee_total = _meter.create_counter(
    name="sgfe.facture.generee",
    unit="1",
    description="Nombre de factures générées.",
)

facture_annulee_total = _meter.create_counter(
    name="sgfe.facture.annulee",
    unit="1",
    description="Nombre de factures annulées.",
)

facture_regeneree_total = _meter.create_counter(
    name="sgfe.facture.regeneree",
    unit="1",
    description="Nombre de factures régénérées (annulation + réémission corrigée).",
)
