"""Couverture d'intégration (vrai Postgres, `TestCase`) des 3 RPC mutants
exclus du pattern `AuditLog` du projet — voir AUDIT_SGFE.md §10.7 et
Notification Service : `CreerDiffusion`, `RevoquerToken`, `RevoquerTousTokens`.

Ces RPC ont un comportement de mutation réel qui n'était pas exercé au niveau
BD par `test_grpc.py`/`test_diffusion.py` (qui couvrent le cas nominal isolé
de chaque RPC) :

- `CreerDiffusion` : le téléphone persisté par ligne `DiffusionEnvoi` doit
  être EXACTEMENT celui résolu via l'appel gRPC à Abonné Service (pas un
  champ vide ou un résidu), y compris quand certaines résolutions échouent
  (dégradation par abonné) ou échouent toutes.
- `RevoquerToken` / `RevoquerTousTokens` : effet de cascade sur `ValiderToken`
  (un token révoqué doit être relu comme invalide depuis la BD, pas depuis un
  objet Python en mémoire) et isolation (revoke d'un token n'affecte jamais
  un autre token actif, y compris pour le même abonné).
"""

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase

_proto_path = str(Path(settings.BASE_DIR) / "proto")
if _proto_path not in sys.path:
    sys.path.insert(0, _proto_path)

import notification_service_pb2 as pb  # noqa: E402

from notifications.grpc_server import NotificationServiceServicer  # noqa: E402
from notifications.models import DiffusionEnvoi, TokenAcces  # noqa: E402


def _abonne_mock(abonne_id: str, telephone: str) -> MagicMock:
    mock = MagicMock()
    mock.abonne_id = abonne_id
    mock.telephone_whatsapp = telephone
    return mock


class TestCreerDiffusionResolutionTelephoneIntegration(TestCase):
    """`CreerDiffusion` résout le téléphone de chaque abonné via gRPC
    (Abonné Service, mocké à la frontière réseau) et le persiste réellement
    en base — le reste (statut, agrégation des compteurs) est déjà couvert
    ailleurs, ce fichier vérifie la VALEUR persistée, pas seulement le compte.
    """

    @patch("notifications.services.abonne_client")
    def test_le_telephone_persiste_est_celui_resolu_par_grpc(self, mock_abonne: MagicMock) -> None:
        aid1, aid2 = str(uuid.uuid4()), str(uuid.uuid4())
        telephones = {aid1: "+237699111111", aid2: "+237699222222"}
        mock_abonne.get_abonne.side_effect = lambda aid: _abonne_mock(aid, telephones[aid])

        servicer = NotificationServiceServicer()
        request = pb.CreerDiffusionRequest(message="Coupure prévue", abonne_ids=[aid1, aid2], created_by="admin-1")
        response = servicer.CreerDiffusion(request, MagicMock())

        self.assertEqual(response.nb_total, 2)
        ligne1 = DiffusionEnvoi.objects.get(diffusion_id=response.diffusion_id, abonne_id=aid1)
        ligne2 = DiffusionEnvoi.objects.get(diffusion_id=response.diffusion_id, abonne_id=aid2)
        # Relu depuis la BD (pas depuis l'objet Python créé en mémoire) :
        # `telephone` est chiffré au repos (EncryptedCharField) — ce test
        # vérifie le round-trip complet chiffrement/déchiffrement, pas
        # seulement la valeur en mémoire juste après création.
        self.assertEqual(ligne1.telephone, "+237699111111")
        self.assertEqual(ligne2.telephone, "+237699222222")

    @patch("notifications.services.abonne_client")
    def test_degradation_par_abonne_au_niveau_rpc_avec_verification_bd(self, mock_abonne: MagicMock) -> None:
        """Un abonné sur trois injoignable : la diffusion est créée avec
        EXACTEMENT les deux lignes résolues, jamais une ligne pour l'abonné en
        échec — vérifié par lecture BD directe, pas seulement par le compteur
        `nb_total` de la réponse."""
        import grpc

        aid_ok1, aid_ko, aid_ok2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

        def _get_abonne(aid: str) -> MagicMock:
            if aid == aid_ko:
                raise grpc.RpcError("Abonné Service injoignable")
            return _abonne_mock(aid, "+237699000000")

        mock_abonne.get_abonne.side_effect = _get_abonne

        servicer = NotificationServiceServicer()
        request = pb.CreerDiffusionRequest(
            message="Annonce", abonne_ids=[aid_ok1, aid_ko, aid_ok2], created_by="admin-2"
        )
        response = servicer.CreerDiffusion(request, MagicMock())

        self.assertEqual(response.nb_total, 2)
        lignes = DiffusionEnvoi.objects.filter(diffusion_id=response.diffusion_id)
        self.assertEqual(lignes.count(), 2)
        self.assertEqual(set(lignes.values_list("abonne_id", flat=True)), {aid_ok1, aid_ok2})
        self.assertFalse(lignes.filter(abonne_id=aid_ko).exists())

    @patch("notifications.services.abonne_client")
    def test_tous_les_abonnes_injoignables_cree_une_diffusion_sans_ligne(self, mock_abonne: MagicMock) -> None:
        """Dégradation totale : la diffusion existe (traçabilité de la
        tentative), mais aucune ligne — vérifié en base, pas de RpcError
        remontée à l'appelant."""
        import grpc

        mock_abonne.get_abonne.side_effect = grpc.RpcError("indisponible")

        servicer = NotificationServiceServicer()
        request = pb.CreerDiffusionRequest(
            message="Annonce", abonne_ids=[str(uuid.uuid4()), str(uuid.uuid4())], created_by="admin-3"
        )
        context = MagicMock()
        response = servicer.CreerDiffusion(request, context)

        context.abort.assert_not_called()
        self.assertEqual(response.nb_total, 0)
        self.assertEqual(DiffusionEnvoi.objects.filter(diffusion_id=response.diffusion_id).count(), 0)


