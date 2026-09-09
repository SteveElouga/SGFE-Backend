"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Contrairement aux 6 autres services, le Notification Service n'a PAS un
`AuditLog` généralisé à toutes ses mutations : seules les 3 RPC identifiées
comme des mutations sensibles à part entière sont couvertes ici —
`CreerDiffusion` (`DiffusionService.creer_diffusion`), `RevoquerToken` et
`RevoquerTousTokens` (`TokenService.revoquer_token`/`revoquer_tous_tokens`).
Voir `notifications/models.py::AuditLog` pour le détail de cette exception
assumée.

Vérifie que chacune de ces 3 mutations écrit bien une entrée d'audit, avec
l'acteur lu depuis `get_caller()` — et que cette écriture participe à la même
transaction que le changement métier (elle est annulée avec lui en cas
d'échec). Les tests d'intégration au niveau RPC (`NotificationServiceServicer`
directement) vivent dans `test_diffusion_revocation_integration.py`, déjà
dédié à ces 3 RPC.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.test import TestCase

from notifications.audit import enregistrer_audit
from notifications.grpc_interceptors import CallerIdentity, caller_identity
from notifications.models import AuditLog, TokenAcces
from notifications.services import DiffusionService, TokenService


def _abonne_mock(abonne_id: str, telephone: str) -> MagicMock:
    mock = MagicMock()
    mock.abonne_id = abonne_id
    mock.telephone_whatsapp = telephone
    return mock


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="alice", role="ADMIN"))
        try:
            enregistrer_audit(action="TEST", objet_type="Diffusion", objet_id="d-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "Diffusion")
        self.assertEqual(entree.objet_id, "d-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "alice")
        self.assertEqual(entree.acteur_role, "ADMIN")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="Diffusion", objet_id="d-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class TokenServiceAuditTests(TestCase):
    """Vérifie que `TokenService.revoquer_token`/`revoquer_tous_tokens`
    écrivent l'audit attendu, sans jamais y faire figurer de PII (numéro de
    téléphone de l'abonné concerné)."""

    def setUp(self) -> None:
        self.svc = TokenService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="admin1", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def _creer_token(self, abonne_id: str | None = None) -> TokenAcces:
        return TokenAcces.objects.create(
            abonne_id=abonne_id or str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )

    def test_revoquer_token_ecrit_une_entree_d_audit(self) -> None:
        token = self._creer_token()

        self.svc.revoquer_token(str(token.id))

        entree = AuditLog.objects.get(action="TOKEN_REVOQUE", objet_id=str(token.id))
        self.assertEqual(entree.objet_type, "TokenAcces")
        self.assertEqual(entree.acteur_id, "u-42")
        self.assertEqual(entree.acteur_nom, "admin1")
        self.assertIn(str(token.id), entree.detail)
        # Jamais l'abonné ni un numéro de téléphone dans le détail (PII).
        self.assertNotIn(token.abonne_id, entree.detail)

    def test_revoquer_token_inexistant_leve_et_n_ecrit_pas_d_audit(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.revoquer_token(str(uuid.uuid4()))
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_revoquer_tous_tokens_ecrit_une_entree_d_audit_avec_le_compte(self) -> None:
        self._creer_token()
        self._creer_token()
        self._creer_token()

        count = self.svc.revoquer_tous_tokens()

        entree = AuditLog.objects.get(action="TOUS_TOKENS_REVOQUES")
        self.assertEqual(entree.objet_type, "TokenAcces")
        self.assertEqual(entree.objet_id, "*")
        self.assertIn(str(count), entree.detail)
        self.assertEqual(count, 3)

    def test_revoquer_tous_tokens_sans_token_actif_ecrit_quand_meme_une_entree(self) -> None:
        count = self.svc.revoquer_tous_tokens()

        entree = AuditLog.objects.get(action="TOUS_TOKENS_REVOQUES")
        self.assertIn("0", entree.detail)
        self.assertEqual(count, 0)


class DiffusionServiceAuditTests(TestCase):
    """Vérifie que `DiffusionService.creer_diffusion` écrit l'audit attendu,
    avec seulement le NOMBRE de destinataires résolus — jamais leurs numéros
    de téléphone ni le contenu du message (PII)."""

    def setUp(self) -> None:
        self.svc = DiffusionService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-9", username="admin9", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    @patch("notifications.services.abonne_client")
    def test_creer_diffusion_ecrit_une_entree_d_audit(self, mock_abonne: MagicMock) -> None:
        aid1, aid2 = str(uuid.uuid4()), str(uuid.uuid4())
        telephones = {aid1: "+237699111111", aid2: "+237699222222"}
        mock_abonne.get_abonne.side_effect = lambda aid: _abonne_mock(aid, telephones[aid])

        diffusion = self.svc.creer_diffusion(
            message="Coupure d'eau prévue demain", abonne_ids=[aid1, aid2], created_by="u-9"
        )

        entree = AuditLog.objects.get(action="DIFFUSION_CREEE", objet_id=str(diffusion.id))
        self.assertEqual(entree.objet_type, "Diffusion")
        self.assertEqual(entree.acteur_id, "u-9")
        self.assertIn("2", entree.detail)
        # Jamais le contenu du message ni un numéro de téléphone (PII).
        self.assertNotIn("Coupure", entree.detail)
        self.assertNotIn("+237699111111", entree.detail)
        self.assertNotIn("+237699222222", entree.detail)

    @patch("notifications.services.abonne_client")
    def test_creer_diffusion_avec_degradation_partielle_compte_seulement_les_resolus(
        self, mock_abonne: MagicMock
    ) -> None:
        import grpc

        aid_ok, aid_ko = str(uuid.uuid4()), str(uuid.uuid4())

        def _get_abonne(aid: str) -> MagicMock:
            if aid == aid_ko:
                raise grpc.RpcError("Abonné Service injoignable")
            return _abonne_mock(aid, "+237699000000")

        mock_abonne.get_abonne.side_effect = _get_abonne

        diffusion = self.svc.creer_diffusion(message="Annonce", abonne_ids=[aid_ok, aid_ko], created_by="u-9")

        entree = AuditLog.objects.get(action="DIFFUSION_CREEE", objet_id=str(diffusion.id))
        self.assertIn("nb_destinataires=1", entree.detail)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="Diffusion", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)

    def test_token_inexistant_leve_avant_toute_ecriture_d_audit(self) -> None:
        """`revoquer_token` lève avant même d'atteindre `enregistrer_audit` —
        rien ne doit être écrit (déjà couvert au niveau service ci-dessus,
        fixé ici pour la garantie transactionnelle explicitement)."""
        with self.assertRaises(ObjectDoesNotExist):
            with transaction.atomic():
                TokenService().revoquer_token(str(uuid.uuid4()))
        self.assertEqual(AuditLog.objects.count(), 0)
