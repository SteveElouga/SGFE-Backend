"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Vérifie que chaque mutation ciblée par l'étape 2 (enregistrement/annulation
d'un paiement, avoir, annulation de solde) écrit bien une entrée d'audit,
avec l'acteur lu depuis `get_caller()` — et que cette écriture participe à la
même transaction que le changement métier (elle est annulée avec lui en cas
d'échec).
"""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from paiements.audit import enregistrer_audit
from paiements.grpc_interceptors import CallerIdentity, caller_identity
from paiements.models import AuditLog, ModePaiement
from paiements.services import PaiementService

ABONNE = "abonne-test"


def _hier(jours: int = 30) -> date:
    return date.today() - timedelta(days=jours)


def _demain(jours: int = 5) -> date:
    return date.today() + timedelta(days=jours)


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="alice", role="COMPTABLE"))
        try:
            enregistrer_audit(action="TEST", objet_type="Paiement", objet_id="p-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "Paiement")
        self.assertEqual(entree.objet_id, "p-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "alice")
        self.assertEqual(entree.acteur_role, "COMPTABLE")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="Paiement", objet_id="p-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class PaiementServiceAuditTests(TestCase):
    """Vérifie que les mutations du service métier écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="caissier1", role="COMPTABLE"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_enregistrer_paiement_ecrit_une_entree_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())

        paiement, _ = self.svc.enregistrer_paiement(
            "facture-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier1"
        )

        entree = AuditLog.objects.get(action="PAIEMENT_ENREGISTRE", objet_id=str(paiement.versement_id))
        self.assertEqual(entree.objet_type, "Paiement")
        self.assertEqual(entree.acteur_id, "u-42")
        self.assertEqual(entree.acteur_nom, "caissier1")
        self.assertIn("facture-1", entree.detail)

    def test_enregistrer_paiement_idempotent_n_ecrit_pas_une_seconde_entree(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())
        self.svc.enregistrer_paiement(
            "facture-1", ABONNE, 5000, date.today(), ModePaiement.MOBILE_MONEY, "ref-momo-1", "caissier1"
        )
        self.assertEqual(AuditLog.objects.filter(action="PAIEMENT_ENREGISTRE").count(), 1)

        # Rejeu réseau avec la même référence : renvoie l'existant sans re-créditer.
        self.svc.enregistrer_paiement(
            "facture-1", ABONNE, 5000, date.today(), ModePaiement.MOBILE_MONEY, "ref-momo-1", "caissier1"
        )
        self.assertEqual(AuditLog.objects.filter(action="PAIEMENT_ENREGISTRE").count(), 1)

    def test_enregistrer_paiement_abonne_ecrit_une_entree_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())

        self.svc.enregistrer_paiement_abonne(ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier1")

        entree = AuditLog.objects.get(action="PAIEMENT_ENREGISTRE")
        self.assertIn(ABONNE, entree.detail)

    def test_annuler_paiement_ecrit_une_entree_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())
        paiement, _ = self.svc.enregistrer_paiement(
            "facture-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier1"
        )
        AuditLog.objects.all().delete()  # ne garder que l'annulation dans cette assertion

        self.svc.annuler_paiement(str(paiement.id), motif="erreur de saisie", annule_par="u-42")

        entree = AuditLog.objects.get(action="PAIEMENT_ANNULE")
        self.assertEqual(entree.objet_type, "Paiement")
        self.assertIn("erreur de saisie", entree.detail)
        self.assertEqual(entree.acteur_id, "u-42")

    def test_annuler_paiement_deja_annule_ne_re_ecrit_pas_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())
        paiement, _ = self.svc.enregistrer_paiement(
            "facture-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier1"
        )
        self.svc.annuler_paiement(str(paiement.id), motif="erreur", annule_par="u-42")
        nb_avant = AuditLog.objects.filter(action="PAIEMENT_ANNULE").count()

        with self.assertRaises(ValidationError):
            self.svc.annuler_paiement(str(paiement.id), motif="deuxième tentative", annule_par="u-42")

        self.assertEqual(AuditLog.objects.filter(action="PAIEMENT_ANNULE").count(), nb_avant)

    def test_crediter_avoir_manuel_ecrit_une_entree_d_audit(self) -> None:
        self.svc.crediter_avoir_manuel(ABONNE, 1000, "geste commercial", "u-42")

        entree = AuditLog.objects.get(action="AVOIR_CREDITE")
        self.assertEqual(entree.objet_type, "AvoirAbonne")
        self.assertEqual(entree.objet_id, ABONNE)
        self.assertIn("geste commercial", entree.detail)

    def test_annuler_solde_ecrit_une_entree_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())

        self.svc.annuler_solde("facture-1", motif="index erroné")

        entree = AuditLog.objects.get(action="SOLDE_FACTURE_ANNULE")
        self.assertEqual(entree.objet_type, "SoldeFacture")
        self.assertEqual(entree.objet_id, "facture-1")
        self.assertIn("index erroné", entree.detail)

    def test_annuler_solde_idempotent_ne_re_ecrit_pas_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())
        self.svc.annuler_solde("facture-1", motif="index erroné")
        nb_avant = AuditLog.objects.filter(action="SOLDE_FACTURE_ANNULE").count()

        # Réannuler un solde déjà ANNULEE est un no-op métier (voir services.py).
        self.svc.annuler_solde("facture-1", motif="nouvelle tentative")

        self.assertEqual(AuditLog.objects.filter(action="SOLDE_FACTURE_ANNULE").count(), nb_avant)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_montant_invalide_leve_avant_toute_ecriture_d_audit(self) -> None:
        self.svc.initialiser_solde("facture-1", ABONNE, 5000, _demain())

        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                "facture-1", ABONNE, -100, date.today(), ModePaiement.ESPECES, "", "caissier1"
            )

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="Paiement", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
