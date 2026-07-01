"""Tests des services du Notification Service.

Vérifie la logique métier de EnvoiService et TokenService.
Les dépendances externes (grpc_clients, whatsapp_client) sont mockées.
"""

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from notifications.models import StatutEnvoi, TokenAcces, TypeEnvoi
from notifications.services import EnvoiService, TokenService


def _make_facture_mock(
    facture_id: str | None = None,
    abonne_id: str | None = None,
    consommation: float = 15.0,
    montant: float = 7500.0,
    date_releve: str = "2025-07-01",
    date_limite_paiement: str = "2025-07-20",
) -> MagicMock:
    """Construit un mock de FactureResponse."""
    mock = MagicMock()
    mock.facture_id = facture_id or str(uuid.uuid4())
    mock.abonne_id = abonne_id or str(uuid.uuid4())
    mock.consommation = consommation
    mock.montant = montant
    mock.date_releve = date_releve
    mock.date_limite_paiement = date_limite_paiement
    return mock


def _make_abonne_mock(
    abonne_id: str | None = None,
    nom: str = "DUPONT",
    prenom: str = "Jean",
    telephone: str = "+237699000001",
) -> MagicMock:
    """Construit un mock de AbonneResponse."""
    mock = MagicMock()
    mock.abonne_id = abonne_id or str(uuid.uuid4())
    mock.nom = nom
    mock.prenom = prenom
    mock.telephone_whatsapp = telephone
    return mock


