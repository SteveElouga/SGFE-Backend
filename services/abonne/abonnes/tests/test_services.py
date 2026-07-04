from django.db import IntegrityError
from django.test import TestCase

from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur
from abonnes.services import AbonneService, CompteurService, NumerotationService, ValidationError


def _create_abonne(service: AbonneService, **overrides):
    defaults = dict(
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
    defaults.update(overrides)
    return service.create_abonne(**defaults)


class NumerotationServiceTests(TestCase):
    def test_first_numero_is_ab_0001(self):
        self.assertEqual(NumerotationService().generer(), "AB-0001")

    def test_increments_sequentially(self):
        service = AbonneService()
        _create_abonne(service)
        self.assertEqual(NumerotationService().generer(), "AB-0002")
        _create_abonne(service, numero_compteur=2)
        self.assertEqual(NumerotationService().generer(), "AB-0003")


class AbonneServiceTests(TestCase):
    def setUp(self):
        self.service = AbonneService()

    def test_create_abonne_creates_compteur(self):
        abonne = _create_abonne(self.service)
        compteur = CompteurService().get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.statut, StatutCompteur.ACTIF)
        self.assertEqual(compteur.numero_compteur, 1)

    def test_create_abonne_assigns_sequential_numero(self):
        a1 = _create_abonne(self.service)
        a2 = _create_abonne(self.service, numero_compteur=2)
        self.assertEqual(a1.numero_abonne, "AB-0001")
        self.assertEqual(a2.numero_abonne, "AB-0002")

    def test_update_abonne_partial(self):
        abonne = _create_abonne(self.service)
        updated = self.service.update_abonne(
            str(abonne.id), nom="", prenom="", telephone_whatsapp="+24199999999", adresse=""
        )
        self.assertEqual(updated.nom, "Doe")
        self.assertEqual(updated.telephone_whatsapp, "+24199999999")

    def test_suspendre_then_reactiver(self):
        abonne = _create_abonne(self.service)
        suspendu = self.service.suspendre_abonne(str(abonne.id))
        self.assertEqual(suspendu.statut, StatutAbonne.SUSPENDU)
        reactive = self.service.reactiver_abonne(str(abonne.id))
        self.assertEqual(reactive.statut, StatutAbonne.ACTIF)

    def test_suspendre_abonne_deja_suspendu_raises(self):
        abonne = _create_abonne(self.service)
        self.service.suspendre_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.suspendre_abonne(str(abonne.id))

    def test_reactiver_abonne_non_suspendu_raises(self):
        abonne = _create_abonne(self.service)
        with self.assertRaises(ValidationError):
            self.service.reactiver_abonne(str(abonne.id))

    def test_resilier_abonne(self):
        abonne = _create_abonne(self.service)
        resilie = self.service.resilier_abonne(str(abonne.id))
        self.assertEqual(resilie.statut, StatutAbonne.RESILIE)

    def test_resilier_abonne_desactive_le_compteur_actif(self):
        """Régression ANO-017 : le compteur actif doit passer à DESACTIVE
        lors de la résiliation de l'abonné (il n'est ni remplacé, ni
        toujours en service)."""
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        compteur = Compteur.objects.get(abonne_id=abonne.id)
        self.assertEqual(compteur.statut, StatutCompteur.DESACTIVE)

    def test_resilier_abonne_deja_resilie_raises(self):
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.resilier_abonne(str(abonne.id))

    def test_suspendre_abonne_resilie_raises(self):
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.suspendre_abonne(str(abonne.id))

    def test_create_abonne_with_invalid_telephone_raises(self):
        with self.assertRaises(ValidationError):
            _create_abonne(self.service, telephone_whatsapp="pas-un-numero")

    def test_create_abonne_with_empty_telephone_raises(self):
        with self.assertRaises(ValidationError):
            _create_abonne(self.service, telephone_whatsapp="")

    def test_update_abonne_with_invalid_telephone_raises(self):
        abonne = _create_abonne(self.service)
        with self.assertRaises(ValidationError):
            self.service.update_abonne(str(abonne.id), nom="", prenom="", telephone_whatsapp="123", adresse="")

    def test_create_abonne_is_atomic_on_compteur_failure(self):
        # numero_compteur=1 existe déjà : la création du Compteur échoue
        # (contrainte unique) et ne doit pas laisser un Abonne orphelin.
        _create_abonne(self.service)
        with self.assertRaises(IntegrityError):
            _create_abonne(self.service, numero_compteur=1)
        self.assertEqual(Abonne.objects.count(), 1)

    def test_list_abonnes_actifs_excludes_suspendus(self):
        a1 = _create_abonne(self.service)
        a2 = _create_abonne(self.service, numero_compteur=2)
        self.service.suspendre_abonne(str(a2.id))
        actifs = {a.id for a in self.service.list_abonnes_actifs()}
        self.assertIn(a1.id, actifs)
        self.assertNotIn(a2.id, actifs)

    def test_list_abonnes_filters_by_statut(self):
        a1 = _create_abonne(self.service)
        self.service.suspendre_abonne(str(a1.id))
        suspendus = self.service.list_abonnes(StatutAbonne.SUSPENDU)
        self.assertEqual([a.id for a in suspendus], [a1.id])


