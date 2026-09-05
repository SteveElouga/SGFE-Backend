"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Vérifie que chaque mutation ciblée par l'étape 2 (mise à jour d'un paramètre
de configuration, mise à jour des infos société) écrit bien une entrée
d'audit, avec l'acteur lu depuis `get_caller()` — et que cette écriture
participe à la même transaction que le changement métier (elle est annulée
avec lui en cas d'échec).
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.test import TestCase

from parametres.audit import enregistrer_audit
from parametres.grpc_interceptors import CallerIdentity, caller_identity
from parametres.models import AuditLog
from parametres.services import ConfigService, InfosSocieteService


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="alice", role="ADMIN"))
        try:
            enregistrer_audit(action="TEST", objet_type="ConfigParam", objet_id="p-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "ConfigParam")
        self.assertEqual(entree.objet_id, "p-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "alice")
        self.assertEqual(entree.acteur_role, "ADMIN")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="ConfigParam", objet_id="p-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class ConfigServiceAuditTests(TestCase):
    """Vérifie que `ConfigService.update` écrit l'audit attendu."""

    def setUp(self) -> None:
        self.svc = ConfigService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="admin1", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_update_ecrit_une_entree_d_audit(self) -> None:
        self.svc.update("delai_paiement_jours", "10")

        entree = AuditLog.objects.get(action="CONFIG_PARAM_MODIFIE", objet_id="delai_paiement_jours")
        self.assertEqual(entree.objet_type, "ConfigParam")
        self.assertEqual(entree.acteur_id, "u-42")
        self.assertEqual(entree.acteur_nom, "admin1")
        self.assertIn("5", entree.detail)  # ancienne valeur par défaut
        self.assertIn("10", entree.detail)  # nouvelle valeur

    def test_update_inconnu_ne_produit_aucun_audit(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.update("CLE_INCONNUE", "42")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_update_repete_ecrit_une_entree_par_appel(self) -> None:
        self.svc.update("delai_paiement_jours", "7")
        self.svc.update("delai_paiement_jours", "9")
        self.assertEqual(AuditLog.objects.filter(action="CONFIG_PARAM_MODIFIE").count(), 2)


class InfosSocieteServiceAuditTests(TestCase):
    """Vérifie que `InfosSocieteService.update` écrit l'audit attendu."""

    def setUp(self) -> None:
        self.svc = InfosSocieteService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-7", username="admin2", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_update_ecrit_une_entree_d_audit(self) -> None:
        infos = self.svc.update(nom="Eau Pure SA", adresse="Yaoundé")

        entree = AuditLog.objects.get(action="INFOS_SOCIETE_MODIFIEES")
        self.assertEqual(entree.objet_type, "InfosSociete")
        self.assertEqual(entree.objet_id, str(infos.pk))
        self.assertEqual(entree.acteur_id, "u-7")
        self.assertIn("Eau Pure SA", entree.detail)
        self.assertIn("Yaoundé", entree.detail)

    def test_update_sans_champ_fourni_ecrit_quand_meme_une_entree(self) -> None:
        self.svc.update()
        entree = AuditLog.objects.get(action="INFOS_SOCIETE_MODIFIEES")
        self.assertEqual(entree.detail, "aucun champ fourni")

    def test_update_repete_ecrit_une_entree_par_appel(self) -> None:
        self.svc.update(nom="Première")
        self.svc.update(nom="Seconde")
        self.assertEqual(AuditLog.objects.filter(action="INFOS_SOCIETE_MODIFIEES").count(), 2)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def test_update_config_inconnu_leve_avant_toute_ecriture_d_audit(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            ConfigService().update("CLE_INCONNUE", "1")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="ConfigParam", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
