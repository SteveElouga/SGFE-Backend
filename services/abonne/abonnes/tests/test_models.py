from django.db import IntegrityError
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

    def test_deux_compteurs_actifs_pour_le_meme_abonne_leve_erreur(self):
        """Régression ANO-017 : la contrainte DB unique_compteur_actif_par_abonne
        doit empêcher un deuxième compteur ACTIF pour le même abonné, même en
        cas de bug applicatif qui contournerait la logique de service."""
        abonne = Abonne.objects.create(
            numero_abonne="AB-0004", nom="Doe", prenom="Jack", telephone_whatsapp="+241000003"
        )
        Compteur.objects.create(
            abonne=abonne, numero_compteur=10, quartier="Centre", camp=1, index_initial=0, date_pose="2024-01-01"
        )
        with self.assertRaises(IntegrityError):
            Compteur.objects.create(
                abonne=abonne, numero_compteur=11, quartier="Centre", camp=1, index_initial=0, date_pose="2024-01-01"
            )

    def test_deux_compteurs_non_actifs_pour_le_meme_abonne_autorises(self):
        """La contrainte est bien partielle (condition statut=ACTIF) : un
        abonné peut avoir plusieurs compteurs REMPLACE/DESACTIVE en historique."""
        abonne = Abonne.objects.create(
            numero_abonne="AB-0005", nom="Doe", prenom="Jill", telephone_whatsapp="+241000004"
        )
        Compteur.objects.create(
            abonne=abonne,
            numero_compteur=20,
            quartier="Centre",
            camp=1,
            index_initial=0,
            date_pose="2024-01-01",
            statut=StatutCompteur.REMPLACE,
        )
        Compteur.objects.create(
            abonne=abonne,
            numero_compteur=21,
            quartier="Centre",
            camp=1,
            index_initial=0,
            date_pose="2024-01-01",
            statut=StatutCompteur.DESACTIVE,
        )
        self.assertEqual(Compteur.objects.filter(abonne=abonne).count(), 2)
