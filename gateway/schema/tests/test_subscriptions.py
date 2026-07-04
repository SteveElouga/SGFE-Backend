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


def _mock_redis(listen_gen, mock_redis_cls):
    """Câble un pubsub Redis mocké dont listen() renvoie l'async-gen fourni."""
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.listen = listen_gen
    instance = MagicMock()
    instance.pubsub.return_value = pubsub
    instance.aclose = AsyncMock()
    mock_redis_cls.from_url.return_value = instance
    return pubsub


class SubscriptionFactureUpdatedTests(IsolatedAsyncioTestCase):
    """factureUpdated : réservé ADMIN/COMPTABLE, pousse la facture re-fetchée."""

    @patch("schema.subscriptions.require_role")
    async def test_factureupdated_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")

        info = MagicMock()
        agen = Subscription().facture_updated(info=info)
        with self.assertRaises(AuthError):
            await agen.__anext__()
        mock_require_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.facturation_client")
    @patch("schema.subscriptions.require_role")
    async def test_factureupdated_admin_pousse_la_facture(
        self, mock_require_role, mock_facturation_client, mock_redis_cls
    ):
        mock_require_role.return_value = MagicMock()

        async def _listen():
            yield {"type": "message", "data": '{"facture_id": "fac-1", "campagne_id": "camp-1"}'}

        _mock_redis(_listen, mock_redis_cls)

        facture = MagicMock()
        facture.facture_id = "fac-1"
        facture.campagne_id = "camp-1"
        facture.statut = "PARTIELLE"
        for attr in (
            "numero_facture",
            "abonne_id",
            "date_releve",
            "date_limite_paiement",
            "date_generation",
            "pdf_path",
            "numero_mobile_money",
        ):
            setattr(facture, attr, "")
        for attr in ("ancien_index", "nouveau_index", "consommation", "prix_m3", "montant"):
            setattr(facture, attr, 0.0)
        mock_facturation_client.get_facture.return_value = facture

        agen = Subscription().facture_updated(info=MagicMock())
        result = await agen.__anext__()
        self.assertEqual(result.facture_id, "fac-1")
        self.assertEqual(result.statut, "PARTIELLE")
        mock_facturation_client.get_facture.assert_called_once_with("fac-1")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.facturation_client")
    @patch("schema.subscriptions.require_role")
    async def test_factureupdated_filtre_campagne_ecarte_les_autres(
        self, mock_require_role, mock_facturation_client, mock_redis_cls
    ):
        """Un événement d'une autre campagne ne doit rien pousser (ni re-fetch)."""
        mock_require_role.return_value = MagicMock()

        async def _listen():
            yield {"type": "message", "data": '{"facture_id": "fac-1", "campagne_id": "AUTRE"}'}

        _mock_redis(_listen, mock_redis_cls)

        agen = Subscription().facture_updated(info=MagicMock(), campagne_id="camp-1")
        with self.assertRaises(StopAsyncIteration):
            await agen.__anext__()
        mock_facturation_client.get_facture.assert_not_called()


class SubscriptionPaiementCreeTests(IsolatedAsyncioTestCase):
    """paiementCree : réservé ADMIN/COMPTABLE, événement Redis auto-porteur."""

    @patch("schema.subscriptions.require_role")
    async def test_paiementcree_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")

        info = MagicMock()
        agen = Subscription().paiement_cree(info=info)
        with self.assertRaises(AuthError):
            await agen.__anext__()
        mock_require_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.auth_client")
    @patch("schema.subscriptions.require_role")
    async def test_paiementcree_admin_pousse_le_paiement(self, mock_require_role, mock_auth_client, mock_redis_cls):
        """Sans filtre : le paiement est reconstruit depuis l'événement, avec le
        statut de facture et l'opérateur résolu (aucun re-fetch de facture)."""
        mock_require_role.return_value = MagicMock()
        mock_auth_client.get_user.return_value = MagicMock(username="comptable1")

        data = (
            '{"event_type": "PAIEMENT_CREATED", "paiement_id": "pay-1", "facture_id": "fac-1",'
            ' "montant": 5000, "date_paiement": "2026-07-04", "mode_paiement": "ESPECES",'
            ' "reference_transaction": "", "created_at": "2026-07-04T10:00:00",'
            ' "enregistre_par": "user-1", "statut_facture": "PARTIELLE"}'
        )

        async def _listen():
            yield {"type": "message", "data": data}

        pubsub = _mock_redis(_listen, mock_redis_cls)

        agen = Subscription().paiement_cree(info=MagicMock())
        result = await agen.__anext__()
        self.assertEqual(result.paiement_id, "pay-1")
        self.assertEqual(result.montant, 5000.0)
        self.assertEqual(result.statut_facture, "PARTIELLE")
        self.assertEqual(result.operateur, "comptable1")
        pubsub.subscribe.assert_awaited_once_with("paiement:events")


