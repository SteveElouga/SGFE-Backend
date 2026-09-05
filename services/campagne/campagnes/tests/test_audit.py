"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7 et §J.

Vérifie que chaque mutation ciblée par l'étape 2 côté campagne (création,
démarrage, clôture, ajout d'abonné, affectation d'agent/zones, saisie et
correction d'index, marquage non-relevé) écrit bien une entrée d'audit, avec
l'acteur lu depuis `get_caller()` — et que cette écriture participe à la même
transaction que le changement métier (elle est annulée avec lui en cas
d'échec).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from campagnes.audit import enregistrer_audit
from campagnes.grpc_clients import AbonneServiceClient
from campagnes.grpc_interceptors import CallerIdentity, caller_identity
from campagnes.models import AuditLog, StatutCampagne, StatutReleve
from campagnes.repositories import CampagneRepository
from campagnes.services import CampagneService, ReleveService

# ajouter_abonne_campagne vérifie le statut ACTIF de l'abonné via un appel
# gRPC réel à Abonné Service (ANO-003) — patché pour tout le module, comme
# dans test_services.py, pour ne pas dépendre d'un Abonné Service démarré.
_abonne_patcher = patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="ACTIF"))


def setUpModule() -> None:
    _abonne_patcher.start()


def tearDownModule() -> None:
    _abonne_patcher.stop()


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="alice", role="SUPERVISEUR"))
        try:
            enregistrer_audit(action="TEST", objet_type="Campagne", objet_id="c-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "Campagne")
        self.assertEqual(entree.objet_id, "c-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "alice")
        self.assertEqual(entree.acteur_role, "SUPERVISEUR")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="Campagne", objet_id="c-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class CampagneServiceAuditTests(TestCase):
    """Vérifie que les mutations de `CampagneService` écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.svc = CampagneService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="superviseur1", role="SUPERVISEUR"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_creer_campagne_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="Campagne Test", periode_mois=6, periode_annee=2026, created_by="u-42")

        entree = AuditLog.objects.get(action="CAMPAGNE_CREEE", objet_id=str(campagne.id))
        self.assertEqual(entree.objet_type, "Campagne")
        self.assertEqual(entree.acteur_id, "u-42")
        self.assertEqual(entree.acteur_nom, "superviseur1")
        self.assertIn("Campagne Test", entree.detail)

    def test_creer_campagne_invalide_leve_avant_toute_ecriture_d_audit(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.creer_campagne(nom="   ", periode_mois=6, periode_annee=2026, created_by="u-42")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_demarrer_campagne_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()  # ne garder que le démarrage dans cette assertion

        self.svc.demarrer_campagne(str(campagne.id))

        entree = AuditLog.objects.get(action="CAMPAGNE_DEMARREE")
        self.assertEqual(entree.objet_type, "Campagne")
        self.assertEqual(entree.objet_id, str(campagne.id))
        self.assertEqual(entree.acteur_id, "u-42")

    def test_demarrer_campagne_deja_en_cours_ne_re_ecrit_pas_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        self.svc.demarrer_campagne(str(campagne.id))
        AuditLog.objects.all().delete()

        with self.assertRaises(ValidationError):
            self.svc.demarrer_campagne(str(campagne.id))

        self.assertEqual(AuditLog.objects.filter(action="CAMPAGNE_DEMARREE").count(), 0)

    def test_cloturer_campagne_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        self.svc.demarrer_campagne(str(campagne.id))
        AuditLog.objects.all().delete()

        self.svc.cloturer_campagne(str(campagne.id))

        entree = AuditLog.objects.get(action="CAMPAGNE_CLOTUREE")
        self.assertEqual(entree.objet_type, "Campagne")
        self.assertEqual(entree.objet_id, str(campagne.id))

    def test_cloturer_campagne_non_en_cours_ne_re_ecrit_pas_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()

        with self.assertRaises(ValidationError):
            self.svc.cloturer_campagne(str(campagne.id))

        self.assertEqual(AuditLog.objects.filter(action="CAMPAGNE_CLOTUREE").count(), 0)

    def test_ajouter_abonne_campagne_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()

        releve = self.svc.ajouter_abonne_campagne(str(campagne.id), "abonne-1", ancien_index=Decimal("10"))

        entree = AuditLog.objects.get(action="RELEVE_ABONNE_AJOUTE", objet_id=str(releve.id))
        self.assertEqual(entree.objet_type, "Releve")
        self.assertIn("abonne-1", entree.detail)
        self.assertIn(str(campagne.id), entree.detail)

    def test_assigner_agent_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()

        self.svc.assigner_agent(str(campagne.id), "agent-007")

        entree = AuditLog.objects.get(action="CAMPAGNE_AGENT_ASSIGNE")
        self.assertEqual(entree.objet_type, "Campagne")
        self.assertEqual(entree.objet_id, str(campagne.id))
        self.assertIn("agent-007", entree.detail)

    def test_assigner_agent_campagne_inexistante_n_ecrit_pas_d_audit(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.svc.assigner_agent("00000000-0000-0000-0000-000000000000", "agent-007")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_affecter_zones_ecrit_une_entree_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()

        self.svc.affecter_zones(str(campagne.id), "agent-007", [("Bastos", 1), ("Mvog-Ada", 2)])

        entree = AuditLog.objects.get(action="CAMPAGNE_ZONES_AFFECTEES")
        self.assertEqual(entree.objet_id, str(campagne.id))
        self.assertIn("agent-007", entree.detail)
        self.assertIn("2 zone", entree.detail)

    def test_affecter_zones_sans_agent_ne_re_ecrit_pas_d_audit(self) -> None:
        campagne = self.svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        AuditLog.objects.all().delete()

        with self.assertRaises(ValidationError):
            self.svc.affecter_zones(str(campagne.id), "", [("Bastos", 1)])

        self.assertEqual(AuditLog.objects.filter(action="CAMPAGNE_ZONES_AFFECTEES").count(), 0)


class ReleveServiceAuditTests(TestCase):
    """Vérifie que les mutations de `ReleveService` écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.campagne_svc = CampagneService()
        self.svc = ReleveService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-9", username="agent9", role="AGENT"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

        campagne = self.campagne_svc.creer_campagne(nom="C", periode_mois=6, periode_annee=2026, created_by="u-42")
        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        self.campagne = campagne
        self.releve = self.campagne_svc.ajouter_abonne_campagne(
            str(campagne.id), "abonne-1", ancien_index=Decimal("100")
        )
        AuditLog.objects.all().delete()  # ne garder que les mutations testées ici

    def test_saisir_index_ecrit_une_entree_d_audit(self) -> None:
        releve = self.svc.saisir_index(str(self.releve.id), nouveau_index=Decimal("150"), agent_id="u-9")

        entree = AuditLog.objects.get(action="RELEVE_INDEX_SAISI", objet_id=str(releve.id))
        self.assertEqual(entree.objet_type, "Releve")
        self.assertEqual(entree.acteur_id, "u-9")
        self.assertIn("150", entree.detail)

    def test_saisir_index_invalide_ne_re_ecrit_pas_d_audit(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(str(self.releve.id), nouveau_index=Decimal("50"), agent_id="u-9")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_corriger_releve_ecrit_une_entree_d_audit(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=Decimal("150"), agent_id="u-9")
        AuditLog.objects.filter(action="RELEVE_INDEX_SAISI").delete()

        releve = self.svc.corriger_releve(str(self.releve.id), nouveau_index=Decimal("180"), auteur_id="u-9")

        entree = AuditLog.objects.get(action="RELEVE_INDEX_CORRIGE", objet_id=str(releve.id))
        self.assertIn("150", entree.detail)
        self.assertIn("180", entree.detail)

    def test_marquer_non_releve_ecrit_une_entree_d_audit(self) -> None:
        releve = self.svc.marquer_non_releve(str(self.releve.id), statut=StatutReleve.NON_RELEVE)

        entree = AuditLog.objects.get(action="RELEVE_MARQUE", objet_id=str(releve.id))
        self.assertIn("NON_RELEVE", entree.detail)

    def test_marquer_non_releve_deja_releve_ne_re_ecrit_pas_d_audit(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=Decimal("150"), agent_id="u-9")
        AuditLog.objects.all().delete()

        with self.assertRaises(ValidationError):
            self.svc.marquer_non_releve(str(self.releve.id), statut=StatutReleve.NON_RELEVE)

        self.assertEqual(AuditLog.objects.filter(action="RELEVE_MARQUE").count(), 0)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="Campagne", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
