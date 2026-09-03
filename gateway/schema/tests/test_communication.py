"""Tests des resolvers GraphQL des diffusions (Notification Service, gateway)."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.communication_mutations import CommunicationMutations
from schema.communication_queries import CommunicationQueries


def _diffusion_response(**kwargs: object) -> MagicMock:
    defaults = dict(
        diffusion_id="diff-001",
        message="Coupure d'eau demain 8h-12h",
        statut="EN_COURS",
        nb_total=12,
        nb_envoyes=3,
        nb_echecs=0,
        created_by="user-001",
        created_at="2026-09-03T10:00:00",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestCommunicationMutations(SimpleTestCase):
    @patch("schema.communication_mutations.notification_client")
    @patch("schema.communication_mutations.require_role")
    def test_creer_diffusion(self, mock_role: MagicMock, mock_client: MagicMock) -> None:
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-001", username="demo_admin")
        mock_client.creer_diffusion.return_value = _diffusion_response()

        result = CommunicationMutations().creer_diffusion(
            MagicMock(), message="Coupure d'eau demain 8h-12h", abonne_ids=["a1", "a2"]
        )

        self.assertEqual(result.message, "Coupure d'eau demain 8h-12h")
        self.assertEqual(result.nb_total, 12)
        # Le nom d'utilisateur de l'opérateur courant vient du JWT — pas d'aller-retour
        # vers Auth Service pour la mutation.
        self.assertEqual(result.created_by, "demo_admin")
        mock_client.creer_diffusion.assert_called_once_with(
            message="Coupure d'eau demain 8h-12h", abonne_ids=["a1", "a2"], created_by="user-001"
        )


class TestCommunicationQueries(SimpleTestCase):
    @patch("schema.communication_queries.auth_client")
    @patch("schema.communication_queries.notification_client")
    @patch("schema.communication_queries.require_role")
    def test_diffusion_resout_l_operateur(
        self, mock_role: MagicMock, mock_client: MagicMock, mock_auth: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="ADMIN")
        mock_client.get_diffusion.return_value = _diffusion_response()
        mock_auth.get_user.return_value = MagicMock(username="demo_admin")

        result = CommunicationQueries().diffusion(MagicMock(), diffusion_id="diff-001")

        self.assertEqual(result.diffusion_id, "diff-001")
        self.assertEqual(result.created_by, "demo_admin")

    @patch("schema.communication_queries.auth_client")
    @patch("schema.communication_queries.notification_client")
    @patch("schema.communication_queries.require_role")
    def test_diffusion_operateur_introuvable_replie_sur_l_identifiant(
        self, mock_role: MagicMock, mock_client: MagicMock, mock_auth: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="ADMIN")
        mock_client.get_diffusion.return_value = _diffusion_response(created_by="user-00112233")
        mock_auth.get_user.side_effect = Exception("indisponible")

        result = CommunicationQueries().diffusion(MagicMock(), diffusion_id="diff-001")

        self.assertEqual(result.created_by, "Utilisateur user-001")

    @patch("schema.communication_queries.auth_client")
    @patch("schema.communication_queries.notification_client")
    @patch("schema.communication_queries.require_role")
    def test_diffusions_liste_toutes_les_diffusions(
        self, mock_role: MagicMock, mock_client: MagicMock, mock_auth: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="ADMIN")
        mock_client.list_diffusions.return_value = MagicMock(
            diffusions=[_diffusion_response(diffusion_id="diff-001"), _diffusion_response(diffusion_id="diff-002")]
        )
        mock_auth.get_user.return_value = MagicMock(username="demo_admin")

        result = CommunicationQueries().diffusions(MagicMock())

        self.assertEqual(len(result), 2)
        self.assertEqual({d.diffusion_id for d in result}, {"diff-001", "diff-002"})
