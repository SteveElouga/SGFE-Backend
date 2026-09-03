"""Changer son mot de passe ferme les sessions ouvertes.

Quelqu'un qui change son mot de passe parce qu'il pense son compte compromis
attend une chose : que l'intrus soit dehors. Sans cela, le geste de défense ne
défend rien — la session de l'attaquant continue de fonctionner jusqu'à
l'expiration naturelle de son jeton, sept jours pour un rafraîchissement.

La liste noire ne pouvait pas rendre ce service : elle révoque un jeton
nommément, et les jetons émis ne sont stockés nulle part. Comparer la date
d'émission à l'horodatage du changement les révoque tous d'un coup, sur tous les
appareils, sans rien stocker par session.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from comptes.models import PasswordSetupToken, Role, User
from comptes.services import (
    AuthenticationError,
    AuthService,
    PasswordSetupService,
    PhoneOtpService,
)


class RevocationParChangementTests(TestCase):
    def setUp(self) -> None:
        self.auth = AuthService()
        self.user = User.objects.create_user(
            username="cible",
            email="cible@example.com",
            password="secret123",
            role=Role.COMPTABLE,
            phone_number="+237690000042",
        )

    def _session_ouverte(self) -> tuple[str, str, int]:
        """Un couple de jetons, comme en aurait un intrus déjà connecté."""
        return self.auth.login("cible", "secret123")

    def _changer_par_lien(self, mot_de_passe: str = "S3cr3t!", decalage_s: int = 2) -> None:
        """Change le mot de passe, en le datant `decalage_s` secondes plus tard.

        `iat` est une seconde epoch : dans un test qui s'exécute en quelques
        millisecondes, la connexion et le changement tombent dans la même
        seconde et deviennent indiscernables. Décaler l'horloge du changement
        reproduit la seule situation qui compte en vrai — un intrus connecté
        avant, et un mot de passe changé après.
        """
        token = PasswordSetupToken.objects.create(user=self.user, expires_at=timezone.now() + timedelta(hours=1))
        plus_tard = timezone.now() + timedelta(seconds=decalage_s)
        with patch("comptes.services.timezone.now", return_value=plus_tard):
            PasswordSetupService().set_password_with_token(str(token.token), mot_de_passe)

    # ── Le cœur du sujet ─────────────────────────────────────────────────────

    def test_le_jeton_d_acces_anterieur_est_refuse(self) -> None:
        access, _refresh, _ = self._session_ouverte()
        self.auth.validate_token(access)  # valide avant le changement

        self._changer_par_lien()

        with self.assertRaises(AuthenticationError):
            self.auth.validate_token(access)

    def test_le_jeton_de_rafraichissement_anterieur_est_refuse(self) -> None:
        """C'est celui qui compte : il vit sept jours."""
        _access, refresh, _ = self._session_ouverte()
        self._changer_par_lien()

        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token(refresh)

    def test_une_session_ouverte_apres_le_changement_fonctionne(self) -> None:
        # Le changement est daté d'il y a deux secondes : la connexion qui suit
        # est donc postérieure, comme dans la vraie vie.
        self._changer_par_lien(decalage_s=-2)
        access, refresh, _ = self.auth.login("cible", "S3cr3t!")
        self.assertEqual(self.auth.validate_token(access).username, "cible")
        self.assertTrue(self.auth.refresh_token(refresh))

    def test_le_changement_par_otp_ferme_aussi_les_sessions(self) -> None:
        """Les deux chemins de réinitialisation doivent se comporter pareil.

        Le code est fixé plutôt que deviné : le service l'envoie par WhatsApp et
        ne le rend jamais, ce qui est correct — mais rend le vrai chemin
        intestable sans le connaître.
        """
        access, _refresh, _ = self._session_ouverte()

        service = PhoneOtpService()
        with (
            patch("comptes.services._generate_otp", return_value="000000"),
            patch("comptes.services.whatsapp_client.send"),
        ):
            service.send_otp(self.user)

        plus_tard = timezone.now() + timedelta(seconds=2)
        with patch("comptes.services.timezone.now", return_value=plus_tard):
            service.verify_otp_and_set_password(self.user.phone_number, "000000", "nouveaumotdepasse")

        with self.assertRaises(AuthenticationError):
            self.auth.validate_token(access)

    # ── Ce qui ne doit pas casser ────────────────────────────────────────────

    def test_un_compte_qui_n_a_jamais_change_de_mot_de_passe_reste_connecte(self) -> None:
        """Sinon le déploiement du champ déconnecterait tout le parc d'un coup."""
        self.assertIsNone(self.user.password_changed_at)
        access, refresh, _ = self._session_ouverte()
        self.assertEqual(self.auth.validate_token(access).username, "cible")
        self.assertTrue(self.auth.refresh_token(refresh))

    def test_le_changement_estampille_l_utilisateur(self) -> None:
        avant = timezone.now()
        self._changer_par_lien()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.password_changed_at)
        assert self.user.password_changed_at is not None
        self.assertGreaterEqual(self.user.password_changed_at, avant)

    def test_un_autre_utilisateur_n_est_pas_affecte(self) -> None:
        """La révocation vise une personne, pas le parc."""
        autre = User.objects.create_user(
            username="voisin",
            email="voisin@example.com",
            password="secret",
            role=Role.COMPTABLE,
            phone_number="+237690000043",
        )
        access_voisin, _r, _ = self.auth.login("voisin", "secret")

        self._changer_par_lien()

        self.assertEqual(self.auth.validate_token(access_voisin).username, "voisin")
        self.assertTrue(autre)

    def test_la_rotation_du_rafraichissement_survit_au_garde(self) -> None:
        """Le garde ne doit pas casser le renouvellement normal des jetons."""
        _a, refresh, _ = self._session_ouverte()
        nouveau_access, nouveau_refresh, _ = self.auth.refresh_token(refresh)
        self.assertEqual(self.auth.validate_token(nouveau_access).username, "cible")
        # L'ancien est révoqué par rotation, pas par le garde.
        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token(refresh)
        self.assertTrue(nouveau_refresh)
