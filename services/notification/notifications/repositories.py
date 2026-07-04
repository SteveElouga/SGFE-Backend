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
    ) -> Envoi:
        """Crée un envoi en statut EN_ATTENTE."""
        return Envoi.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=type_envoi,
            telephone=telephone,
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

    def revoquer_tous_actifs(self) -> int:
        """Révoque en masse tous les tokens actifs. Retourne le nombre révoqué."""
        return TokenAcces.objects.filter(is_active=True).update(is_active=False)

    def save(self, token: TokenAcces) -> TokenAcces:
        """Persiste les modifications d'un token."""
        token.save()
        return token
