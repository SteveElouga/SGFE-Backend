from django.test import TestCase

from abonnes.models import Abonne, Compteur
from abonnes.serializers import abonne_to_response, compteur_to_response


class SerializerTests(TestCase):
    def setUp(self) -> None:
        self.abonne = Abonne.objects.create(
            numero_abonne="AB-0001",
            nom="Doe",
            prenom="John",
            telephone_whatsapp="+24100000000",
            adresse="Quartier X",
        )
        self.compteur = Compteur.objects.create(
            abonne=self.abonne,
            numero_compteur=1,
            quartier="Centre",
            camp=1,
            index_initial=0,
            date_pose="2024-01-01",
        )

    def test_compteur_to_response(self) -> None:
        data = compteur_to_response(self.compteur)
        self.assertEqual(data["numero_compteur"], 1)
        self.assertEqual(data["index_initial"], 0.0)
        self.assertEqual(data["date_pose"], "2024-01-01")

    def test_abonne_to_response_with_compteur(self) -> None:
        data = abonne_to_response(self.abonne, self.compteur)
        self.assertEqual(data["numero_abonne"], "AB-0001")
        compteur_data = data["compteur"]
        assert compteur_data is not None
        self.assertEqual(compteur_data["numero_compteur"], 1)

    def test_abonne_to_response_without_compteur(self) -> None:
        data = abonne_to_response(self.abonne)
        self.assertIsNone(data["compteur"])
