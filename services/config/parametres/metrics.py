"""Métriques métier custom (OpenTelemetry Metrics API) pour le service Config.

Ces compteurs complètent les métriques RED automatiques déjà exposées via
`opentelemetry-instrument` (traces + métriques gRPC) : ils mesurent des
événements métier (ex. paramètre de configuration modifié) que
l'auto-instrumentation ne peut pas connaître. Étiquettes : uniquement des
catégories fermées à faible cardinalité — jamais un identifiant arbitraire
(voir CLAUDE.md, règle de cardinalité).
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

# Étiquette `cle` volontairement conservée ici : contrairement à un identifiant
# arbitraire, l'ensemble des clés de configuration valides est fermé et fini —
# `parametres.models.CONFIG_DEFAULTS` en dénombre 10, et
# `ConfigParamRepository.get_or_default`/`ConfigService.update` refusent
# (`ObjectDoesNotExist`) toute clé qui n'y figure pas. `ConfigParam.cle` est un
# `CharField` libre en base, mais aucune mutation ne peut jamais atteindre une
# valeur hors de cet ensemble fermé : la cardinalité reste bornée à 10.
config_modifie_total = _meter.create_counter(
    name="sgfe.config.modifie",
    unit="1",
    description="Nombre de modifications de paramètres de configuration, par clé.",
)
