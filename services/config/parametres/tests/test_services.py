from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

from parametres.models import CONFIG_DEFAULTS, ConfigParam, InfosSociete
from parametres.services import ConfigService, InfosSocieteService


class InfosSocieteServiceTests(TestCase):
    def setUp(self):
        self.service = InfosSocieteService()

    def test_get_creates_empty_singleton_if_absent(self):
        infos = self.service.get()
        self.assertIsNotNone(infos)
        self.assertEqual(infos.pk, 1)
        self.assertEqual(infos.nom, "")

    def test_get_returns_existing_singleton(self):
        InfosSociete.objects.create(
            pk=1, nom="SGFE", adresse="Yaoundé", telephone="+237"
        )
        infos = self.service.get()
        self.assertEqual(infos.nom, "SGFE")

    def test_update_nom(self):
        infos = self.service.update(nom="Eau Pure SA")
        self.assertEqual(infos.nom, "Eau Pure SA")

    def test_update_adresse(self):
        infos = self.service.update(adresse="123 Rue de l'Eau")
        self.assertEqual(infos.adresse, "123 Rue de l'Eau")

    def test_update_telephone(self):
        infos = self.service.update(telephone="+237699000000")
        self.assertEqual(infos.telephone, "+237699000000")

    def test_update_logo_path(self):
        infos = self.service.update(logo_path="/media/logos/logo.png")
        self.assertEqual(infos.logo_path, "/media/logos/logo.png")

    def test_update_empty_string_does_not_overwrite(self):
        self.service.update(nom="Société Initiale")
        infos = self.service.update(nom="", adresse="Nouvelle adresse")
        self.assertEqual(infos.nom, "Société Initiale")
        self.assertEqual(infos.adresse, "Nouvelle adresse")

    def test_update_persists_to_db(self):
        self.service.update(nom="Persisté")
        infos = InfosSociete.objects.get(pk=1)
        self.assertEqual(infos.nom, "Persisté")


class ConfigServiceTests(TestCase):
    def setUp(self):
        self.service = ConfigService()

    def test_get_known_key_returns_default_if_absent(self):
        param = self.service.get("DELAI_PAIEMENT_JOURS")
        self.assertEqual(param.cle, "DELAI_PAIEMENT_JOURS")
        self.assertEqual(param.valeur, "5")

    def test_get_creates_record_in_db(self):
        self.service.get("TOKEN_VALIDITE_JOURS")
        self.assertTrue(ConfigParam.objects.filter(cle="TOKEN_VALIDITE_JOURS").exists())

    def test_get_unknown_key_raises(self):
        with self.assertRaises(ObjectDoesNotExist):
            self.service.get("CLE_INCONNUE")

    def test_update_known_key_changes_value(self):
        param = self.service.update("DELAI_PAIEMENT_JOURS", "10")
        self.assertEqual(param.valeur, "10")

    def test_update_persists_to_db(self):
        self.service.update("DELAI_PAIEMENT_JOURS", "7")
        param = ConfigParam.objects.get(cle="DELAI_PAIEMENT_JOURS")
        self.assertEqual(param.valeur, "7")

    def test_update_unknown_key_raises(self):
        with self.assertRaises(ObjectDoesNotExist):
            self.service.update("CLE_INCONNUE", "42")

    def test_list_all_returns_all_defaults(self):
        params = self.service.list_all()
        cles = {p.cle for p in params}
        for cle in CONFIG_DEFAULTS:
            self.assertIn(cle, cles)

    def test_list_all_initializes_missing_defaults(self):
        self.assertEqual(ConfigParam.objects.count(), 0)
        self.service.list_all()
        self.assertEqual(ConfigParam.objects.count(), len(CONFIG_DEFAULTS))

    def test_list_all_does_not_duplicate_existing(self):
        self.service.list_all()
        self.service.list_all()
        self.assertEqual(ConfigParam.objects.count(), len(CONFIG_DEFAULTS))

    def test_suspension_auto_default_is_true(self):
        param = self.service.get("SUSPENSION_AUTO_ACTIVE")
        self.assertEqual(param.valeur, "true")

    def test_relance_etapes_defaults(self):
        expected = {
            "RELANCE_ETAPE_1_JOURS": "0",
            "RELANCE_ETAPE_2_JOURS": "3",
            "RELANCE_ETAPE_3_JOURS": "7",
            "RELANCE_ETAPE_4_JOURS": "14",
        }
        for cle, valeur_attendue in expected.items():
            self.assertEqual(self.service.get(cle).valeur, valeur_attendue)