class TestRevoquerTokenCascadeIntegration(TestCase):
    """`RevoquerToken` : la révocation doit être visible par `ValiderToken`
    (relecture BD réelle) et rester isolée aux autres tokens actifs."""

    def test_revoquer_puis_valider_le_meme_token_le_voit_invalide(self) -> None:
        """Boucle complète : RevoquerToken puis ValiderToken sur le MÊME
        token — la mutation d'un RPC doit être visible par l'autre RPC via
        une relecture réelle en base, pas un objet Python partagé en mémoire."""
        token = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )
        servicer = NotificationServiceServicer()

        valider_avant = servicer.ValiderToken(pb.ValiderTokenRequest(token=str(token.token)), MagicMock())
        self.assertTrue(valider_avant.is_valid)

        revoquer = servicer.RevoquerToken(pb.TokenIdRequest(token_id=str(token.id)), MagicMock())
        self.assertTrue(revoquer.success)

        valider_apres = servicer.ValiderToken(pb.ValiderTokenRequest(token=str(token.token)), MagicMock())
        self.assertFalse(valider_apres.is_valid)

    def test_revoquer_un_token_n_affecte_pas_un_autre_token_actif_du_meme_abonne(self) -> None:
        """Isolation : un abonné peut avoir plusieurs tokens (factures
        différentes) — en révoquer un ne doit jamais toucher les autres."""
        abonne_id = str(uuid.uuid4())
        token_a = TokenAcces.objects.create(
            abonne_id=abonne_id, facture_id="fact-a", date_expiration=date.today() + timedelta(days=20)
        )
        token_b = TokenAcces.objects.create(
            abonne_id=abonne_id, facture_id="fact-b", date_expiration=date.today() + timedelta(days=20)
        )

        servicer = NotificationServiceServicer()
        servicer.RevoquerToken(pb.TokenIdRequest(token_id=str(token_a.id)), MagicMock())

        token_a.refresh_from_db()
        token_b.refresh_from_db()
        self.assertFalse(token_a.is_active)
        self.assertTrue(token_b.is_active)

        # Confirmé aussi via le RPC de lecture, pas seulement l'ORM.
        reponse_b = servicer.ValiderToken(pb.ValiderTokenRequest(token=str(token_b.token)), MagicMock())
        self.assertTrue(reponse_b.is_valid)

    def test_revoquer_deux_fois_le_meme_token_reste_sans_erreur(self) -> None:
        """Une révocation répétée (double clic, retry client) ne doit jamais
        lever — le token est déjà inactif, l'opération est idempotente."""
        token = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )
        servicer = NotificationServiceServicer()

        premiere = servicer.RevoquerToken(pb.TokenIdRequest(token_id=str(token.id)), MagicMock())
        seconde = servicer.RevoquerToken(pb.TokenIdRequest(token_id=str(token.id)), MagicMock())

        self.assertTrue(premiere.success)
        self.assertTrue(seconde.success)
        token.refresh_from_db()
        self.assertFalse(token.is_active)


