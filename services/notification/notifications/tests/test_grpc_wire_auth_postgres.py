"""Rejet réel, sur le fil gRPC, d'un appel sans `INTERNAL_GRPC_KEY` valide.

`notifications/tests/test_fields.py`/les tests d'`AuthServerInterceptor`
ailleurs dans le dépôt (voir `paiements/tests/test_grpc_auth.py`, copié à
l'identique côté client/serveur) vérifient `intercept_service` en lui
passant directement un `handler_call_details` fabriqué à la main — utile
pour la logique de comparaison de clé, mais ça ne démarre jamais de VRAI
serveur gRPC ni n'envoie de VRAIE requête sur le réseau loopback. Ce fichier
fait tourner le servicer réel (`NotificationServiceServicer`, exactement
celui démarré par `grpc_server.py::serve()`, mêmes intercepteurs empilés
dans le même ordre) sur un port éphémère, et appelle À TRAVERS le
canal — c'est le seul endroit du service qui prouve qu'un appelant sans clé,
ou avec la mauvaise clé, se fait effectivement couper au niveau transport
avant d'atteindre la moindre ligne de logique métier.

`TransactionTestCase`, pas `TestCase` : le servicer tourne dans un thread
serveur gRPC séparé, avec sa PROPRE connexion Postgres (`django.setup()`
n'est pas nécessaire ici — la connexion par défaut du process suffit, mais
elle est bien accédée depuis un thread distinct de celui du test). Sous
`TestCase`, la transaction ouverte par le test ne serait jamais visible
depuis la connexion utilisée par le thread serveur — `TransactionTestCase`
commite réellement les écritures, seule façon pour le thread serveur de les
voir.
"""

from __future__ import annotations

import sys
import uuid
from concurrent import futures
from pathlib import Path
from unittest import skipUnless

import grpc
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

_proto_path = str(Path(settings.BASE_DIR) / "proto")
if _proto_path not in sys.path:
    sys.path.insert(0, _proto_path)

import notification_service_pb2 as pb  # noqa: E402
import notification_service_pb2_grpc as pb_grpc  # noqa: E402

from notifications.grpc_auth import METADATA_KEY, AuthServerInterceptor  # noqa: E402
from notifications.grpc_interceptors import ErrorHandlingInterceptor, IdentityInterceptor  # noqa: E402
from notifications.grpc_server import NotificationServiceServicer  # noqa: E402
from notifications.models import Envoi, StatutEnvoi, TypeEnvoi  # noqa: E402

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"

_CLE_VALIDE = "cle-de-test-integration-wire"


def _fermer_connexions_bd() -> None:
    """Exécuté DANS le thread ouvrier du serveur gRPC (voir `tearDown`) pour
    fermer sa connexion Postgres thread-local avant l'arrêt du test."""
    from django.db import connections

    connections.close_all()


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class RejetInternalGrpcKeyReelTests(TransactionTestCase):
    """Démarre un vrai `grpc.server`, exactement empilé comme
    `grpc_server.py::serve()` (AuthServerInterceptor → ErrorHandlingInterceptor
    → IdentityInterceptor), et vérifie sur le fil qu'un appel sans la bonne
    métadonnée `x-internal-key` est rejeté avant d'atteindre le servicer."""

    def setUp(self) -> None:
        # `max_workers=1` délibéré : un seul thread ouvrier traite tous les
        # appels de ce test, ce qui permet à `tearDown` de lui faire fermer
        # SA connexion Postgres (thread-local, voir `_fermer_connexions_bd`)
        # avant l'arrêt du serveur — sans ça, la connexion ouverte par le
        # thread ouvrier reste vivante après le test et fait échouer la
        # destruction de la base de test en fin de suite ("database ... is
        # being accessed by other users").
        self.executor = futures.ThreadPoolExecutor(max_workers=1)
        self.server = grpc.server(
            self.executor,
            interceptors=[
                AuthServerInterceptor(_CLE_VALIDE),
                ErrorHandlingInterceptor(),
                IdentityInterceptor(),
            ],
        )
        pb_grpc.add_NotificationServiceServicer_to_server(NotificationServiceServicer(), self.server)
        port = self.server.add_insecure_port("127.0.0.1:0")
        self.server.start()
        self.channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        self.stub = pb_grpc.NotificationServiceStub(self.channel)

    def tearDown(self) -> None:
        self.channel.close()
        self.server.stop(None)
        # Voir le commentaire de `setUp` : ferme la connexion Postgres
        # ouverte par l'unique thread ouvrier, depuis CE thread précisément
        # (les connexions Django sont thread-local — `close_all()` appelé
        # depuis le thread du test n'aurait aucun effet sur celle du thread
        # ouvrier).
        self.executor.submit(_fermer_connexions_bd).result(timeout=5)
        self.executor.shutdown(wait=True)

    def test_appel_sans_metadonnee_est_refuse_unauthenticated(self) -> None:
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stub.GetEnvoi(pb.EnvoiIdRequest(envoi_id=str(uuid.uuid4())))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_appel_avec_mauvaise_cle_est_refuse_unauthenticated(self) -> None:
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stub.GetEnvoi(
                pb.EnvoiIdRequest(envoi_id=str(uuid.uuid4())),
                metadata=((METADATA_KEY, "mauvaise-cle"),),
            )
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_appel_refuse_n_atteint_jamais_la_logique_metier(self) -> None:
        """Un appel `GetEnvoi` sur un id existant, mais sans la bonne clé,
        doit échouer AVANT toute lecture BD — vérifié indirectement : la
        levée `UNAUTHENTICATED` elle-même (pas `NOT_FOUND` ni une réponse
        valide) prouve que le servicer n'a pas été exécuté."""
        envoi = Envoi.objects.create(
            facture_id=str(uuid.uuid4()),
            abonne_id=str(uuid.uuid4()),
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699000001",
            statut=StatutEnvoi.ENVOYE,
        )
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stub.GetEnvoi(pb.EnvoiIdRequest(envoi_id=str(envoi.id)))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_appel_avec_la_bonne_cle_atteint_reellement_la_bd(self) -> None:
        """Contrôle positif : la même requête, avec la bonne clé, doit
        réussir et renvoyer la vraie ligne — sans ce test, un correctif qui
        casserait toute authentification (ex. `AuthServerInterceptor` qui
        laisserait tout passer) ne serait pas détecté par les deux tests
        négatifs ci-dessus."""
        envoi = Envoi.objects.create(
            facture_id=str(uuid.uuid4()),
            abonne_id=str(uuid.uuid4()),
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699000001",
            statut=StatutEnvoi.ENVOYE,
        )
        response = self.stub.GetEnvoi(
            pb.EnvoiIdRequest(envoi_id=str(envoi.id)),
            metadata=((METADATA_KEY, _CLE_VALIDE),),
        )
        self.assertEqual(response.envoi_id, str(envoi.id))
        self.assertEqual(response.statut, StatutEnvoi.ENVOYE)