def _mock_user(user_id: str, role: str = "ADMIN") -> MagicMock:
    """UserResponse gRPC mocké, exploitable par user_from_grpc."""
    u = MagicMock()
    u.user_id = user_id
    u.username = "bob"
    u.email = "bob@example.com"
    u.phone_number = "+237690000000"
    u.role = role
    u.is_active = True
    u.created_at = "2026-07-01T00:00:00Z"
    return u


class SubscriptionUtilisateurUpdatedTests(IsolatedAsyncioTestCase):
    """utilisateurUpdated : ADMIN suit tout le monde ; un non-ADMIN ne peut suivre
    que son propre compte (cas « profil » sécurité)."""

    @patch("schema.subscriptions.require_auth")
    async def test_non_admin_sans_filtre_soi_leve_autherror(self, mock_require_auth):
        """Un non-ADMIN sans filtre (ou filtrant un autre id) est refusé."""
        mock_require_auth.return_value = MagicMock(role="COMPTABLE", user_id="u-1")

        agen = Subscription().utilisateur_updated(info=MagicMock())
        with self.assertRaises(AuthError):
            await agen.__anext__()

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.auth_client")
    @patch("schema.subscriptions.require_auth")
    async def test_non_admin_sur_son_propre_id_recoit_son_compte(
        self, mock_require_auth, mock_auth_client, mock_redis_cls
    ):
        """Cas profil : un COMPTABLE peut suivre son propre id (déconnexion forcée
        si un admin le désactive / change son rôle)."""
        mock_require_auth.return_value = MagicMock(role="COMPTABLE", user_id="u-1")
        mock_auth_client.get_user.return_value = _mock_user("u-1")

        async def _listen():
            yield {"type": "message", "data": '{"event_type": "USER_UPDATED", "user_id": "u-1"}'}

        _mock_redis(_listen, mock_redis_cls)

        agen = Subscription().utilisateur_updated(info=MagicMock(), utilisateur_id="u-1")
        result = await agen.__anext__()
        self.assertEqual(result.id, "u-1")
        mock_auth_client.get_user.assert_called_once_with("u-1")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.auth_client")
    @patch("schema.subscriptions.require_auth")
    async def test_admin_flux_global_recoit_les_autres(self, mock_require_auth, mock_auth_client, mock_redis_cls):
        mock_require_auth.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        mock_auth_client.get_user.return_value = _mock_user("u-2")

        async def _listen():
            yield {"type": "message", "data": '{"event_type": "USER_CREATED", "user_id": "u-2"}'}

        _mock_redis(_listen, mock_redis_cls)

        agen = Subscription().utilisateur_updated(info=MagicMock())
        result = await agen.__anext__()
        self.assertEqual(result.id, "u-2")


class SubscriptionConfigUpdatedTests(IsolatedAsyncioTestCase):
    """configUpdated : ADMIN, pousse le paramètre re-fetché."""

    @patch("schema.subscriptions.require_role")
    async def test_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")
        info = MagicMock()
        agen = Subscription().config_updated(info=info)
        with self.assertRaises(AuthError):
            await agen.__anext__()
        mock_require_role.assert_called_once_with(info, "ADMIN")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.config_client")
    @patch("schema.subscriptions.require_role")
    async def test_admin_pousse_le_parametre(self, mock_require_role, mock_config_client, mock_redis_cls):
        mock_require_role.return_value = MagicMock()

        async def _listen():
            yield {"type": "message", "data": '{"cle": "delai_paiement_jours"}'}

        _mock_redis(_listen, mock_redis_cls)

        param = MagicMock()
        param.cle = "delai_paiement_jours"
        param.valeur = "7"
        param.description = "Délai de paiement"
        mock_config_client.get_config.return_value = param

        agen = Subscription().config_updated(info=MagicMock())
        result = await agen.__anext__()
        self.assertEqual(result.cle, "delai_paiement_jours")
        self.assertEqual(result.valeur, "7")
        mock_config_client.get_config.assert_called_once_with("delai_paiement_jours")


