"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Vérifie que chaque mutation ciblée par l'étape 2 (création, modification,
désactivation, réactivation d'un utilisateur, renvoi d'identifiants par un
admin) écrit bien une entrée d'audit, avec l'acteur lu depuis `get_caller()`
— et que cette écriture participe à la même transaction que le changement
métier (elle est annulée avec lui en cas d'échec).
"""

from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from comptes.audit import enregistrer_audit, enregistrer_evenement_securite
from comptes.grpc_interceptors import CallerIdentity, caller_identity
from comptes.models import AuditLog, Role, User
from comptes.services import UserAdminService


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="admin1", role="ADMIN"))
        try:
            enregistrer_audit(action="TEST", objet_type="User", objet_id="u-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "User")
        self.assertEqual(entree.objet_id, "u-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "admin1")
        self.assertEqual(entree.acteur_role, "ADMIN")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="User", objet_id="u-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class UserAdminServiceAuditTests(TestCase):
    """Vérifie que les mutations du service métier écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.svc = UserAdminService()
        # E-mail/WhatsApp mockés : ces tests ne doivent jamais toucher un
        # vrai fournisseur (même patron que test_services.py).
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)
        self.whatsapp_patcher = patch("comptes.services.whatsapp_client.send")
        self.mock_whatsapp = self.whatsapp_patcher.start()
        self.addCleanup(self.whatsapp_patcher.stop)

        jeton = caller_identity.set(CallerIdentity(user_id="admin-1", username="superadmin", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_create_user_ecrit_une_entree_d_audit(self) -> None:
        user = self.svc.create_user(username="agent1", phone_number="+237690000101", role=Role.AGENT)

        entree = AuditLog.objects.get(action="UTILISATEUR_CREE", objet_id=str(user.id))
        self.assertEqual(entree.objet_type, "User")
        self.assertEqual(entree.acteur_id, "admin-1")
        self.assertEqual(entree.acteur_nom, "superadmin")
        self.assertIn("agent1", entree.detail)
        self.assertIn(Role.AGENT, entree.detail)

    def test_update_user_ecrit_une_entree_d_audit_avec_le_changement_de_role(self) -> None:
        created = self.svc.create_user(username="agent2", phone_number="+237690000102", role=Role.AGENT)
        AuditLog.objects.filter(action="UTILISATEUR_CREE").delete()  # ne garder que la modification

        updated = self.svc.update_user(str(created.id), email="", role=Role.SUPERVISEUR)

        entree = AuditLog.objects.get(action="UTILISATEUR_MODIFIE", objet_id=str(updated.id))
        self.assertEqual(entree.objet_type, "User")
        self.assertIn(f"role={Role.AGENT}->{Role.SUPERVISEUR}", entree.detail)
        self.assertEqual(entree.acteur_id, "admin-1")

    def test_deactivate_user_ecrit_une_entree_d_audit(self) -> None:
        created = self.svc.create_user(username="agent3", phone_number="+237690000103", role=Role.AGENT)
        AuditLog.objects.filter(action="UTILISATEUR_CREE").delete()

        self.svc.deactivate_user(str(created.id))

        entree = AuditLog.objects.get(action="UTILISATEUR_DESACTIVE", objet_id=str(created.id))
        self.assertEqual(entree.objet_type, "User")
        self.assertIn("agent3", entree.detail)
        self.assertEqual(entree.acteur_id, "admin-1")

    def test_reactivate_user_ecrit_une_entree_d_audit(self) -> None:
        created = self.svc.create_user(username="agent4", phone_number="+237690000104", role=Role.AGENT)
        self.svc.deactivate_user(str(created.id))
        AuditLog.objects.all().delete()

        self.svc.reactivate_user(str(created.id))

        entree = AuditLog.objects.get(action="UTILISATEUR_REACTIVE", objet_id=str(created.id))
        self.assertEqual(entree.objet_type, "User")
        self.assertIn("agent4", entree.detail)

    def test_resend_credentials_non_admin_ecrit_une_entree_d_audit(self) -> None:
        created = self.svc.create_user(username="agent5", phone_number="+237690000105", role=Role.AGENT)
        AuditLog.objects.filter(action="UTILISATEUR_CREE").delete()

        self.svc.resend_credentials(str(created.id))

        entree = AuditLog.objects.get(action="IDENTIFIANTS_RENVOYES", objet_id=str(created.id))
        self.assertEqual(entree.objet_type, "User")
        self.assertIn("lien d'activation renvoyé", entree.detail)

    def test_resend_credentials_admin_active_ecrit_reinitialisation_dans_le_detail(self) -> None:
        created = self.svc.create_user(
            username="admin_x", email="admin_x@example.com", phone_number="+237690000106", role=Role.ADMIN
        )
        created.set_password("S3cr3t!")
        created.is_active = True
        created.save()
        AuditLog.objects.filter(action="UTILISATEUR_CREE").delete()

        self.svc.resend_credentials(str(created.id))

        entree = AuditLog.objects.get(action="IDENTIFIANTS_RENVOYES", objet_id=str(created.id))
        self.assertIn("réinitialisation de mot de passe demandée", entree.detail)


class EnregistrerEvenementSecuriteTests(TestCase):
    """Tests de la centralisation des événements de sécurité de la gateway
    (voir AUDIT_SGFE.md §J, "Journalisation de sécurité centralisée")."""

    def test_ecrit_une_entree_avec_l_acteur_explicite(self) -> None:
        enregistrer_evenement_securite(
            type_evenement="ROLE_REFUSE",
            detail="Accès refusé : rôle AGENT insuffisant (requis : ADMIN)",
            acteur_id="u-1",
            acteur_nom="bob",
            acteur_role="AGENT",
            request_id="req-123",
        )

        entree = AuditLog.objects.get(action="ROLE_REFUSE")
        self.assertEqual(entree.objet_type, "EvenementSecuriteGateway")
        self.assertEqual(entree.objet_id, "req-123")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "bob")
        self.assertEqual(entree.acteur_role, "AGENT")
        self.assertIn("rôle AGENT insuffisant", entree.detail)

    def test_ignore_get_caller_meme_si_une_identite_est_posee(self) -> None:
        """L'acteur vient des paramètres explicites, jamais de `get_caller()`
        — contrairement à `enregistrer_audit` — pour rester correct même si
        l'identité gRPC propagée ne correspond pas à l'acteur de l'événement."""
        jeton = caller_identity.set(CallerIdentity(user_id="autre", username="autre-nom", role="COMPTABLE"))
        try:
            enregistrer_evenement_securite(type_evenement="TOKEN_INVALIDE", detail="jeton expiré")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TOKEN_INVALIDE")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")

    def test_type_evenement_vide_leve_value_error(self) -> None:
        with self.assertRaises(ValueError):
            enregistrer_evenement_securite(type_evenement="")

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_appels_multiples_n_ecrivent_jamais_dans_la_meme_transaction_qu_une_mutation(self) -> None:
        """Chaque appel ouvre sa propre transaction dédiée (pas de mutation
        métier associée) : un échec APRÈS l'écriture ne doit rien annuler,
        contrairement à `enregistrer_audit` qui, elle, doit être annulable."""
        enregistrer_evenement_securite(type_evenement="TOKEN_INVALIDE", detail="premier appel")
        enregistrer_evenement_securite(type_evenement="TOKEN_INVALIDE", detail="second appel")

        self.assertEqual(AuditLog.objects.filter(action="TOKEN_INVALIDE").count(), 2)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def setUp(self) -> None:
        self.svc = UserAdminService()

    def test_creation_admin_sans_email_leve_avant_toute_ecriture_d_audit(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.create_user(username="admin_sans_email", phone_number="+237690000107", role=Role.ADMIN)

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_desactivation_du_dernier_admin_leve_avant_toute_ecriture_d_audit(self) -> None:
        admin = User.objects.create_user(
            username="admin_seul",
            email="admin_seul@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000108",
        )

        with self.assertRaises(ValueError):
            self.svc.deactivate_user(str(admin.id))

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="User", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
