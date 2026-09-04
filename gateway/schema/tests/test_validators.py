"""Tests des validateurs de format des entrées GraphQL critiques (validators.py)."""

from django.test import SimpleTestCase

from schema.validators import InputValidationError, valider_date_iso, valider_index, valider_telephone_whatsapp


class ValiderIndexTests(SimpleTestCase):
    def test_index_positif_passe(self) -> None:
        valider_index(0.0, "nouveau_index")
        valider_index(42.5, "nouveau_index")

    def test_index_negatif_rejete(self) -> None:
        with self.assertRaises(InputValidationError) as ctx:
            valider_index(-1.0, "nouveau_index")
        self.assertIn("nouveau_index doit être positif ou nul", str(ctx.exception))
        self.assertEqual(InputValidationError.code, "INVALID_ARGUMENT")


class ValiderTelephoneWhatsappTests(SimpleTestCase):
    def test_numero_e164_valide_passe(self) -> None:
        valider_telephone_whatsapp("+24100000000")
        valider_telephone_whatsapp("+237690000000")

    def test_numero_avec_espaces_et_tirets_est_nettoye(self) -> None:
        valider_telephone_whatsapp("+237 69-00-00-00")

    def test_numero_vide_rejete(self) -> None:
        with self.assertRaises(InputValidationError):
            valider_telephone_whatsapp("")

    def test_numero_sans_indicatif_rejete(self) -> None:
        with self.assertRaises(InputValidationError) as ctx:
            valider_telephone_whatsapp("690000000")
        self.assertIn("telephone_whatsapp invalide", str(ctx.exception))

    def test_numero_trop_court_rejete(self) -> None:
        with self.assertRaises(InputValidationError):
            valider_telephone_whatsapp("+241")

    def test_numero_avec_lettres_rejete(self) -> None:
        with self.assertRaises(InputValidationError):
            valider_telephone_whatsapp("pas-un-numero")


class ValiderDateIsoTests(SimpleTestCase):
    def test_date_iso_valide_passe(self) -> None:
        valider_date_iso("2024-01-01", "date_pose")

    def test_chaine_vide_est_toleree(self) -> None:
        """Certains champs date sont optionnels côté service (ex.
        `date_planifiee = ""` = « non planifiée ») : ne pas les rejeter."""
        valider_date_iso("", "date_planifiee")

    def test_date_mal_formee_rejetee(self) -> None:
        with self.assertRaises(InputValidationError) as ctx:
            valider_date_iso("pas-une-date", "date_pose")
        self.assertIn("date_pose invalide", str(ctx.exception))

    def test_date_avec_mois_hors_bornes_rejetee(self) -> None:
        with self.assertRaises(InputValidationError):
            valider_date_iso("2024-13-01", "date_pose")