class SubscriptionTarifUpdatedTests(IsolatedAsyncioTestCase):
    """tarifUpdated : ADMIN/COMPTABLE, re-fetch le tarif actif."""

    @patch("schema.subscriptions.require_role")
    async def test_role_insuffisant_leve_autherror(self, mock_require_role):
        mock_require_role.side_effect = AuthError("Accès non autorisé", code="PERMISSION_DENIED")
        info = MagicMock()
        agen = Subscription().tarif_updated(info=info)
        with self.assertRaises(AuthError):
            await agen.__anext__()
        mock_require_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.facturation_client")
    @patch("schema.subscriptions.require_role")
    async def test_pousse_le_tarif_actif(self, mock_require_role, mock_facturation_client, mock_redis_cls):
        mock_require_role.return_value = MagicMock()

        async def _listen():
            yield {"type": "message", "data": '{"event_type": "TARIF_UPDATED"}'}

        _mock_redis(_listen, mock_redis_cls)

        tarif = MagicMock()
        tarif.tarif_id = "t-1"
        tarif.prix_m3 = 515.0
        tarif.date_effet = "2026-07-01"
        tarif.is_active = True
        mock_facturation_client.get_tarif_actuel.return_value = tarif

        agen = Subscription().tarif_updated(info=MagicMock())
        result = await agen.__anext__()
        self.assertEqual(result.tarif_id, "t-1")
        self.assertEqual(result.prix_m3, 515.0)


class SubscriptionProgressionUpdatedTests(IsolatedAsyncioTestCase):
    """progressionUpdated : ADMIN/AGENT/SUPERVISEUR ; un SUPERVISEUR/AGENT ne
    voit que ses campagnes, le flux global est réservé à l'ADMIN."""

    @patch("schema.subscriptions.require_role")
    async def test_flux_global_non_admin_refuse(self, mock_require_role):
        mock_require_role.return_value = MagicMock(role="AGENT", user_id="a-1")
        agen = Subscription().progression_updated(info=MagicMock())
        with self.assertRaises(AuthError):
            await agen.__anext__()

    @patch("schema.subscriptions._verifier_acces_campagne")
    @patch("schema.subscriptions.require_role")
    async def test_superviseur_autre_campagne_refuse(self, mock_require_role, mock_verifier):
        mock_require_role.return_value = MagicMock(role="SUPERVISEUR", user_id="s-1")
        mock_verifier.side_effect = PermissionError("cette campagne ne vous appartient pas")
        agen = Subscription().progression_updated(info=MagicMock(), campagne_id="c-2")
        with self.assertRaises(PermissionError):
            await agen.__anext__()

    @patch("redis.asyncio.Redis")
    @patch("schema.subscriptions.campagne_client")
    @patch("schema.subscriptions._verifier_acces_campagne")
    @patch("schema.subscriptions.require_role")
    async def test_superviseur_sur_sa_campagne_recoit_la_progression(
        self, mock_require_role, mock_verifier, mock_campagne_client, mock_redis_cls
    ):
        mock_require_role.return_value = MagicMock(role="SUPERVISEUR", user_id="s-1")
        mock_verifier.return_value = None  # accès autorisé

        async def _listen():
            yield {"type": "message", "data": '{"campagne_id": "c-1"}'}

        _mock_redis(_listen, mock_redis_cls)

        prog = MagicMock()
        prog.campagne_id = "c-1"
        prog.total_abonnes = 10
        prog.nb_releves = 4
        prog.nb_en_attente = 6
        prog.pourcentage = 40.0
        mock_campagne_client.get_progression.return_value = prog

        agen = Subscription().progression_updated(info=MagicMock(), campagne_id="c-1")
        result = await agen.__anext__()
        self.assertEqual(result.campagne_id, "c-1")
        self.assertEqual(result.nb_releves, 4)
        mock_verifier.assert_called_once()
