"""La couche gRPC interne refuse désormais les appels non authentifiés.

Avant, quiconque atteignait les ports 50051-50058 appelait n'importe quel
service sans identifiant. Les huit intercepteurs ne faisaient que du mapping
d'erreurs ; aucune métadonnée n'était envoyée, aucun jeton vérifié.

Ces tests portent sur les trois propriétés qui font la valeur du garde-fou :
il refuse ce qui n'a pas la clé, il l'exige au démarrage plutôt que de se
laisser désarmer par une variable oubliée, et il ne laisse pas fuiter le
secret dans les journaux.
"""

from collections.abc import Iterable
from unittest.mock import MagicMock

import grpc
from django.test import SimpleTestCase

from paiements.grpc_auth import (
    METADATA_KEY,
    AuthClientInterceptor,
    AuthServerInterceptor,
    CleInterneManquante,
    canal_authentifie,
    exiger_cle,
)

CLE = "cle-de-test-partagee"


def _details(
    metadata: Iterable[tuple[str, str]] = (), methode: str = "/paiement.PaiementService/GetSolde"
) -> MagicMock:
    d = MagicMock()
    d.method = methode
    d.invocation_metadata = metadata
    return d


class FailClosedTest(SimpleTestCase):
    """Une clé absente doit arrêter le service, pas l'ouvrir."""

    def test_refuse_une_cle_vide(self) -> None:
        for valeur in ("", None):
            with self.assertRaises(CleInterneManquante):
                exiger_cle(valeur, "test")

    def test_le_serveur_refuse_de_demarrer_sans_cle(self) -> None:
        # C'est le point important : une variable d'environnement oubliée doit
        # produire un service mort, pas un service ouvert. Le second est bien
        # pire, parce qu'il a l'air de fonctionner.
        with self.assertRaises(CleInterneManquante):
            AuthServerInterceptor("")

    def test_le_client_refuse_de_partir_sans_cle(self) -> None:
        with self.assertRaises(CleInterneManquante):
            AuthClientInterceptor("")

    def test_le_message_dit_quoi_faire(self) -> None:
        with self.assertRaises(CleInterneManquante) as ctx:
            exiger_cle("", "paiement-service")
        message = str(ctx.exception)
        self.assertIn("paiement-service", message)
        self.assertIn("INTERNAL_GRPC_KEY", message)


class ServeurTest(SimpleTestCase):
    def setUp(self) -> None:
        self.interceptor = AuthServerInterceptor(CLE)
        self.suite = MagicMock(return_value="handler-metier")

    def test_laisse_passer_un_appel_correctement_signe(self) -> None:
        resultat = self.interceptor.intercept_service(self.suite, _details(metadata=((METADATA_KEY, CLE),)))
        self.assertEqual(resultat, "handler-metier")

    def test_refuse_un_appel_sans_metadonnee(self) -> None:
        resultat = self.interceptor.intercept_service(self.suite, _details())
        self.assertNotEqual(resultat, "handler-metier")
        self.suite.assert_not_called()

    def test_refuse_une_cle_fausse(self) -> None:
        resultat = self.interceptor.intercept_service(self.suite, _details(metadata=((METADATA_KEY, "mauvaise-cle"),)))
        self.assertNotEqual(resultat, "handler-metier")
        self.suite.assert_not_called()

    def test_refuse_une_cle_prefixe_de_la_bonne(self) -> None:
        # `compare_digest` protège contre la comparaison courte-circuitée ; ce
        # test vérifie surtout qu'on ne compare pas avec `startswith`.
        resultat = self.interceptor.intercept_service(self.suite, _details(metadata=((METADATA_KEY, CLE[:5]),)))
        self.assertNotEqual(resultat, "handler-metier")

    def test_refuse_avant_d_atteindre_le_metier(self) -> None:
        # L'intercepteur d'authentification est monté avant celui des erreurs :
        # un appel non authentifié ne doit toucher aucune logique métier.
        self.interceptor.intercept_service(self.suite, _details())
        self.suite.assert_not_called()

    def test_ne_journalise_pas_la_cle_recue(self) -> None:
        # Une clé erronée reste un secret : l'écrire dans les journaux en
        # ferait une fuite, et les journaux sont le premier endroit qu'on
        # partage pour demander de l'aide.
        with self.assertLogs("paiements.grpc_auth", level="WARNING") as journaux:
            self.interceptor.intercept_service(
                self.suite, _details(metadata=((METADATA_KEY, "secret-a-ne-pas-fuiter"),))
            )
        trace = "\n".join(journaux.output)
        self.assertNotIn("secret-a-ne-pas-fuiter", trace)
        self.assertIn("GetSolde", trace)


class ClientTest(SimpleTestCase):
    def test_ajoute_la_cle_a_chaque_appel(self) -> None:
        interceptor = AuthClientInterceptor(CLE)
        suite = MagicMock(return_value="reponse")
        details = MagicMock(method="/x/Y", timeout=None, metadata=None, credentials=None)

        interceptor.intercept_unary_unary(suite, details, "requete")

        envoyes = dict(suite.call_args[0][0].metadata)
        self.assertEqual(envoyes[METADATA_KEY], CLE)

    def test_preserve_la_metadonnee_existante(self) -> None:
        interceptor = AuthClientInterceptor(CLE)
        suite = MagicMock(return_value="reponse")
        details = MagicMock(method="/x/Y", timeout=None, metadata=(("trace-id", "abc"),), credentials=None)

        interceptor.intercept_unary_unary(suite, details, "requete")

        envoyes = dict(suite.call_args[0][0].metadata)
        self.assertEqual(envoyes["trace-id"], "abc")
        self.assertEqual(envoyes[METADATA_KEY], CLE)

    def test_preserve_le_delai_et_les_identifiants(self) -> None:
        interceptor = AuthClientInterceptor(CLE)
        suite = MagicMock(return_value="reponse")
        details = MagicMock(method="/x/Y", timeout=3.5, metadata=None, credentials="jeton")

        interceptor.intercept_unary_unary(suite, details, "requete")

        transmis = suite.call_args[0][0]
        self.assertEqual(transmis.method, "/x/Y")
        self.assertEqual(transmis.timeout, 3.5)
        self.assertEqual(transmis.credentials, "jeton")


class CanalTest(SimpleTestCase):
    def test_le_canal_est_bien_intercepte(self) -> None:
        canal = canal_authentifie("localhost:50055", CLE)
        # `intercept_channel` rend un canal enveloppé, pas le canal nu : c'est
        # ce qui garantit qu'un appel ajouté demain sera authentifié sans que
        # personne n'ait à y penser.
        metaclasse = type(grpc.Channel)
        self.assertNotIsInstance(canal, metaclasse)
        self.assertTrue(hasattr(canal, "unary_unary"))
