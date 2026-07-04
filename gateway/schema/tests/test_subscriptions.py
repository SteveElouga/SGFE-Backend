from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from schema.context import AuthError
from schema.subscriptions import Subscription


class SubscriptionAbonneUpdatedTests(IsolatedAsyncioTestCase):
    """Régression ANO-015 : abonneUpdated ne doit plus être accessible à un
    client WebSocket non authentifié — la query équivalente (abonne/abonnes)
    exige déjà ADMIN, la subscription doit se comporter pareil."""

    @patch("schema.subscriptions.require_role")
    async def test_abonne_updated_sans_auth_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Authentification requise", code="UNAUTHENTICATED")

        info = MagicMock()
        agen = Subscription().abonne_updated(info=info)

        with self.assertRaises(AuthError):
            await agen.__anext__()

        mock_require_role.assert_called_once_with(info, "ADMIN")

    @patch("schema.subscriptions.require_role")
    async def test_abonne_updated_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")

        info = MagicMock()
        agen = Subscription().abonne_updated(info=info)

        with self.assertRaises(AuthError):
            await agen.__anext__()

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.abonne_client")
    @patch("schema.subscriptions.require_role")
    async def test_abonne_updated_admin_recoit_les_mises_a_jour(
        self, mock_require_role, mock_abonne_client, mock_redis_cls
    ):
        """Une fois authentifié ADMIN, le flux Redis -> gRPC -> yield doit
        continuer à fonctionner normalement (pas de régression fonctionnelle)."""
        mock_require_role.return_value = MagicMock(role="ADMIN")

        async def _listen():
            yield {"type": "message", "data": '{"abonne_id": "abonne-1"}'}

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.listen = _listen

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub.return_value = mock_pubsub
        mock_redis_instance.aclose = AsyncMock()
        mock_redis_cls.from_url.return_value = mock_redis_instance

        abonne_response = MagicMock()
        abonne_response.HasField.return_value = False
        abonne_response.abonne_id = "abonne-1"
        abonne_response.numero_abonne = "AB-0001"
        abonne_response.nom = "DUPONT"
        abonne_response.prenom = "Jean"
        abonne_response.telephone_whatsapp = "+237690000000"
        abonne_response.adresse = ""
        abonne_response.statut = "ACTIF"
        abonne_response.created_at = "2026-07-01T00:00:00Z"
        mock_abonne_client.get_abonne.return_value = abonne_response

        info = MagicMock()
        agen = Subscription().abonne_updated(info=info)
        result = await agen.__anext__()

        self.assertEqual(result.id, "abonne-1")


class SubscriptionWhatsappStatusTests(IsolatedAsyncioTestCase):
    """whatsappStatus pousse le statut de connexion + QR en temps réel,
    réservé ADMIN comme la query whatsappQr équivalente."""

    @patch("schema.subscriptions.require_role")
    async def test_whatsapp_status_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")

        info = MagicMock()
        agen = Subscription().whatsapp_status(info=info)

        with self.assertRaises(AuthError):
            await agen.__anext__()

        mock_require_role.assert_called_once_with(info, "ADMIN")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.notification_client")
    @patch("schema.subscriptions.require_role")
    async def test_whatsapp_status_admin_snapshot_puis_evenements(
        self, mock_require_role, mock_notification_client, mock_redis_cls
    ):
        """ADMIN reçoit d'abord un snapshot initial de l'état courant, puis
        chaque changement publié sur Redis (nouveau QR, connexion…)."""
        mock_require_role.return_value = MagicMock(role="ADMIN")

        # Snapshot initial : déconnecté, QR déjà disponible.
        snapshot = MagicMock()
        snapshot.ready = False
        snapshot.qr = "data:image/png;base64,SNAP"
        snapshot.number = ""
        mock_notification_client.get_whatsapp_qr.return_value = snapshot

        # Événement poussé ensuite par whatsapp-service : connexion établie.
        async def _listen():
            yield {"type": "message", "data": '{"ready": true, "qr": "", "number": "237690000000"}'}

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.listen = _listen

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub.return_value = mock_pubsub
        mock_redis_instance.aclose = AsyncMock()
        mock_redis_cls.from_url.return_value = mock_redis_instance

        info = MagicMock()
        agen = Subscription().whatsapp_status(info=info)

        # 1er yield = snapshot initial (état courant immédiat)
        first = await agen.__anext__()
        self.assertFalse(first.ready)
        self.assertEqual(first.qr, "data:image/png;base64,SNAP")

        # 2e yield = événement Redis (connexion établie)
        second = await agen.__anext__()
        self.assertTrue(second.ready)
        self.assertEqual(second.number, "237690000000")
        mock_pubsub.subscribe.assert_awaited_once_with("whatsapp:events")
