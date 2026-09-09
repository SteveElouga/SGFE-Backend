"""Tests dédiés de `_verifier_acces_campagne` (schema/campagne_queries.py).

Cette fonction est le seul verrou qui empêche un SUPERVISEUR de voir/gérer les
campagnes d'un autre superviseur, et un AGENT d'accéder à une campagne où il
n'est pas affecté (voir CLAUDE.md racine, §"Rôles et permissions" — filtrage
par `campagne.created_by`). Elle est appelée par une dizaine de resolvers
(`campagne_queries.py`, `campagne_mutations.py`, `subscriptions.py`), mais
aucun test existant ne l'exerçait directement dans ses branches SUPERVISEUR/
AGENT refusées : tous les tests de resolvers qui la traversent le font avec
`role="ADMIN"` (no-op) ou en la patchant elle-même. Un projet déjà mordu une
fois par une régression de contrôle de rôle sur `/impayes` (voir CLAUDE.md
racine) justifie un test isolé et exhaustif de ce verrou précis.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.campagne_queries import _verifier_acces_campagne, _verifier_propriete_superviseur


def _user(role: str, user_id: str = "user-001") -> MagicMock:
    return MagicMock(role=role, user_id=user_id)


class VerifierAccesCampagneAdminTests(SimpleTestCase):
    @patch("schema.campagne_queries.campagne_client")
    def test_admin_acces_libre_sans_appel_grpc(self, mock_client: MagicMock) -> None:
        """ADMIN : no-op complet — ni GetCampagne ni ListCampagnes ne doivent
        être appelés (accès déjà tranché par le rôle, rien à vérifier de plus)."""
        _verifier_acces_campagne(_user("ADMIN"), "camp-001")
        mock_client.get_campagne.assert_not_called()
        mock_client.list_campagnes.assert_not_called()


class VerifierAccesCampagneSuperviseurTests(SimpleTestCase):
    @patch("schema.campagne_queries.campagne_client")
    def test_superviseur_proprietaire_autorise(self, mock_client: MagicMock) -> None:
        mock_client.get_campagne.return_value = MagicMock(created_by="sup-001")
        # Ne doit pas lever.
        _verifier_acces_campagne(_user("SUPERVISEUR", user_id="sup-001"), "camp-001")
        mock_client.get_campagne.assert_called_once_with("camp-001")

    @patch("schema.campagne_queries.campagne_client")
    def test_superviseur_non_proprietaire_refuse(self, mock_client: MagicMock) -> None:
        """Cas exact de la règle métier : la campagne appartient à un AUTRE
        superviseur — doit être refusé, jamais dégradé en accès silencieux."""
        mock_client.get_campagne.return_value = MagicMock(created_by="sup-AUTRE")
        with self.assertRaises(PermissionError):
            _verifier_acces_campagne(_user("SUPERVISEUR", user_id="sup-001"), "camp-001")

    @patch("schema.campagne_queries.campagne_client")
    def test_superviseur_ne_consulte_jamais_list_campagnes(self, mock_client: MagicMock) -> None:
        """La vérification SUPERVISEUR passe par GetCampagne (created_by),
        jamais par ListCampagnes (chemin réservé à AGENT) — une confusion
        entre les deux romprait le filtrage par propriétaire."""
        mock_client.get_campagne.return_value = MagicMock(created_by="sup-001")
        _verifier_acces_campagne(_user("SUPERVISEUR", user_id="sup-001"), "camp-001")
        mock_client.list_campagnes.assert_not_called()


class VerifierAccesCampagneAgentTests(SimpleTestCase):
    @patch("schema.campagne_queries.campagne_client")
    def test_agent_affecte_autorise(self, mock_client: MagicMock) -> None:
        mock_client.list_campagnes.return_value = MagicMock(
            campagnes=[MagicMock(campagne_id="camp-001"), MagicMock(campagne_id="camp-002")]
        )
        # Ne doit pas lever.
        _verifier_acces_campagne(_user("AGENT", user_id="agent-001"), "camp-001")
        mock_client.list_campagnes.assert_called_once_with(agent_id="agent-001")

    @patch("schema.campagne_queries.campagne_client")
    def test_agent_non_affecte_refuse(self, mock_client: MagicMock) -> None:
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="camp-AUTRE")])
        with self.assertRaises(PermissionError):
            _verifier_acces_campagne(_user("AGENT", user_id="agent-001"), "camp-001")

    @patch("schema.campagne_queries.campagne_client")
    def test_agent_sans_aucune_affectation_refuse(self, mock_client: MagicMock) -> None:
        """Liste vide (agent nouvellement créé, pas encore affecté) : refusé,
        pas d'exception de type différent (ex. IndexError) qui masquerait le
        vrai refus derrière une erreur 500 générique côté GraphQL."""
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[])
        with self.assertRaises(PermissionError):
            _verifier_acces_campagne(_user("AGENT", user_id="agent-001"), "camp-001")

    @patch("schema.campagne_queries.campagne_client")
    def test_agent_ne_consulte_jamais_get_campagne(self, mock_client: MagicMock) -> None:
        """Chemin AGENT et chemin SUPERVISEUR ne doivent jamais se mélanger :
        un AGENT ne doit jamais déclencher un GetCampagne (réservé au
        SUPERVISEUR)."""
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="camp-001")])
        _verifier_acces_campagne(_user("AGENT", user_id="agent-001"), "camp-001")
        mock_client.get_campagne.assert_not_called()


class VerifierAccesCampagneRoleInattenduTests(SimpleTestCase):
    @patch("schema.campagne_queries.campagne_client")
    def test_role_ni_superviseur_ni_agent_ne_declenche_aucune_verification(self, mock_client: MagicMock) -> None:
        """Défensif : un rôle qui n'est ni SUPERVISEUR ni AGENT (ex. COMPTABLE
        — n'atteint normalement jamais cette fonction, `require_role` filtre
        en amont) ne doit provoquer ni exception ni appel gRPC, seulement
        traverser sans effet."""
        _verifier_acces_campagne(_user("COMPTABLE"), "camp-001")
        mock_client.get_campagne.assert_not_called()
        mock_client.list_campagnes.assert_not_called()

    @patch("schema.campagne_queries.campagne_client")
    def test_role_absent_ne_declenche_aucune_verification(self, mock_client: MagicMock) -> None:
        _verifier_acces_campagne(MagicMock(role=None, user_id="user-001"), "camp-001")
        mock_client.get_campagne.assert_not_called()
        mock_client.list_campagnes.assert_not_called()


class AliasCampagneMutationsTests(SimpleTestCase):
    """`campagne_mutations.py` importe cette même fonction sous l'alias
    `_verifier_propriete_superviseur` (voir campagne_queries.py) — vérifie que
    l'alias pointe bien vers l'identique fonction, pas une redéfinition
    divergente qui aurait pu dériver silencieusement avec le temps."""

    def test_alias_est_bien_le_meme_objet_fonction(self) -> None:
        self.assertIs(_verifier_propriete_superviseur, _verifier_acces_campagne)
