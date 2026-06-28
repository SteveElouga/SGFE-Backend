from django.test import TestCase

from abonnes.models import StatutAbonne, StatutCompteur
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
        _create_abonne(service)
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
