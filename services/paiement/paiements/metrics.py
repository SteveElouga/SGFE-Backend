"""Métriques métier custom (OpenTelemetry Metrics API) du Paiement Service.

Ces compteurs complètent les métriques RED automatiques déjà exposées via
`opentelemetry-instrument` (traces + métriques HTTP/gRPC) : ils mesurent des
événements métier (paiement enregistré, montant encaissé, annulation,
escalade de relance) que l'auto-instrumentation ne peut pas connaître.

Règle de cardinalité : les seuls attributs utilisés (`mode_paiement`,
`etape`) sont des catégories fermées à faible cardinalité — jamais un
identifiant de paiement/facture/abonné en étiquette, ce qui ferait exploser
la cardinalité côté Prometheus.
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

paiement_enregistre_total = _meter.create_counter(
    name="sgfe.paiement.enregistre",
    unit="1",
    description="Nombre de versements enregistrés, par mode de paiement.",
)

paiement_montant_encaisse_total = _meter.create_counter(
    name="sgfe.paiement.montant_encaisse",
    unit="FCFA",
    description="Montant total encaissé, par mode de paiement.",
)

paiement_annule_total = _meter.create_counter(
    name="sgfe.paiement.annule",
    unit="1",
    description="Nombre de paiements annulés.",
)

relance_escaladee_total = _meter.create_counter(
    name="sgfe.relance.escaladee",
    unit="1",
    description="Nombre de relances d'impayé franchies, par étape (1 à 4).",
)
