"""Tests du throttle des demandes sensibles (throttle.py).

RÈGLE ABSOLUE : ces tests ne déclenchent jamais de vraie connexion Redis —
Redis est entièrement mocké (même patron que
`notifications/tests/test_rate_limiter.py` côté service Notification).
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from comptes import throttle
from comptes.throttle import ThrottleError, verifier_throttle


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class ThrottleDisabledInTestingTests(SimpleTestCase):
    def test_noop_en_environnement_de_test(self) -> None:
        """settings.TESTING est déjà True dans ce process (`manage.py test`) :
        le throttle doit être un no-op, sans jamais toucher Redis."""
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            verifier_throttle("otp-throttle:+237690000000")
            verifier_throttle("otp-throttle:+237690000000")  # jamais bloqué non plus
        client.set.assert_not_called()


@override_settings(TESTING=False)
class ThrottleViaRedisTests(SimpleTestCase):
    def test_premiere_demande_passe(self) -> None:
        """Le SET NX EX réussit : la demande est acceptée sans lever."""
        client = MagicMock()
        client.set.return_value = True
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            verifier_throttle("otp-throttle:+237690000000")
        client.set.assert_called_once_with("otp-throttle:+237690000000", "1", nx=True, ex=60)
        client.close.assert_called_once()

    def test_deuxieme_demande_immediate_est_bloquee(self) -> None:
        """Le SET NX échoue (clé déjà posée par la 1re demande) : ThrottleError."""
        client = MagicMock()
        client.set.return_value = False
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            with self.assertRaises(ThrottleError):
                verifier_throttle("otp-throttle:+237690000000")

    def test_apres_expiration_une_nouvelle_demande_repasse(self) -> None:
        """La clé Redis expire au bout de `ex` secondes (EX du SET) : un SET NX
        ultérieur réussit à nouveau, sans action explicite de notre côté —
        simulé ici par un deuxième appel à `set` qui réussit de nouveau."""
        client = MagicMock()
        client.set.side_effect = [True, False, True]
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            verifier_throttle("otp-throttle:+237690000000")  # 1re demande : passe
            with self.assertRaises(ThrottleError):
                verifier_throttle("otp-throttle:+237690000000")  # 2e immédiate : bloquée
            verifier_throttle("otp-throttle:+237690000000")  # après expiration : repasse
        self.assertEqual(client.set.call_count, 3)

    def test_fenetre_personnalisee_est_transmise_a_redis(self) -> None:
        client = MagicMock()
        client.set.return_value = True
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            verifier_throttle("password-reset-throttle:admin@example.com", fenetre_secondes=30)
        client.set.assert_called_once_with("password-reset-throttle:admin@example.com", "1", nx=True, ex=30)

    def test_redis_indisponible_replie_sur_verrou_local(self) -> None:
        """Si Redis lève une exception, le throttle ne doit ni crasher ni
        laisser passer indéfiniment — il retombe sur un verrou local."""
        fake_redis = SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(side_effect=ConnectionError("down"))))
        with patch.dict(sys.modules, {"redis": fake_redis}):
            with patch("comptes.throttle._verifier_local") as mock_local:
                verifier_throttle("otp-throttle:+237690000000")
        mock_local.assert_called_once_with("otp-throttle:+237690000000", 60)


@override_settings(TESTING=False)
class ThrottleLocalFallbackTests(SimpleTestCase):
    def setUp(self) -> None:
        throttle._local_dernieres_demandes.clear()

    def test_premiere_demande_locale_passe(self) -> None:
        with patch("comptes.throttle.time.monotonic", return_value=1000.0):
            throttle._verifier_local("cle-test", 60)
        self.assertEqual(throttle._local_dernieres_demandes["cle-test"], 1000.0)

    def test_deuxieme_demande_locale_immediate_est_bloquee(self) -> None:
        with patch("comptes.throttle.time.monotonic", return_value=1000.0):
            throttle._verifier_local("cle-test", 60)
        with patch("comptes.throttle.time.monotonic", return_value=1010.0):
            with self.assertRaises(ThrottleError):
                throttle._verifier_local("cle-test", 60)

    def test_apres_le_delai_la_demande_locale_repasse(self) -> None:
        with patch("comptes.throttle.time.monotonic", return_value=1000.0):
            throttle._verifier_local("cle-test", 60)
        with patch("comptes.throttle.time.monotonic", return_value=1061.0):
            throttle._verifier_local("cle-test", 60)  # 61s plus tard : repasse
