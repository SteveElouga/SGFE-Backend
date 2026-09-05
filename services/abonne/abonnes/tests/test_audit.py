"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Réplique le patron déjà testé côté Paiement Service
(`services/paiement/paiements/tests/test_audit.py`) : vérifie que chaque
mutation métier ciblée écrit bien une entrée d'audit, avec l'acteur lu depuis
`get_caller()` — et que cette écriture participe à la même transaction que le
changement métier (elle est annulée avec lui en cas d'échec).

L'anonymisation RGPD (`anonymiser_abonne`) n'est délibérément PAS auditée ici
(voir `AbonneService.anonymiser_abonne` et AUDIT_SGFE.md §J) : seules les
mutations métier normales le sont.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from abonnes.audit import enregistrer_audit
from abonnes.grpc_interceptors import CallerIdentity, caller_identity
from abonnes.models import Abonne, AuditLog
from abonnes.services import AbonneService, CompteurService, ValidationError

_DEFAULTS = dict(
    nom="Doe",
    prenom="John",
    telephone_whatsapp="+24100000000",
    adresse="Quartier X",
    numero_compteur=1,
    quartier="Centre",
    camp=1,
    index_initial=0,
    date_pose="2024-01-01",
)


def _create_abonne(service: AbonneService, **overrides: object) -> Abonne:
    """Crée un abonné de test avec des valeurs par défaut raisonnables."""
    params = dict(_DEFAULTS)
    params.update(overrides)
    return service.create_abonne(**params)  # type: ignore[arg-type]


class EnregistrerAuditTests(TestCase):
    """Tests unitaires directs de `enregistrer_audit`."""

    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="alice", role="ADMIN"))
        try:
            enregistrer_audit(action="TEST", objet_type="Abonne", objet_id="a-1", detail="détail libre")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, "a-1")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "alice")
        self.assertEqual(entree.acteur_role, "ADMIN")
        self.assertEqual(entree.detail, "détail libre")
        self.assertIsNotNone(entree.horodatage)

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        # Pas d'identité propagée (appel de test sans métadonnées, tâche de
        # fond...) : l'audit ne doit jamais faire échouer la mutation qu'il
        # documente.
        enregistrer_audit(action="TEST_ANONYME", objet_type="Abonne", objet_id="a-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")
        self.assertEqual(entree.acteur_nom, "")
        self.assertEqual(entree.acteur_role, "")


