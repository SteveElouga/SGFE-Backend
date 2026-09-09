"""Accès concurrent RÉEL au premier accès de `ConfigParam` — singleton
initialisé paresseusement (`get_or_default`/`list_all`, voir `repositories.py`
et `models.py::CONFIG_DEFAULTS`).

Contrairement à `InfosSociete` (un seul enregistrement, pk fixe), `ConfigParam`
a DIX clés par défaut qui n'existent en base qu'après un premier accès —
`get_or_default(cle)` en crée UNE, `list_all()` les crée TOUTES d'un coup si
manquantes. `tests/test_services.py::test_list_all_does_not_duplicate_existing`
vérifie déjà l'idempotence SÉQUENTIELLE (`list_all()` appelé deux fois de
suite), mais jamais deux requêtes qui arrivent EN MÊME TEMPS sur une table
encore vide — exactement le risque de concurrence signalé pour ce service
(deux workers gRPC qui lisent une clé de config au tout premier démarrage).

Gaté par `FORCE_POSTGRES_TESTS`, `TransactionTestCase` (pas `TestCase`) : le
test fait courir deux VRAIS threads avec leur PROPRE connexion Postgres —
sous `TestCase`, tout tournerait dans l'unique transaction non committée du
test, ce qui empêcherait d'observer deux transactions concurrentes réelles se
disputer la même ligne/le même lot de lignes.
"""

from __future__ import annotations

import threading
from typing import Callable
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from parametres.models import CONFIG_DEFAULTS, ConfigParam
from parametres.repositories import ConfigParamRepository

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"


def _executer_dans_un_thread(cible: Callable[[], None], erreurs: list[BaseException]) -> threading.Thread:
    def _wrapper() -> None:
        try:
            cible()
        except BaseException as exc:  # noqa: BLE001 — capturé pour ressortir côté thread principal
            erreurs.append(exc)
        finally:
            # Connexion Django thread-local : sans fermeture explicite, elle
            # reste ouverte après le thread et fait échouer la purge des
            # tables entre tests / la destruction de la base de test en fin
            # de suite (même défaut que documenté dans les autres fichiers
            # `*_postgres.py` de ce lot).
            connections.close_all()

    thread = threading.Thread(target=_wrapper)
    return thread


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class GetOrDefaultAccesConcurrentTests(TransactionTestCase):
    """`get_or_default` repose sur `ConfigParam.objects.get_or_create` — la
    contrainte UNIQUE sur `cle` (models.py) tranche la course, et Django
    documente explicitement que `get_or_create` rattrape l'`IntegrityError`
    du perdant par un second `get()`. Ce test le prouve contre un vrai
    Postgres plutôt que de le supposer."""

    def test_deux_threads_sur_la_meme_cle_absente_ne_creent_qu_une_ligne(self) -> None:
        cle = "delai_paiement_jours"
        barriere = threading.Barrier(2)
        erreurs: list[BaseException] = []

        def _get_or_default() -> None:
            barriere.wait(timeout=5)
            ConfigParamRepository().get_or_default(cle)

        threads = [_executer_dans_un_thread(_get_or_default, erreurs) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(erreurs, [], f"aucun thread ne doit lever : {erreurs}")
        self.assertEqual(ConfigParam.objects.filter(cle=cle).count(), 1)
        self.assertEqual(ConfigParam.objects.get(cle=cle).valeur, CONFIG_DEFAULTS[cle][0])


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class ListAllAccesConcurrentTests(TransactionTestCase):
    """`list_all()` initialise LES DIX clés par défaut d'un coup
    (`bulk_create`) quand la table est vide — un chemin distinct de
    `get_or_default`, avec son propre risque de concurrence au premier accès
    (voir `repositories.py::ConfigParamRepository.list_all`)."""

    def test_deux_threads_sur_une_table_vide_ne_levent_pas_et_ne_dupliquent_rien(self) -> None:
        """Deux requêtes `list_all()` simultanées sur une table VIDE (premier
        accès concurrent, ex. deux workers gRPC qui démarrent en même temps)
        ne doivent ni lever, ni laisser une table avec des doublons ou une
        clé manquante."""
        self.assertEqual(ConfigParam.objects.count(), 0, "précondition : table vide")
        barriere = threading.Barrier(2)
        erreurs: list[BaseException] = []

        def _list_all() -> None:
            barriere.wait(timeout=5)
            ConfigParamRepository().list_all()

        threads = [_executer_dans_un_thread(_list_all, erreurs) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(erreurs, [], f"aucun thread ne doit lever : {erreurs}")
        self.assertEqual(ConfigParam.objects.count(), len(CONFIG_DEFAULTS))
        for cle, (valeur_defaut, _description) in CONFIG_DEFAULTS.items():
            self.assertEqual(ConfigParam.objects.get(cle=cle).valeur, valeur_defaut)