class TestEnvoiServiceEnvoyerFacture(TestCase):
    """Tests de EnvoiService.envoyer_facture."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_facture_succes(self, mock_fact, mock_abonne, mock_config, mock_wa):
        """Un envoi réussi crée un Envoi avec statut ENVOYE."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None  # Pas d'exception = succès

        service = EnvoiService()
        envoi = service.envoyer_facture(facture_id, abonne_id)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.facture_id, facture_id)
        self.assertEqual(envoi.abonne_id, abonne_id)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.FACTURE)
        self.assertIsNotNone(envoi.date_envoi)
        self.assertEqual(envoi.tentatives, 1)
        mock_wa.send.assert_called_once()

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_facture_whatsapp_ko(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """Si WhatsApp échoue, l'envoi est marqué ECHEC sans lever d'exception."""
        from notifications.whatsapp_client import WhatsAppDeliveryError

        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.side_effect = WhatsAppDeliveryError("Service inaccessible")

        service = EnvoiService()
        # Ne doit pas lever d'exception
        envoi = service.envoyer_facture(facture_id, abonne_id)

        self.assertEqual(envoi.statut, StatutEnvoi.ECHEC)
        self.assertIn("inaccessible", envoi.erreur)
        self.assertEqual(envoi.tentatives, 1)

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_facture_cree_token_acces(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """Un envoi de facture doit créer un TokenAcces en base."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        service = EnvoiService()
        service.envoyer_facture(facture_id, abonne_id)

        tokens = TokenAcces.objects.filter(facture_id=facture_id, abonne_id=abonne_id)
        self.assertEqual(tokens.count(), 1)
        token = tokens.first()
        self.assertTrue(token.is_active)
        self.assertEqual(token.date_expiration, date.today() + timedelta(days=20))


class TestEnvoiServiceEnvoyerRelance(TestCase):
    """Tests de EnvoiService.envoyer_relance."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_relance_etape_1(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """La relance étape 1 doit envoyer un message RELANCE_1."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        service = EnvoiService()
        envoi = service.envoyer_relance(facture_id, abonne_id, etape=1)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.RELANCE_1)
        # Le message doit inclure un lien espace abonné
        args, _ = mock_wa.send.call_args
        self.assertIn("/espace/", args[1])

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_relance_etape_2(self, mock_fact, mock_abonne, mock_wa):
        """La relance étape 2 doit envoyer un message RELANCE_2."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_wa.send.return_value = None

        service = EnvoiService()
        envoi = service.envoyer_relance(facture_id, abonne_id, etape=2)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.RELANCE_2)
        args, _ = mock_wa.send.call_args
        self.assertIn("3 jours", args[1])

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_relance_etape_3(self, mock_fact, mock_abonne, mock_wa):
        """La relance étape 3 doit envoyer un message AVERTISSEMENT."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_wa.send.return_value = None

        service = EnvoiService()
        envoi = service.envoyer_relance(facture_id, abonne_id, etape=3)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.AVERTISSEMENT)
        args, _ = mock_wa.send.call_args
        self.assertIn("AVERTISSEMENT", args[1])

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_relance_etape_4(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """La relance étape 4 doit envoyer un message SUSPENSION."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_infos_societe.return_value = MagicMock(
            telephone="+237690000000"
        )
        mock_wa.send.return_value = None

        service = EnvoiService()
        envoi = service.envoyer_relance(facture_id, abonne_id, etape=4)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.SUSPENSION)
        args, _ = mock_wa.send.call_args
        self.assertIn("suspendue", args[1])

    def test_envoyer_relance_etape_invalide(self):
        """Une étape hors de [1, 4] doit lever une ValidationError."""
        service = EnvoiService()
        with self.assertRaises(ValidationError):
            service.envoyer_relance("facture-id", "abonne-id", etape=5)

    def test_envoyer_relance_etape_zero_invalide(self):
        """L'étape 0 est invalide et doit lever une ValidationError."""
        service = EnvoiService()
        with self.assertRaises(ValidationError):
            service.envoyer_relance("facture-id", "abonne-id", etape=0)


class TestEnvoiServiceRenvoyer(TestCase):
    """Tests de EnvoiService.renvoyer_facture."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_renvoyer_facture_revoque_ancien_token(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """renvoyer_facture doit révoquer les tokens actifs existants."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        # Crée un premier token actif
        ancien_token = TokenAcces.objects.create(
            abonne_id=abonne_id,
            facture_id=facture_id,
            date_expiration=date.today() + timedelta(days=20),
        )
        self.assertTrue(ancien_token.is_active)

        service = EnvoiService()
        service.renvoyer_facture(facture_id)

        # L'ancien token doit être révoqué
        ancien_token.refresh_from_db()
        self.assertFalse(ancien_token.is_active)

        # Un nouveau token actif doit exister
        tokens_actifs = TokenAcces.objects.filter(facture_id=facture_id, is_active=True)
        self.assertEqual(tokens_actifs.count(), 1)


class TestTokenService(TestCase):
    """Tests de TokenService."""

    def _create_token(
        self, is_active: bool = True, jours_expiration: int = 20
    ) -> TokenAcces:
        """Crée un TokenAcces pour les tests."""
        return TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=jours_expiration),
            is_active=is_active,
        )

    def test_valider_token_valide(self):
        """valider_token retourne le token si actif et non expiré."""
        token = self._create_token()
        service = TokenService()

        result = service.valider_token(str(token.token))

        self.assertEqual(result.id, token.id)
        self.assertIsNotNone(result.date_derniere_visite)

    def test_valider_token_expire(self):
        """valider_token lève ValueError si le token est expiré."""
        token = self._create_token(jours_expiration=-1)  # Expiré hier
        service = TokenService()

        with self.assertRaises(ValueError) as ctx:
            service.valider_token(str(token.token))
        self.assertIn("expiré", str(ctx.exception))

    def test_valider_token_revoque(self):
        """valider_token lève ValueError si le token est révoqué (is_active=False)."""
        token = self._create_token(is_active=False)
        service = TokenService()

        with self.assertRaises(ValueError) as ctx:
            service.valider_token(str(token.token))
        self.assertIn("révoqué", str(ctx.exception))

    def test_valider_token_introuvable(self):
        """valider_token lève ObjectDoesNotExist si le token n'existe pas."""
        service = TokenService()

        with self.assertRaises(ObjectDoesNotExist):
            service.valider_token(str(uuid.uuid4()))

    def test_revoquer_token(self):
        """revoquer_token met is_active à False."""
        token = self._create_token()
        service = TokenService()

        service.revoquer_token(str(token.id))

        token.refresh_from_db()
        self.assertFalse(token.is_active)

    def test_revoquer_token_introuvable(self):
        """revoquer_token lève ObjectDoesNotExist si le token est introuvable."""
        service = TokenService()

        with self.assertRaises(ObjectDoesNotExist):
            service.revoquer_token(str(uuid.uuid4()))

    def test_valider_token_met_a_jour_derniere_visite(self):
        """valider_token doit mettre à jour date_derniere_visite."""
        token = self._create_token()
        self.assertIsNone(token.date_derniere_visite)

        service = TokenService()
        service.valider_token(str(token.token))

        token.refresh_from_db()
        self.assertIsNotNone(token.date_derniere_visite)
