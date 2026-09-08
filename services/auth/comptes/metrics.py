"""Métriques métier custom (OpenTelemetry Metrics API) du service Auth.

Ces compteurs complètent les métriques RED automatiques déjà exposées par
`opentelemetry-instrument` (traces + métriques HTTP/gRPC) : ils mesurent des
événements métier (connexions, création/désactivation d'utilisateurs) que
l'auto-instrumentation ne peut pas connaître. Étiquettes limitées à des
catégories fermées et à faible cardinalité — jamais un identifiant
utilisateur, un nom ou un e-mail (voir CLAUDE.md racine, règle de
cardinalité).

`metrics.get_meter(__name__)` renvoie un meter « proxy » tant qu'aucun
`MeterProvider` réel n'est configuré (ce qui est le cas ici : le SDK OTel est
initialisé en dehors du code Django, par `opentelemetry-instrument`, voir
docker-compose.yml). Les compteurs créés dès l'import de ce module sont
automatiquement reliés au vrai provider une fois celui-ci posé — aucune
initialisation paresseuse n'est nécessaire côté application.
"""

from opentelemetry import metrics

_meter = metrics.get_meter(__name__)

auth_connexion_total = _meter.create_counter(
    name="sgfe.auth.connexion",
    unit="1",
    description="Nombre de tentatives de connexion, par résultat (succes/echec).",
)

utilisateur_cree_total = _meter.create_counter(
    name="sgfe.utilisateur.cree",
    unit="1",
    description="Nombre d'utilisateurs créés.",
)

utilisateur_desactive_total = _meter.create_counter(
    name="sgfe.utilisateur.desactive",
    unit="1",
    description="Nombre d'utilisateurs désactivés.",
)