class TestRevoquerTousTokensCascadeIntegration(TestCase):
    """`RevoquerTousTokens` : révocation de masse — cascade sur plusieurs
    abonnés distincts, effet visible par `ValiderToken`, idempotence."""

    def _creer_token_actif(self, abonne_id: str | None = None) -> TokenAcces:
        return TokenAcces.objects.create(
            abonne_id=abonne_id or str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )

    def test_revoque_les_tokens_de_plusieurs_abonnes_distincts(self) -> None:
        """La révocation de masse ne se limite pas à un seul abonné — c'est
        un UPDATE sur TOUTE la table, vérifié ici sur 3 abonnés différents."""
        tokens = [self._creer_token_actif() for _ in range(3)]

        servicer = NotificationServiceServicer()
        response = servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())

        self.assertEqual(response.count, 3)
        for token in tokens:
            token.refresh_from_db()
            self.assertFalse(token.is_active)

    def test_effet_visible_par_valider_token_pour_chaque_abonne(self) -> None:
        """Cascade bout en bout : après la révocation de masse, `ValiderToken`
        (RPC de lecture distinct) doit voir chaque token comme invalide —
        relecture réelle en base, pas un artefact de l'objet ayant servi à
        la mutation."""
        token_1 = self._creer_token_actif()
        token_2 = self._creer_token_actif()

        servicer = NotificationServiceServicer()
        servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())

        for token in (token_1, token_2):
            reponse = servicer.ValiderToken(pb.ValiderTokenRequest(token=str(token.token)), MagicMock())
            self.assertFalse(reponse.is_valid)

    def test_appel_repete_est_idempotent_et_ne_revoque_rien_de_plus(self) -> None:
        """Un second appel (retry, double clic admin) ne doit rien casser :
        déjà tout révoqué, le compte retombe à 0, aucune exception."""
        self._creer_token_actif()
        self._creer_token_actif()
        servicer = NotificationServiceServicer()

        premier = servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())
        second = servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())

        self.assertEqual(premier.count, 2)
        self.assertEqual(second.count, 0)
        self.assertEqual(TokenAcces.objects.filter(is_active=True).count(), 0)

    def test_ne_revoque_pas_les_tokens_deja_inactifs_deux_fois_dans_le_compte(self) -> None:
        """Un token déjà révoqué avant l'appel ne doit pas gonfler le
        compteur retourné — seuls les tokens réellement passés actif→inactif
        par CET appel sont comptés (`UPDATE ... WHERE is_active`)."""
        deja_revoque = self._creer_token_actif()
        deja_revoque.is_active = False
        deja_revoque.save()
        actif = self._creer_token_actif()

        servicer = NotificationServiceServicer()
        response = servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())

        self.assertEqual(response.count, 1)
        actif.refresh_from_db()
        self.assertFalse(actif.is_active)
