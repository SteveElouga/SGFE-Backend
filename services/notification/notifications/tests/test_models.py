"""Tests des modèles Django du Notification Service.

Vérifie la création et les valeurs par défaut de Envoi et TokenAcces.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from django.test import TestCase

from notifications.models import MAX_TENTATIVES_AUTO, Envoi, StatutEnvoi, TokenAcces, TypeEnvoi


class TestEnvoiModel(TestCase):
    """Tests unitaires du modèle Envoi."""

    def _create_envoi(self, **kwargs: Any) -> Envoi:
        """Crée un Envoi avec des valeurs par défaut raisonnables."""
        defaults = {
            "facture_id": str(uuid.uuid4()),
            "abonne_id": str(uuid.uuid4()),
            "type_envoi": TypeEnvoi.FACTURE,
            "telephone": "+237699000001",
        }
        defaults.update(kwargs)
        return Envoi.objects.create(**defaults)

    def test_creation_envoi_valeurs_par_defaut(self) -> None:
        """Un Envoi créé sans statut explicite doit être EN_ATTENTE."""
        envoi = self._create_envoi()

        self.assertIsNotNone(envoi.id)
        self.assertIsInstance(envoi.id, uuid.UUID)
        self.assertEqual(envoi.statut, StatutEnvoi.EN_ATTENTE)
        self.assertEqual(envoi.tentatives, 0)
        self.assertEqual(envoi.erreur, "")
        self.assertEqual(envoi.telnyx_message_id, "")
        self.assertIsNone(envoi.date_envoi)
        self.assertIsNotNone(envoi.created_at)
        self.assertEqual(envoi.dernier_message, "")
        self.assertFalse(envoi.avec_pdf)
        self.assertEqual(envoi.pdf_filename, "")

    def test_creation_envoi_type_facture(self) -> None:
        """Un Envoi de type FACTURE doit conserver son type_envoi."""
        envoi = self._create_envoi(type_envoi=TypeEnvoi.FACTURE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.FACTURE)

    def test_creation_envoi_type_relance_1(self) -> None:
        """Un Envoi de type RELANCE_1 doit conserver son type_envoi."""
        envoi = self._create_envoi(type_envoi=TypeEnvoi.RELANCE_1)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.RELANCE_1)

    def test_creation_envoi_type_suspension(self) -> None:
        """Un Envoi de type SUSPENSION doit conserver son type_envoi."""
        envoi = self._create_envoi(type_envoi=TypeEnvoi.SUSPENSION)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.SUSPENSION)

    def test_mise_a_jour_statut_envoye(self) -> None:
        """Le statut d'un Envoi doit pouvoir passer à ENVOYE."""
        envoi = self._create_envoi()
        envoi.statut = StatutEnvoi.ENVOYE
        envoi.save()

        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)

    def test_mise_a_jour_statut_echec(self) -> None:
        """Le statut d'un Envoi doit pouvoir passer à ECHEC avec un message d'erreur."""
        envoi = self._create_envoi()
        envoi.statut = StatutEnvoi.ECHEC
        envoi.erreur = "Service WhatsApp inaccessible"
        envoi.save()

        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, StatutEnvoi.ECHEC)
        self.assertIn("inaccessible", envoi.erreur)

    def test_str_envoi(self) -> None:
        """La représentation textuelle d'un Envoi doit être informative."""
        envoi = self._create_envoi(type_envoi=TypeEnvoi.FACTURE)
        self.assertIn("FACTURE", str(envoi))
        self.assertIn("EN_ATTENTE", str(envoi))

    def test_dernier_message_et_avec_pdf_persistes(self) -> None:
        """`dernier_message`/`avec_pdf`/`pdf_filename` doivent être persistés
        tels quels — c'est ce que rejoue le retry automatique."""
        envoi = self._create_envoi(
            dernier_message="Votre facture de 7500 FCFA est disponible.",
            avec_pdf=True,
            pdf_filename="facture-123.pdf",
        )

        envoi.refresh_from_db()
        self.assertEqual(envoi.dernier_message, "Votre facture de 7500 FCFA est disponible.")
        self.assertTrue(envoi.avec_pdf)
        self.assertEqual(envoi.pdf_filename, "facture-123.pdf")

    def test_max_tentatives_auto_est_cinq(self) -> None:
        """Le plafond de retentatives automatiques est figé à 5 — le même
        ordre de grandeur que MAX_DELIVERY_ATTEMPTS côté reporting."""
        self.assertEqual(MAX_TENTATIVES_AUTO, 5)


class TestTokenAccesModel(TestCase):
    """Tests unitaires du modèle TokenAcces."""

    def _create_token(self, **kwargs: Any) -> TokenAcces:
        """Crée un TokenAcces avec des valeurs par défaut raisonnables."""
        defaults = {
            "abonne_id": str(uuid.uuid4()),
            "facture_id": str(uuid.uuid4()),
            "date_expiration": date.today() + timedelta(days=20),
        }
        defaults.update(kwargs)
        return TokenAcces.objects.create(**defaults)

    def test_creation_token_valeurs_par_defaut(self) -> None:
        """Un TokenAcces créé sans valeurs explicites doit avoir un token UUID unique."""
        token = self._create_token()

        self.assertIsNotNone(token.id)
        self.assertIsInstance(token.id, uuid.UUID)
        self.assertIsInstance(token.token, uuid.UUID)
        self.assertTrue(token.is_active)
        self.assertIsNone(token.date_derniere_visite)
        self.assertIsNotNone(token.created_at)

    def test_tokens_differents_uuid(self) -> None:
        """Deux TokenAcces distincts doivent avoir des tokens UUID différents."""
        token1 = self._create_token()
        token2 = self._create_token()
        self.assertNotEqual(token1.token, token2.token)

    def test_revocation_token(self) -> None:
        """Un token peut être révoqué en passant is_active à False."""
        token = self._create_token()
        token.is_active = False
        token.save()

        token.refresh_from_db()
        self.assertFalse(token.is_active)

    def test_str_token(self) -> None:
        """La représentation textuelle d'un TokenAcces doit contenir le statut."""
        token = self._create_token()
        self.assertIn("actif", str(token))

        token.is_active = False
        token.save()
        self.assertIn("révoqué", str(token))

    def test_date_expiration_future(self) -> None:
        """La date d'expiration doit être persistée correctement."""
        expiration = date.today() + timedelta(days=20)
        token = self._create_token(date_expiration=expiration)

        token.refresh_from_db()
        self.assertEqual(token.date_expiration, expiration)
