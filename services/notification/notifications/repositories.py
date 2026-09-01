"""Repositories du Notification Service — accès base de données."""

from datetime import date


from notifications.models import Envoi, StatutEnvoi, TokenAcces


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
