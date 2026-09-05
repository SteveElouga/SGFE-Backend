"""La couche gRPC interne refuse désormais les appels non authentifiés.

Avant, quiconque atteignait les ports 50051-50058 appelait n'importe quel
service sans identifiant. Les huit intercepteurs ne faisaient que du mapping
d'erreurs ; aucune métadonnée n'était envoyée, aucun jeton vérifié.

Ces tests portent sur les trois propriétés qui font la valeur du garde-fou :
il refuse ce qui n'a pas la clé, il l'exige au démarrage plutôt que de se
laisser désarmer par une variable oubliée, et il ne laisse pas fuiter le
secret dans les journaux.
"""

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.test import SimpleTestCase

from paiements.grpc_auth import (
    METADATA_KEY,
    AuthClientInterceptor,
    AuthServerInterceptor,
    CertificatTlsManquant,
    CleInterneManquante,
    _materiel_tls,
    _tls_requis,
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


# ─────────────────────────────────────────────────────────────────────────
# mTLS — repli en clair désormais averti, et GRPC_TLS_REQUIRED (voir
# docs/CONFORMITE_SOC2_OWASP.md §3.3 V12, plan de remédiation item #4).
# ─────────────────────────────────────────────────────────────────────────

_VARIABLES_TLS = ("GRPC_TLS_CA", "GRPC_TLS_CERT", "GRPC_TLS_KEY")


class TlsRequisTest(SimpleTestCase):
    """`_tls_requis()` lit GRPC_TLS_REQUIRED — défaut False, formes usuelles acceptées."""

    def test_defaut_absent_vaut_false(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRPC_TLS_REQUIRED", None)
            self.assertFalse(_tls_requis())

    def test_formes_vraies_acceptees(self) -> None:
        for valeur in ("1", "true", "True", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {"GRPC_TLS_REQUIRED": valeur}):
                self.assertTrue(_tls_requis(), msg=f"valeur={valeur!r}")

    def test_formes_fausses(self) -> None:
        for valeur in ("0", "false", "", "n'importe-quoi"):
            with patch.dict(os.environ, {"GRPC_TLS_REQUIRED": valeur}):
                self.assertFalse(_tls_requis(), msg=f"valeur={valeur!r}")


class MaterielTlsRepliAverstiTest(SimpleTestCase):
    """`GRPC_TLS_REQUIRED` absent/false (comportement historique) : le repli
    en clair reste possible, mais journalise désormais un avertissement
    explicite — c'était la moitié manquante du dispositif signalée par
    docs/CONFORMITE_SOC2_OWASP.md §3.3 V12."""

    def setUp(self) -> None:
        for variable in (*_VARIABLES_TLS, "GRPC_TLS_REQUIRED"):
            os.environ.pop(variable, None)

    def test_aucun_certificat_renvoie_none_et_journalise_un_avertissement(self) -> None:
        with self.assertLogs("paiements.grpc_auth", level="WARNING") as journaux:
            resultat = _materiel_tls()
        self.assertIsNone(resultat)
        trace = "\n".join(journaux.output)
        self.assertIn("repli sur un canal gRPC en clair", trace)
        self.assertIn("GRPC_TLS_REQUIRED=true", trace)

    def test_fichier_illisible_renvoie_none_et_journalise_un_avertissement(self) -> None:
        with patch.dict(os.environ, {"GRPC_TLS_CA": "/chemin/inexistant.crt"}):
            with self.assertLogs("paiements.grpc_auth", level="WARNING") as journaux:
                resultat = _materiel_tls()
        self.assertIsNone(resultat)
        trace = "\n".join(journaux.output)
        self.assertIn("GRPC_TLS_CA", trace)
        self.assertIn("illisible", trace)


class MaterielTlsRequisTest(SimpleTestCase):
    """`GRPC_TLS_REQUIRED=true` : refus explicite de démarrer plutôt que le
    repli en clair — la remédiation elle-même (ASVS V12, plan de remédiation
    item #4)."""

    def setUp(self) -> None:
        for variable in _VARIABLES_TLS:
            os.environ.pop(variable, None)
        self.env_patcher = patch.dict(os.environ, {"GRPC_TLS_REQUIRED": "true"})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def test_aucun_certificat_leve_certificat_tls_manquant(self) -> None:
        with self.assertRaises(CertificatTlsManquant) as ctx:
            _materiel_tls()
        message = str(ctx.exception)
        self.assertIn("GRPC_TLS_REQUIRED=true", message)
        for variable in _VARIABLES_TLS:
            self.assertIn(variable, message)

    def test_fichier_illisible_leve_certificat_tls_manquant(self) -> None:
        with patch.dict(os.environ, {"GRPC_TLS_CA": "/chemin/inexistant.crt"}):
            with self.assertRaises(CertificatTlsManquant) as ctx:
                _materiel_tls()
        message = str(ctx.exception)
        self.assertIn("GRPC_TLS_CA", message)
        self.assertIn("GRPC_TLS_REQUIRED=true", message)

    def test_certificats_complets_et_lisibles_ne_leve_rien(self) -> None:
        """Un certificat effectivement présent et lisible ne doit jamais être
        pénalisé par GRPC_TLS_REQUIRED — seule l'ABSENCE doit faire refuser
        de démarrer."""
        with tempfile.TemporaryDirectory() as dossier:
            chemins = {}
            for variable in _VARIABLES_TLS:
                chemin = Path(dossier) / f"{variable}.pem"
                chemin.write_bytes(b"contenu-de-test")
                chemins[variable] = str(chemin)
            with patch.dict(os.environ, chemins):
                resultat = _materiel_tls()
        self.assertEqual(resultat, (b"contenu-de-test", b"contenu-de-test", b"contenu-de-test"))
