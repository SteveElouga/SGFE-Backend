"""Rejet réel, sur le fil gRPC, d'un appel sans `INTERNAL_GRPC_KEY` valide —
même patron que
`services/notification/notifications/tests/test_grpc_wire_auth_postgres.py`
(voir sa docstring pour la justification complète : les tests existants
d'`AuthServerInterceptor` appellent `intercept_service` directement avec un
`handler_call_details` fabriqué à la main, jamais un vrai serveur gRPC sur le
réseau loopback).

`TransactionTestCase`, pas `TestCase` : le servicer tourne dans un thread
serveur gRPC séparé, avec sa propre connexion Postgres — `TransactionTestCase`
commite réellement les écritures faites depuis le thread de test, seule façon
pour ce thread serveur de les voir.
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

import reporting_service_pb2 as pb  # noqa: E402
import reporting_service_pb2_grpc as pb_grpc  # noqa: E402

from stats.grpc_auth import METADATA_KEY, AuthServerInterceptor  # noqa: E402
from stats.grpc_interceptors import ErrorHandlingInterceptor, IdentityInterceptor  # noqa: E402
from stats.grpc_server import ReportingServiceServicer  # noqa: E402
from stats.models import StatsCampagne  # noqa: E402

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
    """Démarre un vrai `grpc.server`, empilé comme `grpc_server.py::serve()`
    (AuthServerInterceptor → ErrorHandlingInterceptor → IdentityInterceptor),
    et vérifie sur le fil qu'un appel sans la bonne métadonnée
    `x-internal-key` est rejeté avant d'atteindre le servicer."""

    def setUp(self) -> None:
        # `max_workers=1` délibéré — voir le commentaire équivalent côté
        # notification-service : permet à `tearDown` de fermer la connexion
        # Postgres du thread ouvrier avant la destruction de la base de test.
        self.executor = futures.ThreadPoolExecutor(max_workers=1)
        self.server = grpc.server(
            self.executor,
            interceptors=[
                AuthServerInterceptor(_CLE_VALIDE),
                ErrorHandlingInterceptor(),
                IdentityInterceptor(),
            ],
        )
        pb_grpc.add_ReportingServiceServicer_to_server(ReportingServiceServicer(), self.server)
        port = self.server.add_insecure_port("127.0.0.1:0")
        self.server.start()
        self.channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        self.stub = pb_grpc.ReportingServiceStub(self.channel)

    def tearDown(self) -> None:
        self.channel.close()
        self.server.stop(None)
        self.executor.submit(_fermer_connexions_bd).result(timeout=5)
        self.executor.shutdown(wait=True)

    def test_appel_sans_metadonnee_est_refuse_unauthenticated(self) -> None:
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stub.GetStatsCampagne(pb.CampagneIdRequest(campagne_id=str(uuid.uuid4())))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_appel_avec_mauvaise_cle_est_refuse_unauthenticated(self) -> None:
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stub.GetStatsCampagne(
                pb.CampagneIdRequest(campagne_id=str(uuid.uuid4())),
                metadata=((METADATA_KEY, "mauvaise-cle"),),
            )
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_appel_avec_la_bonne_cle_atteint_reellement_la_bd(self) -> None:
        """Contrôle positif : sans lui, un correctif qui casserait toute
        authentification (`AuthServerInterceptor` qui laisserait tout
        passer) ne serait détecté par aucun des deux tests négatifs
        ci-dessus."""
        campagne_id = uuid.uuid4()
        StatsCampagne.objects.create(campagne_id=campagne_id, total_abonnes=10, nb_releves=3)

        response = self.stub.GetStatsCampagne(
            pb.CampagneIdRequest(campagne_id=str(campagne_id)),
            metadata=((METADATA_KEY, _CLE_VALIDE),),
        )
        self.assertEqual(response.total_abonnes, 10)
        self.assertEqual(response.nb_releves, 3)
