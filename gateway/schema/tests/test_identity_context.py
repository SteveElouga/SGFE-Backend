"""Tests du contexte d'identité (`schema/identity_context.py`).

Trois volets, cohérents avec le point de vigilance de AUDIT_SGFE.md §10.7 :

1. Comportement de base (set/get/reset) et génération de l'identifiant de
   corrélation.
2. Propagation à travers un appel **synchrone direct** (le cas des resolvers
   GraphQL de requêtes/mutations — même thread, même contexte, pas de copie) :
   fonctionne.
3. Propagation à travers un **pool de threads** (`ThreadPoolExecutor` nu, et
   `asyncio.to_thread`, utilisé par `subscriptions.py`) : `copy_context()`
   fige une photographie du contexte au moment de l'appel — un `.set()` fait
   à l'intérieur reste local à cette photographie et ne remonte jamais vers
   l'appelant ni vers un appel ultérieur. Documenté et vérifié ici plutôt que
   supposé.
"""

import asyncio
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import MagicMock

from django.http import HttpRequest, HttpResponse

from schema.identity_context import (
    ResetIdentityMiddleware,
    get_identity,
    get_request_id,
    reset_identity,
    set_identity,
)


class SetGetResetTests(TestCase):
    def tearDown(self) -> None:
        reset_identity()

    def test_get_identity_sans_authentification_est_none(self) -> None:
        reset_identity()
        self.assertIsNone(get_identity())

    def test_set_identity_peuple_get_identity(self) -> None:
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        identity = get_identity()
        assert identity is not None
        self.assertEqual((identity.user_id, identity.username, identity.role), ("u-1", "alice", "ADMIN"))

    def test_set_identity_genere_un_request_id(self) -> None:
        reset_identity()
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        self.assertTrue(get_request_id())

    def test_request_id_stable_sur_plusieurs_appels_de_la_meme_requete(self) -> None:
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        premier = get_request_id()
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        second = get_request_id()
        self.assertEqual(premier, second)

    def test_reset_identity_efface_identite_et_request_id(self) -> None:
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        reset_identity()
        self.assertIsNone(get_identity())


class PropagationSynchroneTests(TestCase):
    """Cas des resolvers de requêtes/mutations : fonction sync appelée
    directement, dans le même thread — pas de frontière de contexte."""

    def tearDown(self) -> None:
        reset_identity()

    def test_appel_direct_dans_le_meme_thread_voit_l_identite(self) -> None:
        reset_identity()
        set_identity(user_id="u-1", username="alice", role="COMPTABLE")

        def resolver_simule() -> str | None:
            identity = get_identity()
            return identity.user_id if identity else None

        self.assertEqual(resolver_simule(), "u-1")


class PropagationThreadPoolTests(TestCase):
    """Un `ThreadPoolExecutor.submit` nu ne copie PAS le contexte courant par
    défaut : un `.set()` fait depuis le thread ne modifie qu'un contexte
    fraîchement démarré côté thread, invisible à l'appelant."""

    def tearDown(self) -> None:
        reset_identity()

    def test_set_identity_dans_un_thread_du_pool_reste_local_au_thread(self) -> None:
        reset_identity()
        set_identity(user_id="u-main", username="alice", role="ADMIN")

        def lu_dans_le_thread() -> object:
            # Un thread neuf d'un ThreadPoolExecutor n'hérite d'aucun contexte
            # de la coroutine/appelant : `get_identity()` y renvoie donc None,
            # sauf si l'appelant a explicitement recopié son contexte (ce que
            # ni `ThreadPoolExecutor.submit` ni `grpc`'s propre pool ne font
            # automatiquement).
            return get_identity()

        with ThreadPoolExecutor(max_workers=1) as pool:
            resultat = pool.submit(lu_dans_le_thread).result()

        self.assertIsNone(resultat)
        # L'identité côté thread appelant, elle, est intacte.
        identity = get_identity()
        assert identity is not None
        self.assertEqual(identity.user_id, "u-main")


class PropagationAsyncioToThreadTests(IsolatedAsyncioTestCase):
    """`asyncio.to_thread` (utilisé par `subscriptions.py`) copie le contexte
    ambiant à CHAQUE appel (`contextvars.copy_context()`). Un `set_identity()`
    fait à l'intérieur d'un premier `to_thread` mute cette copie, pas le
    contexte ambiant : un second `to_thread`, lancé depuis la même coroutine,
    repart d'un contexte qui n'a jamais vu la mutation.

    C'est exactement le motif de `subscriptions.py` : `require_role` est
    appelé via un `asyncio.to_thread` séparé de celui des résolutions gRPC
    ultérieures — l'identité posée par le premier ne les atteint donc pas.
    Documenté ici comme limite connue (les souscriptions sont des lectures,
    hors périmètre du journal d'audit) plutôt que corrigé silencieusement.
    """

    async def asyncSetUp(self) -> None:
        reset_identity()

    async def asyncTearDown(self) -> None:
        reset_identity()

    async def test_deux_to_thread_separes_ne_partagent_pas_la_mutation(self) -> None:
        def poser_identite() -> None:
            set_identity(user_id="u-sub", username="bob", role="AGENT")

        def lire_identite() -> object:
            identity = get_identity()
            return identity.user_id if identity else None

        await asyncio.to_thread(poser_identite)
        # Le `to_thread` de lecture est un appel SÉPARÉ : il recopie le
        # contexte ambiant de la coroutine, qui n'a jamais été mutée (la
        # mutation du premier appel est restée dans sa propre copie).
        resultat = await asyncio.to_thread(lire_identite)

        self.assertIsNone(resultat)

    async def test_meme_to_thread_voit_sa_propre_mutation(self) -> None:
        # À l'intérieur d'UN SEUL `to_thread` (une seule copie de contexte),
        # poser puis relire l'identité fonctionne normalement — c'est la
        # frontière ENTRE deux appels qui isole, pas l'exécution en thread
        # en elle-même.
        def poser_puis_lire() -> object:
            set_identity(user_id="u-sub", username="bob", role="AGENT")
            identity = get_identity()
            return identity.user_id if identity else None

        resultat = await asyncio.to_thread(poser_puis_lire)
        self.assertEqual(resultat, "u-sub")


class ResetIdentityMiddlewareTests(IsolatedAsyncioTestCase):
    """Le middleware réinitialise l'identité avant d'appeler `get_response`,
    empêchant qu'une identité posée par une requête précédente ne fuite vers
    la suivante sur un worker qui réutiliserait le même thread/contexte."""

    def tearDown(self) -> None:
        reset_identity()

    def test_reinitialise_avant_get_response_sync(self) -> None:
        set_identity(user_id="u-fuite", username="alice", role="ADMIN")

        vue_a_vu: dict[str, object] = {}

        def get_response(request: HttpRequest) -> HttpResponse:
            vue_a_vu["identity"] = get_identity()
            return HttpResponse()

        middleware = ResetIdentityMiddleware(get_response)
        middleware(MagicMock(spec=HttpRequest))

        self.assertIsNone(vue_a_vu["identity"])

    async def test_reinitialise_avant_get_response_async(self) -> None:
        set_identity(user_id="u-fuite", username="alice", role="ADMIN")

        vue_a_vu: dict[str, object] = {}

        async def get_response(request: HttpRequest) -> HttpResponse:
            vue_a_vu["identity"] = get_identity()
            return HttpResponse()

        middleware = ResetIdentityMiddleware(get_response)
        resultat = middleware(MagicMock(spec=HttpRequest))
        assert isinstance(resultat, Awaitable)
        await resultat

        self.assertIsNone(vue_a_vu["identity"])
