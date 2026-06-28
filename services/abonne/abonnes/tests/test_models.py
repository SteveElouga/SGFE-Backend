from django.test import TestCase

from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur


class AbonneModelTests(TestCase):
    def test_create_abonne_defaults_to_actif(self):
        abonne = Abonne.objects.create(
            numero_abonne="AB-0001", nom="Doe", prenom="John", telephone_whatsapp="+241000000"
        )
        self.assertEqual(abonne.statut, StatutAbonne.ACTIF)

    def test_str_includes_numero_and_nom(self):
        abonne = Abonne.objects.create(
            numero_abonne="AB-0002", nom="Doe", prenom="Jane", telephone_whatsapp="+241000001"
        )
        self.assertIn("AB-0002", str(abonne))
        self.assertIn("Doe", str(abonne))


class CompteurModelTests(TestCase):
    def test_create_compteur_defaults_to_actif(self):
        abonne = Abonne.objects.create(
            numero_abonne="AB-0003", nom="Doe", prenom="Jim", telephone_whatsapp="+241000002"
        )
        compteur = Compteur.objects.create(
            abonne=abonne, numero_compteur=1, quartier="Centre", camp=1, index_initial=0, date_pose="2024-01-01"
        )
        self.assertEqual(compteur.statut, StatutCompteur.ACTIF)
        self.assertIn(str(compteur.numero_compteur), str(compteur))
