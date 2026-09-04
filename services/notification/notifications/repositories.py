"""Repositories du Notification Service — accès base de données."""

from datetime import date

from django.db.models import Count, Q

from notifications.models import (
    MAX_TENTATIVES_AUTO,
    Diffusion,
    DiffusionEnvoi,
    Envoi,
    StatutDiffusion,
    StatutDiffusionEnvoi,
    StatutEnvoi,
    TokenAcces,
)


class EnvoiRepository:
    """Accès base de données pour les envois WhatsApp."""

    def create(
        self,
        facture_id: str,
        abonne_id: str,
        type_envoi: str,
        telephone: str,
        paiement_id: str = "",
    ) -> Envoi:
        """Crée un envoi en statut EN_ATTENTE.

        `paiement_id` n'est renseigné que pour un reçu : c'est ce qui permet de
        le renvoyer plus tard sans deviner de quel versement il parlait.
        """
        return Envoi.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=type_envoi,
            telephone=telephone,
            paiement_id=paiement_id,
            statut=StatutEnvoi.EN_ATTENTE,
        )

    def get_by_id(self, envoi_id: str) -> Envoi:
        """Récupère un envoi par son UUID. Lève ObjectDoesNotExist si absent."""
        return Envoi.objects.get(id=envoi_id)

    def list_by_facture_and_abonne(self, facture_id: str, abonne_id: str) -> list[Envoi]:
        """Liste les envois filtrés par facture et/ou abonné."""
        qs = Envoi.objects.all()
        if facture_id:
            qs = qs.filter(facture_id=facture_id)
        if abonne_id:
            qs = qs.filter(abonne_id=abonne_id)
        return list(qs.order_by("-created_at"))

    def save(self, envoi: Envoi) -> Envoi:
        """Persiste les modifications d'un envoi."""
        envoi.save()
        return envoi

    def list_echecs_a_retenter(self, limite: int) -> list[Envoi]:
        """Lot d'envois en ECHEC sous le plafond de tentatives automatiques
        (`MAX_TENTATIVES_AUTO`), les plus anciens d'abord — pour qu'un échec
        ancien ne soit pas indéfiniment dépassé par des échecs plus récents.

        Le filtre `tentatives__lt` est le cap dur du retry automatique : un
        envoi qui a atteint le plafond n'est plus jamais sélectionné ici, quel
        que soit le nombre de passages du job.
        """
        return list(
            Envoi.objects.filter(statut=StatutEnvoi.ECHEC, tentatives__lt=MAX_TENTATIVES_AUTO).order_by("created_at")[
                :limite
            ]
        )


class DiffusionRepository:
    """Accès base de données pour les diffusions de masse."""

    def create(self, message: str, created_by: str, abonnes: list[tuple[str, str]]) -> Diffusion:
        """Crée une diffusion et ses lignes d'envoi (une par abonné visé).

        `abonnes` : paires (abonne_id, telephone) déjà résolues par l'appelant.
        Une seule transaction : soit la diffusion et toutes ses lignes existent,
        soit aucune — jamais une diffusion sans destinataire par écriture
        interrompue à mi-chemin.
        """
        from django.db import transaction

        with transaction.atomic():
            diffusion = Diffusion.objects.create(message=message, created_by=created_by)
            DiffusionEnvoi.objects.bulk_create(
                DiffusionEnvoi(diffusion=diffusion, abonne_id=abonne_id, telephone=telephone)
                for abonne_id, telephone in abonnes
            )
        return diffusion

    def get_by_id(self, diffusion_id: str) -> Diffusion:
        """Récupère une diffusion par son UUID. Lève ObjectDoesNotExist si absente."""
        return Diffusion.objects.get(id=diffusion_id)

    def list_all(self) -> list[Diffusion]:
        """Liste toutes les diffusions, la plus récente d'abord."""
        return list(Diffusion.objects.order_by("-created_at"))

    def compter(self, diffusion: Diffusion) -> tuple[int, int, int]:
        """(nb_total, nb_envoyes, nb_echecs) — calculés à la demande, jamais stockés."""
        agg = diffusion.envois.aggregate(
            total=Count("id"),
            envoyes=Count("id", filter=Q(statut=StatutDiffusionEnvoi.ENVOYE)),
            echecs=Count("id", filter=Q(statut=StatutDiffusionEnvoi.ECHEC)),
        )
        return agg["total"], agg["envoyes"], agg["echecs"]

    def prochains_en_attente(self, limite: int) -> list[DiffusionEnvoi]:
        """Un lot de lignes EN_ATTENTE à traiter, les diffusions les plus
        anciennes d'abord — pour qu'une diffusion lancée avant une autre
        termine avant elle plutôt que d'être intercalée au hasard."""
        return list(
            DiffusionEnvoi.objects.filter(statut=StatutDiffusionEnvoi.EN_ATTENTE)
            .select_related("diffusion")
            .order_by("diffusion__created_at", "id")[:limite]
        )

    def save_envoi(self, envoi: DiffusionEnvoi) -> DiffusionEnvoi:
        """Persiste les modifications d'une ligne d'envoi."""
        envoi.save()
        return envoi

    def terminer_si_completes(self) -> list[str]:
        """Passe en TERMINEE toute diffusion EN_COURS sans ligne EN_ATTENTE
        restante. Retourne les id de celles qui viennent de changer, pour que
        l'appelant publie l'événement de progression correspondant."""
        termine_ids = []
        for diffusion in Diffusion.objects.filter(statut=StatutDiffusion.EN_COURS):
            if not diffusion.envois.filter(statut=StatutDiffusionEnvoi.EN_ATTENTE).exists():
                diffusion.statut = StatutDiffusion.TERMINEE
                diffusion.save()
                termine_ids.append(str(diffusion.id))
        return termine_ids


class TokenAccesRepository:
    """Accès base de données pour les tokens d'accès abonné."""

    def create(
        self,
        abonne_id: str,
        facture_id: str,
        date_expiration: date,
    ) -> TokenAcces:
        """Crée un nouveau token actif."""
        return TokenAcces.objects.create(
            abonne_id=abonne_id,
            facture_id=facture_id,
            date_expiration=date_expiration,
        )

    def get_by_id(self, token_id: str) -> TokenAcces:
        """Récupère un TokenAcces par son UUID primaire. Lève ObjectDoesNotExist si absent."""
        return TokenAcces.objects.get(id=token_id)

    def get_by_token(self, token: str) -> TokenAcces:
        """Récupère un TokenAcces par la valeur du token partagé dans l'URL."""
        return TokenAcces.objects.get(token=token)

    def list_active_by_facture(self, facture_id: str) -> list[TokenAcces]:
        """Liste les tokens actifs d'une facture."""
        return list(TokenAcces.objects.filter(facture_id=facture_id, is_active=True))

    def get_latest_valid_by_abonne(self, abonne_id: str) -> TokenAcces | None:
        """Dernier token actif et non expiré d'un abonné (None si aucun)."""
        return (
            TokenAcces.objects.filter(
                abonne_id=abonne_id,
                is_active=True,
                date_expiration__gte=date.today(),
            )
            .order_by("-created_at")
            .first()
        )

    def revoquer_tous_actifs(self) -> int:
        """Révoque en masse tous les tokens actifs. Retourne le nombre révoqué."""
        return TokenAcces.objects.filter(is_active=True).update(is_active=False)

    def save(self, token: TokenAcces) -> TokenAcces:
        """Persiste les modifications d'un token."""
        token.save()
        return token
