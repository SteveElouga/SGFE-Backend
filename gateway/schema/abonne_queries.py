import strawberry
import strawberry.types

from schema.abonne_types import Abonne, HistoriqueCompteur, StatutAbonne, abonne_from_grpc, historique_from_grpc
from schema.context import require_role
from schema.grpc_clients import abonne_client


@strawberry.type
class AbonneQueries:
    @strawberry.field()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def abonne(self, info: strawberry.types.Info, id: strawberry.ID) -> Abonne | None:
        """Détails d'un abonné — ADMIN uniquement."""
        require_role(info, "ADMIN")
        return abonne_from_grpc(abonne_client.get_abonne(str(id)))

    @strawberry.field()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def abonnes(
        self,
        info: strawberry.types.Info,
        statut: StatutAbonne | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Abonne]:
        """Liste des abonnés — ADMIN uniquement.

        `limit`/`offset` optionnels : omis, la liste complète est renvoyée à
        l'identique — comportement historique préservé (rétrocompatibilité
        stricte). Voir `abonnesCount` pour le nombre total sans charger la
        liste — utile à un futur pager côté UI.
        """
        require_role(info, "ADMIN")
        statut_str = statut.value if statut else ""
        # Deux branches à mots-clés explicites plutôt qu'un dict + `**` :
        # `list_abonnes` a désormais un paramètre `ids: list[str] | None` en
        # plus de `limit`/`offset: int | None`, et mypy --strict refuse un
        # dict[str, int] déballé en **kwargs dès qu'un des mots-clés
        # possibles n'est pas de ce type (trouvé en CI, PR fan-out gRPC
        # factures_page) — même si ce dict ne contient jamais `ids`. Ceci
        # préserve aussi le contrat exact verrouillé par
        # test_abonnes_sans_limit_offset_renvoie_tout_comme_avant : aucun mot-clé
        # transmis quand les deux sont absents, pas `limit=None, offset=None`.
        if limit is not None or offset is not None:
            response = abonne_client.list_abonnes(statut_str, limit=limit, offset=offset)
        else:
            response = abonne_client.list_abonnes(statut_str)
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def abonnes_count(self, info: strawberry.types.Info, statut: StatutAbonne | None = None) -> int:
        """Nombre total d'abonnés correspondant au filtre — ADMIN uniquement.

        Choix technique (voir le rapport de la tâche « pagination serveur ») :
        une query dédiée plutôt qu'un champ `total` sur `abonnes`, pour ne pas
        changer le type de retour existant (`[Abonne!]!`) et rester
        strictement rétrocompatible avec tout client déjà en place.
        """
        require_role(info, "ADMIN")
        return abonne_client.count_abonnes(statut.value if statut else "")

    @strawberry.field()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def abonnes_actifs(self, info: strawberry.types.Info) -> list[Abonne]:
        """Liste des abonnés ACTIF — ADMIN et SUPERVISEUR (sélecteur pour création de campagne)."""
        require_role(info, "ADMIN", "SUPERVISEUR")
        response = abonne_client.list_abonnes_actifs()
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def historique_compteur(self, info: strawberry.types.Info, id: strawberry.ID) -> list[HistoriqueCompteur]:
        """Historique des remplacements de compteur pour un abonné — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = abonne_client.get_historique_compteur(str(id))
        return [historique_from_grpc(h) for h in response.historique]