class CompteurServiceTests(TestCase):
    def setUp(self):
        self.abonne_service = AbonneService()
        self.compteur_service = CompteurService()

    def test_remplacer_compteur_archives_old_and_creates_new(self):
        abonne = _create_abonne(self.abonne_service, index_initial=0)
        ancien = self.compteur_service.get_compteur_actif(str(abonne.id))

        nouveau = self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Nouveau Quartier",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )

        ancien.refresh_from_db()
        self.assertEqual(ancien.statut, StatutCompteur.REMPLACE)
        self.assertEqual(nouveau.statut, StatutCompteur.ACTIF)
        self.assertEqual(nouveau.numero_compteur, 2)
        self.assertEqual(self.compteur_service.get_compteur_actif(str(abonne.id)).id, nouveau.id)

    def test_remplacer_compteur_enregistre_le_motif(self):
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
            motif="Compteur défectueux",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique[0].motif, "Compteur défectueux")

    def test_remplacer_compteur_motif_optionnel_defaut_vide(self):
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique[0].motif, "")

    def test_remplacer_compteur_with_index_fermeture_below_initial_raises(self):
        abonne = _create_abonne(self.abonne_service, index_initial=50)

        with self.assertRaises(ValidationError):
            self.compteur_service.remplacer_compteur(
                abonne_id=str(abonne.id),
                index_fermeture=10,
                nouveau_numero_compteur=2,
                nouveau_quartier="Q",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            )

    def test_update_compteur_modifie_les_champs_fournis(self):
        abonne = _create_abonne(self.abonne_service, quartier="Ancien", camp=1)
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id), quartier="Nouveau", camp=2, index_initial=None, date_pose=None
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.quartier, "Nouveau")
        self.assertEqual(compteur.camp, 2)

    def test_update_compteur_ne_modifie_pas_les_champs_non_fournis(self):
        abonne = _create_abonne(self.abonne_service, quartier="Bastos", index_initial=50)
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id), quartier="Nlongkak", camp=None, index_initial=None, date_pose=None
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.quartier, "Nlongkak")
        self.assertEqual(float(compteur.index_initial), 50.0)

    def test_remplacer_compteur_is_atomic_on_numero_collision(self):
        # nouveau_numero_compteur=1 collisionne avec le compteur actif
        # lui-même : la création échoue et l'ancien compteur ne doit pas
        # rester archivé sans qu'un nouveau compteur ACTIF existe.
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        ancien = self.compteur_service.get_compteur_actif(str(abonne.id))

        with self.assertRaises(IntegrityError):
            self.compteur_service.remplacer_compteur(
                abonne_id=str(abonne.id),
                index_fermeture=10,
                nouveau_numero_compteur=1,
                nouveau_quartier="Q",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            )

        ancien.refresh_from_db()
        self.assertEqual(ancien.statut, StatutCompteur.ACTIF)
        self.assertEqual(self.compteur_service.get_compteur_actif(str(abonne.id)).id, ancien.id)

    def test_get_historique_returns_entry_after_remplacement(self):
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=100,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(len(historique), 1)
        self.assertEqual(float(historique[0].index_fermeture), 100.0)
        self.assertEqual(historique[0].nouveau_compteur.numero_compteur, 2)

    def test_get_historique_empty_for_new_abonne(self):
        abonne = _create_abonne(self.abonne_service)
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique, [])