class AbonneServiceAuditTests(TestCase):
    """Vérifie que les mutations d'`AbonneService` écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.svc = AbonneService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="agent1", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_create_abonne_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)

        entree = AuditLog.objects.get(action="ABONNE_CREE")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, str(abonne.id))
        self.assertEqual(entree.acteur_id, "u-42")
        self.assertEqual(entree.acteur_nom, "agent1")
        self.assertIn(abonne.numero_abonne, entree.detail)
        # Aucune PII (nom/prénom/téléphone/adresse) dans le détail libre.
        self.assertNotIn("Doe", entree.detail)
        self.assertNotIn("John", entree.detail)
        self.assertNotIn("+24100000000", entree.detail)

    def test_update_abonne_ecrit_une_entree_d_audit_avec_les_champs_modifies(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()  # ne garder que la modification

        self.svc.update_abonne(str(abonne.id), nom="", prenom="", telephone_whatsapp="+24199999999", adresse="")

        entree = AuditLog.objects.get(action="ABONNE_MODIFIE")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, str(abonne.id))
        self.assertIn("telephone_whatsapp", entree.detail)
        self.assertNotIn("nom", entree.detail)  # nom non fourni (chaîne vide) : pas dans les champs modifiés
        self.assertNotIn("+24199999999", entree.detail)  # jamais la valeur, PII

    def test_update_abonne_liste_tous_les_champs_fournis(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()

        self.svc.update_abonne(
            str(abonne.id), nom="Martin", prenom="Alice", telephone_whatsapp="", adresse="Nouvelle adresse"
        )

        entree = AuditLog.objects.get(action="ABONNE_MODIFIE")
        for champ in ("nom", "prenom", "adresse"):
            self.assertIn(champ, entree.detail)
        self.assertNotIn("telephone_whatsapp", entree.detail)  # non fourni
        self.assertNotIn("Martin", entree.detail)  # jamais la valeur, PII

    def test_update_abonne_sans_champ_fourni_journalise_aucun(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()

        self.svc.update_abonne(str(abonne.id), nom="", prenom="", telephone_whatsapp="", adresse="")

        entree = AuditLog.objects.get(action="ABONNE_MODIFIE")
        self.assertIn("aucun", entree.detail)

    def test_suspendre_abonne_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()

        self.svc.suspendre_abonne(str(abonne.id))

        entree = AuditLog.objects.get(action="ABONNE_SUSPENDU")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, str(abonne.id))
        self.assertIn(abonne.numero_abonne, entree.detail)
        self.assertEqual(entree.acteur_id, "u-42")

    def test_suspendre_abonne_deja_suspendu_ne_re_ecrit_pas_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        self.svc.suspendre_abonne(str(abonne.id))
        nb_avant = AuditLog.objects.filter(action="ABONNE_SUSPENDU").count()

        with self.assertRaises(ValidationError):
            self.svc.suspendre_abonne(str(abonne.id))

        self.assertEqual(AuditLog.objects.filter(action="ABONNE_SUSPENDU").count(), nb_avant)

    def test_reactiver_abonne_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        self.svc.suspendre_abonne(str(abonne.id))
        AuditLog.objects.all().delete()

        self.svc.reactiver_abonne(str(abonne.id))

        entree = AuditLog.objects.get(action="ABONNE_REACTIVE")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, str(abonne.id))

    def test_reactiver_abonne_non_suspendu_ne_re_ecrit_pas_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()

        with self.assertRaises(ValidationError):
            self.svc.reactiver_abonne(str(abonne.id))

        self.assertEqual(AuditLog.objects.filter(action="ABONNE_REACTIVE").count(), 0)

    def test_resilier_abonne_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        AuditLog.objects.all().delete()

        self.svc.resilier_abonne(str(abonne.id))

        entree = AuditLog.objects.get(action="ABONNE_RESILIE")
        self.assertEqual(entree.objet_type, "Abonne")
        self.assertEqual(entree.objet_id, str(abonne.id))

    def test_resilier_abonne_deja_resilie_ne_re_ecrit_pas_d_audit(self) -> None:
        abonne = _create_abonne(self.svc)
        self.svc.resilier_abonne(str(abonne.id))
        nb_avant = AuditLog.objects.filter(action="ABONNE_RESILIE").count()

        with self.assertRaises(ValidationError):
            self.svc.resilier_abonne(str(abonne.id))

        self.assertEqual(AuditLog.objects.filter(action="ABONNE_RESILIE").count(), nb_avant)

    def test_anonymiser_abonne_n_ecrit_aucune_entree_d_audit(self) -> None:
        """L'anonymisation RGPD (PR #179) n'est pas un événement du journal
        d'audit métier — voir AbonneService.anonymiser_abonne et
        AUDIT_SGFE.md §J. Pas de régression : elle continue de fonctionner
        sans écrire dans `audit_log`."""
        abonne = _create_abonne(self.svc)
        self.svc.resilier_abonne(str(abonne.id))
        AuditLog.objects.all().delete()

        self.svc.anonymiser_abonne(str(abonne.id))

        self.assertEqual(AuditLog.objects.count(), 0)


class CompteurServiceAuditTests(TestCase):
    """Vérifie que les mutations de `CompteurService` écrivent l'audit attendu."""

    def setUp(self) -> None:
        self.abonne_svc = AbonneService()
        self.compteur_svc = CompteurService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-42", username="agent1", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_update_compteur_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.abonne_svc)
        AuditLog.objects.all().delete()

        compteur = self.compteur_svc.update_compteur(
            abonne_id=str(abonne.id), quartier="Nouveau", camp=None, index_initial=None, date_pose=None
        )

        entree = AuditLog.objects.get(action="COMPTEUR_MODIFIE")
        self.assertEqual(entree.objet_type, "Compteur")
        self.assertEqual(entree.objet_id, str(compteur.id))
        self.assertIn(str(abonne.id), entree.detail)
        self.assertIn("quartier", entree.detail)
        self.assertNotIn("camp", entree.detail)  # non fourni

    def test_update_compteur_liste_tous_les_champs_fournis(self) -> None:
        abonne = _create_abonne(self.abonne_svc)
        AuditLog.objects.all().delete()

        self.compteur_svc.update_compteur(
            abonne_id=str(abonne.id),
            quartier="Nouveau",
            camp=2,
            index_initial=42,
            date_pose="2024-02-02",
            position="Près du portail",
        )

        entree = AuditLog.objects.get(action="COMPTEUR_MODIFIE")
        for champ in ("quartier", "camp", "index_initial", "date_pose", "position"):
            self.assertIn(champ, entree.detail)

    def test_remplacer_compteur_ecrit_une_entree_d_audit(self) -> None:
        abonne = _create_abonne(self.abonne_svc, index_initial=0)
        AuditLog.objects.all().delete()

        nouveau = self.compteur_svc.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Nouveau Quartier",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
            motif="Compteur défectueux",
        )

        entree = AuditLog.objects.get(action="COMPTEUR_REMPLACE")
        self.assertEqual(entree.objet_type, "Compteur")
        self.assertEqual(entree.objet_id, str(nouveau.id))
        self.assertIn(str(abonne.id), entree.detail)
        self.assertIn("Compteur défectueux", entree.detail)

    def test_remplacer_compteur_index_invalide_n_ecrit_aucun_audit(self) -> None:
        abonne = _create_abonne(self.abonne_svc, index_initial=50)

        with self.assertRaises(ValidationError):
            self.compteur_svc.remplacer_compteur(
                abonne_id=str(abonne.id),
                index_fermeture=10,
                nouveau_numero_compteur=2,
                nouveau_quartier="Q",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            )

        self.assertEqual(AuditLog.objects.filter(action="COMPTEUR_REMPLACE").count(), 0)


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    """Le journal d'audit ne doit contenir aucune ligne orpheline : une
    mutation qui échoue en cours de transaction ne doit rien y laisser."""

    def setUp(self) -> None:
        self.abonne_svc = AbonneService()

    def test_creation_compteur_en_echec_n_ecrit_aucun_audit(self) -> None:
        """Régression ANO-017/atomicité : la collision de `numero_compteur`
        fait échouer la création du Compteur — l'Abonné ET l'entrée d'audit
        doivent être défaits ensemble."""
        _create_abonne(self.abonne_svc)

        with self.assertRaises(IntegrityError):
            _create_abonne(self.abonne_svc, numero_compteur=1)

        self.assertEqual(Abonne.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="ABONNE_CREE").count(), 1)

    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        """Une exception levée APRÈS l'écriture d'audit, mais dans la même
        transaction, doit défaire les deux ensemble (rollback atomique)."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="Abonne", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
