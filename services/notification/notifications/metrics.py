"""Métriques métier custom (OpenTelemetry Metrics API) du Notification Service.

Complète les métriques RED automatiques déjà exposées par
`opentelemetry-instrument` (traces + latence/volume/erreurs des RPC) avec des
compteurs métier que l'auto-instrumentation ne peut pas connaître : un envoi
WhatsApp réussi ou en échec, une ligne de diffusion traitée, une tentative de
retry automatique.

Étiquettes : uniquement des catégories fermées à faible cardinalité
(`type_envoi`, `statut`) — jamais un identifiant (`envoi_id`, `abonne_id`,
`diffusion_id`...), qui ferait exploser la cardinalité Prometheus. Voir
CLAUDE.md.
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

notification_envoi_total = _meter.create_counter(
    name="sgfe.notification.envoi",
    unit="1",
    description="Nombre d'envois WhatsApp tentés, par type d'envoi et statut résultant.",
)

notification_diffusion_envoi_total = _meter.create_counter(
    name="sgfe.notification.diffusion_envoi",
    unit="1",
    description="Nombre de lignes de diffusion traitées, par statut résultant.",
)

notification_retry_total = _meter.create_counter(
    name="sgfe.notification.retry",
    unit="1",
    description="Nombre de tentatives de retry automatique d'envois WhatsApp en échec.",
)
