"""Tests unitaires directs de `_revalider_role_deactivate` (comptes/grpc_server.py).

Défense en profondeur (docs/CONFORMITE_SOC2_OWASP.md §3.1 A01) : ce filet ne
BLOQUE jamais l'appel — il journalise seulement un avertissement quand
l'identité propagée par la gateway porte un rôle qui n'aurait pas dû
atteindre `DeactivateUser` (voir CLAUDE.md racine : "Gérer les utilisateurs"
→ ADMIN uniquement).

`DeactivateUserRevalidationRoleTests` (test_grpc.py, `TestCase`) couvre déjà
ce comportement de bout en bout via le servicer complet (nécessite un vrai
`User` en base). Ce fichier complète avec des tests unitaires qui isolent la
fonction pure elle-même — aucun servicer, aucun modèle, aucune BD : seul le
`ContextVar` `caller_identity` est manipulé, ce qui permet de couvrir des
combinaisons de rôles supplémentaires sans le coût d'un fixture BD par cas.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from comptes.grpc_interceptors import CallerIdentity, caller_identity
from comptes.grpc_server import _revalider_role_deactivate


class RevaliderRoleDeactivateTests(SimpleTestCase):
    def _poser_identite(self, **kwargs: str) -> None:
        jeton = caller_identity.set(CallerIdentity(**kwargs))
        self.addCleanup(caller_identity.reset, jeton)

    @patch("comptes.grpc_server.logger")
    def test_role_admin_ne_journalise_aucun_avertissement(self, mock_logger: MagicMock) -> None:
        self._poser_identite(user_id="u-1", username="admin", role="ADMIN")
        _revalider_role_deactivate("DeactivateUser")
        mock_logger.warning.assert_not_called()

    def test_roles_non_autorises_journalisent_un_avertissement_explicite(self) -> None:
        """Paramétré (subTest) plutôt que dupliqué : même comportement attendu
        pour chaque rôle métier qui n'est pas ADMIN — AGENT, COMPTABLE,
        SUPERVISEUR ne doivent jamais déclencher `DeactivateUser` sans
        avertissement, quel que soit celui d'entre eux qui l'atteint."""
        for role_refuse in ("AGENT", "COMPTABLE", "SUPERVISEUR"):
            with self.subTest(role=role_refuse):
                self._poser_identite(user_id="u-1", username="bob", role=role_refuse)
                with self.assertLogs("comptes.grpc_server", level="WARNING") as journaux:
                    _revalider_role_deactivate("DeactivateUser")
                trace = "\n".join(journaux.output)
                self.assertIn("hors de l'ensemble autorisé", trace)
                self.assertIn(role_refuse, trace)
                self.assertIn("DeactivateUser", trace)
                caller_identity.set(CallerIdentity())  # reset avant l'itération suivante

    @patch("comptes.grpc_server.logger")
    def test_identite_anonyme_journalise_un_avertissement_distinct_sans_role(self, mock_logger: MagicMock) -> None:
        """Aucune identité propagée (appel hors gateway, ou service-à-service
        légitime) : avertissement différent, qui ne prétend pas connaître un
        rôle refusé — ne doit jamais se confondre avec le cas `role invalide`."""
        # Identité vide par défaut du ContextVar : ne rien poser explicitement.
        _revalider_role_deactivate("DeactivateUser")
        mock_logger.warning.assert_called_once()
        message = mock_logger.warning.call_args.args[0]
        self.assertIn("sans identité propagée", message)
        self.assertNotIn("hors de l'ensemble autorisé", message)

    def test_ne_leve_jamais_quel_que_soit_le_role(self) -> None:
        """Compromis assumé documenté sur la fonction : ce filet ne bloque
        jamais, y compris pour un rôle complètement inconnu du système."""
        for role in ("ADMIN", "AGENT", "COMPTABLE", "SUPERVISEUR", "ROLE_INEXISTANT", ""):
            with self.subTest(role=role):
                self._poser_identite(user_id="u-1" if role else "", username="x", role=role)
                try:
                    _revalider_role_deactivate("DeactivateUser")
                except Exception as exc:  # pragma: no cover - échec de test si levée
                    self.fail(f"_revalider_role_deactivate a levé {exc!r} pour role={role!r}")
                caller_identity.set(CallerIdentity())
