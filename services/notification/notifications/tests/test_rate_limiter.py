"""Tests du rate-limiter global des envois WhatsApp (rate_limiter.py).

RÈGLE ABSOLUE : ces tests ne déclenchent jamais de vrai envoi WhatsApp ni de
vraie connexion Redis — Redis est entièrement mocké (même patron que
`paiements/tests/test_event_publisher.py`), et `time.sleep`/`time.monotonic`
sont mockés pour ne jamais faire réellement attendre la suite de tests.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from notifications import rate_limiter


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class ThrottleDisabledContextsTests(SimpleTestCase):
    def test_noop_en_environnement_de_test(self):
        """settings.TESTING est déjà True dans ce process (`manage.py test`) :
        le throttle doit être un no-op, sans jamais toucher Redis."""
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            rate_limiter.throttle_whatsapp_send()
        client.set.assert_not_called()

    @override_settings(TESTING=False, WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS=0)
    def test_noop_si_intervalle_nul(self):
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            rate_limiter.throttle_whatsapp_send()
        client.set.assert_not_called()


@override_settings(TESTING=False, WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS=3.0)
class ThrottleViaRedisTests(SimpleTestCase):
    def test_verrou_acquis_immediatement_ne_bloque_pas(self):
        client = MagicMock()
        client.set.return_value = True
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            with patch("notifications.rate_limiter.time.sleep") as mock_sleep:
                rate_limiter.throttle_whatsapp_send()

        client.set.assert_called_once_with(rate_limiter._REDIS_KEY, "1", nx=True, px=3000)
        mock_sleep.assert_not_called()
        client.close.assert_called_once()

    def test_verrou_deja_detenu_attend_puis_reessaie(self):
        client = MagicMock()
        # Échoue une première fois (verrou détenu par un autre envoi), puis réussit.
        client.set.side_effect = [False, True]
        client.pttl.return_value = 120  # ms restants sur le verrou existant

        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            with patch("notifications.rate_limiter.time.sleep") as mock_sleep:
                rate_limiter.throttle_whatsapp_send()

        self.assertEqual(client.set.call_count, 2)
        mock_sleep.assert_called_once()
        # On n'attend jamais plus que l'intervalle configuré, même si le TTL
        # rapporté par Redis est plus grand.
        self.assertLessEqual(mock_sleep.call_args.args[0], 3.0)

    def test_redis_indisponible_replie_sur_verrou_local(self):
        """Si Redis lève une exception, le throttle ne doit ni crasher ni
        laisser passer l'envoi sans délai — il retombe sur un verrou local."""
        fake_redis = SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(side_effect=ConnectionError("down"))))
        with patch.dict(sys.modules, {"redis": fake_redis}):
            with patch("notifications.rate_limiter._throttle_local") as mock_local:
                rate_limiter.throttle_whatsapp_send()
        mock_local.assert_called_once_with(3.0)

    def test_attente_maximale_finit_par_abandonner(self):
        """Si le verrou reste indéfiniment détenu (pic durable), le throttle
        finit par laisser passer l'envoi plutôt que de bloquer pour toujours."""
        client = MagicMock()
        client.set.return_value = False  # jamais acquis
        client.pttl.return_value = 50

        # Simule l'écoulement du temps sans vraiment attendre : chaque appel à
        # monotonic() avance l'horloge simulée au-delà du délai max.
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 40.0
            return clock["t"]

        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            with patch("notifications.rate_limiter.time.sleep"):
                with patch("notifications.rate_limiter.time.monotonic", side_effect=fake_monotonic):
                    rate_limiter.throttle_whatsapp_send()  # ne doit pas boucler indéfiniment ni lever

        client.close.assert_called_once()


@override_settings(TESTING=False, WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS=2.0)
class ThrottleLocalFallbackTests(SimpleTestCase):
    def setUp(self):
        rate_limiter._local_last_send_monotonic = 0.0

    def test_premier_appel_ne_bloque_pas(self):
        with patch("notifications.rate_limiter.time.monotonic", return_value=1000.0):
            with patch("notifications.rate_limiter.time.sleep") as mock_sleep:
                rate_limiter._throttle_local(2.0)
        mock_sleep.assert_not_called()

    def test_appel_rapproche_attend_le_reste_de_l_intervalle(self):
        rate_limiter._local_last_send_monotonic = 1000.0
        with patch("notifications.rate_limiter.time.monotonic", return_value=1000.5):
            with patch("notifications.rate_limiter.time.sleep") as mock_sleep:
                rate_limiter._throttle_local(2.0)
        mock_sleep.assert_called_once()
        (waited,) = mock_sleep.call_args.args
        self.assertAlmostEqual(waited, 1.5, places=3)


class WhatsAppClientCallsThrottleTests(SimpleTestCase):
    """Vérifie que le point de passage unique des envois (whatsapp_client)
    appelle bien le throttle — sans jamais faire de vraie requête réseau."""

    @patch("notifications.whatsapp_client.throttle_whatsapp_send")
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_appelle_le_throttle_avant_la_requete(self, mock_post, mock_throttle):
        from notifications.whatsapp_client import WhatsAppWebClient

        mock_post.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"success": True}))
        WhatsAppWebClient().send("+237690000000", "Bonjour")

        mock_throttle.assert_called_once()
        mock_post.assert_called_once()

    @patch("notifications.whatsapp_client.throttle_whatsapp_send")
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_appelle_le_throttle_avant_la_requete(self, mock_post, mock_throttle):
        from notifications.whatsapp_client import WhatsAppWebClient

        mock_post.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"success": True}))
        WhatsAppWebClient().send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "facture.pdf")

        mock_throttle.assert_called_once()
        mock_post.assert_called_once()
