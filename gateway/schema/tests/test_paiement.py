"""Tests des resolvers GraphQL du Paiement Service (gateway).

Régression ANO-022 : aucun test n'existait pour ce domaine.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.context import AuthError
from schema.identity_context import reset_identity
from schema.paiement_mutations import PaiementMutations
from schema.paiement_queries import PaiementQueries


def _solde_response(**kwargs: object) -> MagicMock:
    defaults = dict(
        facture_id="facture-001", montant_total=25000.0, montant_paye=10000.0, solde_restant=15000.0, statut="PARTIELLE"
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _paiement_response(**kwargs: object) -> MagicMock:
    defaults = dict(
        paiement_id="paiement-001",
        facture_id="facture-001",
        montant=10000.0,
        date_paiement="2026-07-02",
        mode_paiement="MOBILE_MONEY",
        reference_transaction="TXN123",
        created_at="2026-07-02T10:00:00",
        enregistre_par="user-001",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _suivi_response(**kwargs: object) -> MagicMock:
    defaults = dict(
        suivi_id="suivi-001",
        facture_id="facture-001",
        abonne_id="abonne-001",
        date_depassement="2026-07-06",
        etape_actuelle=1,
        resolu_le="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _avoir_response(**kwargs: object) -> MagicMock:
    mouvement = MagicMock(
        montant=100.0,
        type_mouvement="RECTIFICATION",
        motif="Geste commercial",
        facture_id="",
        cree_par="user-001",
        created_at="2026-07-02T10:00:00",
    )
    defaults = dict(abonne_id="abonne-001", montant=100.0, mouvements=[mouvement])
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestPaiementQueries(SimpleTestCase):
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_solde_facture(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_solde.return_value = _solde_response()
        info = MagicMock()
        result = PaiementQueries().solde_facture(info, facture_id="facture-001")
        self.assertEqual(result.solde_restant, 15000.0)
        self.assertEqual(result.statut, "PARTIELLE")
        # Verrou de rôle : ADMIN/COMPTABLE exactement — un test qui ne vérifie
        # que le résultat ne détecterait pas un rôle silencieusement élargi ou
        # restreint (voir la régression historique sur `impayes`, ci-dessous).
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_avec_filtre(
        self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock, mock_auth_client: MagicMock
    ) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response()])
        mock_auth_client.get_user.return_value = MagicMock(username="bah.comptable")
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].montant, 10000.0)
        self.assertEqual(result[0].operateur, "bah.comptable")
        mock_auth_client.get_user.assert_called_once_with("user-001")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_operateur_non_resolu_replie_sur_identifiant(
        self,
        mock_role: MagicMock,
        mock_auth: MagicMock,
        mock_client: MagicMock,
        mock_auth_client: MagicMock,
    ) -> None:
        """Auth Service indisponible : la liste des paiements reste servie (dégradation gracieuse)."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response(enregistre_par="user-999")])
        mock_auth_client.get_user.side_effect = RuntimeError("Auth Service indisponible")
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(result[0].operateur, "Utilisateur user-999")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_sans_limit_offset_appelle_le_client_comme_avant(
        self,
        mock_role: MagicMock,
        mock_auth: MagicMock,
        mock_client: MagicMock,
        mock_auth_client: MagicMock,
    ) -> None:
        """Non-régression explicite : `limit`/`offset` omis, l'appel au client
        gRPC reste identique à ce qu'il était avant leur introduction."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response()])
        mock_auth_client.get_user.return_value = MagicMock(username="bah.comptable")
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(len(result), 1)
        mock_client.list_paiements.assert_called_once_with(facture_id="facture-001", abonne_id="")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_avec_pagination_transmet_limit_offset(
        self,
        mock_role: MagicMock,
        mock_auth: MagicMock,
        mock_client: MagicMock,
        mock_auth_client: MagicMock,
    ) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[])
        info = MagicMock()
        PaiementQueries().paiements(info, facture_id="facture-001", limit=10, offset=5)
        mock_client.list_paiements.assert_called_once_with(facture_id="facture-001", abonne_id="", limit=10, offset=5)
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_count(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.count_paiements.return_value = 15
        info = MagicMock()
        result = PaiementQueries().paiements_count(info, facture_id="facture-001")
        self.assertEqual(result, 15)
        mock_client.count_paiements.assert_called_once_with(facture_id="facture-001", abonne_id="")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_resout_chaque_operateur_une_seule_fois(
        self,
        mock_role: MagicMock,
        mock_auth: MagicMock,
        mock_client: MagicMock,
        mock_auth_client: MagicMock,
    ) -> None:
        """Plusieurs paiements du même opérateur ne déclenchent qu'un seul appel gRPC."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(
            paiements=[
                _paiement_response(paiement_id="p1", enregistre_par="user-001"),
                _paiement_response(paiement_id="p2", enregistre_par="user-001"),
            ]
        )
        mock_auth_client.get_user.return_value = MagicMock(username="bah.comptable")
        info = MagicMock()
        result = PaiementQueries().paiements(info)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_auth_client.get_user.call_count, 1)
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_impayes(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_impayes.return_value = MagicMock(impayes=[_solde_response(statut="IMPAYEE")])
        info = MagicMock()
        result = PaiementQueries().impayes(info)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].statut, "IMPAYEE")
        # Verrou de rôle explicite : ANO historique, un rôle silencieusement
        # élargi (ex. AGENT/SUPERVISEUR ajoutés par erreur) ou restreint sur
        # `/impayes` doit casser ce test, pas passer inaperçu.
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_suivi_impaye(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_suivi_impaye.return_value = _suivi_response(etape_actuelle=2)
        info = MagicMock()
        result = PaiementQueries().suivi_impaye(info, facture_id="facture-001")
        self.assertEqual(result.etape_actuelle, 2)
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_avoir_abonne(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_avoir_abonne.return_value = _avoir_response(montant=100.0)
        info = MagicMock()
        result = PaiementQueries().avoir_abonne(info, abonne_id="abonne-001")
        self.assertEqual(result.montant, 100.0)
        self.assertEqual(len(result.mouvements), 1)
        self.assertEqual(result.mouvements[0].type_mouvement, "RECTIFICATION")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_dette_abonne(self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock) -> None:
        """N'avait aucun test — seul resolver du fichier dans ce cas."""
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_dette_abonne.return_value = MagicMock(
            total_du=45000.0, nb_factures=3, plus_ancienne_echeance="2026-05-05"
        )
        info = MagicMock()
        result = PaiementQueries().dette_abonne(info, abonne_id="abonne-001")
        self.assertEqual(result.total_du, 45000.0)
        self.assertEqual(result.nb_factures, 3)
        self.assertEqual(result.plus_ancienne_echeance, "2026-05-05")
        mock_client.get_dette_abonne.assert_called_once_with("abonne-001", "")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_dette_abonne_echeance_absente_devient_none(
        self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock
    ) -> None:
        """Abonné à jour : `plus_ancienne_echeance` vide côté gRPC devient
        `None` côté GraphQL (jamais une chaîne vide qui laisserait croire à
        une échéance dépassée non renseignée)."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_dette_abonne.return_value = MagicMock(total_du=0.0, nb_factures=0, plus_ancienne_echeance="")
        info = MagicMock()
        result = PaiementQueries().dette_abonne(info, abonne_id="abonne-001", hors_facture_id="facture-001")
        self.assertIsNone(result.plus_ancienne_echeance)
        mock_client.get_dette_abonne.assert_called_once_with("abonne-001", "facture-001")


class TestPaiementMutations(SimpleTestCase):
    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement(self, mock_role: MagicMock, mock_client: MagicMock) -> None:
        mock_role.return_value = MagicMock(role="COMPTABLE", user_id="user-001", username="bah.comptable")
        mock_client.enregistrer_paiement.return_value = _paiement_response()
        info = MagicMock()
        result = PaiementMutations().enregistrer_paiement(
            info,
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=10000.0,
            date_paiement="2026-07-02",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="TXN123",
        )
        self.assertEqual(result.montant, 10000.0)
        # L'opérateur affiché est le nom d'utilisateur courant, déjà connu du
        # payload JWT — aucun aller-retour vers Auth Service pour la mutation.
        self.assertEqual(result.operateur, "bah.comptable")
        mock_client.enregistrer_paiement.assert_called_once_with(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=10000.0,
            date_paiement="2026-07-02",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="TXN123",
            enregistre_par="user-001",
        )
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_crediter_avoir(self, mock_role: MagicMock, mock_client: MagicMock) -> None:
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-001", username="admin")
        mock_client.crediter_avoir.return_value = _avoir_response(montant=250.0)
        info = MagicMock()
        result = PaiementMutations().crediter_avoir(
            info, abonne_id="abonne-001", montant=250.0, motif="Erreur d'index corrigée"
        )
        self.assertEqual(result.montant, 250.0)
        mock_client.crediter_avoir.assert_called_once_with(
            abonne_id="abonne-001",
            montant=250.0,
            motif="Erreur d'index corrigée",
            cree_par="user-001",
        )
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_annuler_paiement(self, mock_role: MagicMock, mock_client: MagicMock) -> None:
        """N'avait aucun test — seule mutation d'annulation du domaine."""
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-001", username="admin")
        mock_client.annuler_paiement.return_value = _paiement_response(montant=10000.0)
        info = MagicMock()
        result = PaiementMutations().annuler_paiement(info, paiement_id="paiement-001", motif="Erreur de saisie")
        self.assertEqual(result.montant, 10000.0)
        self.assertEqual(result.operateur, "admin")
        mock_client.annuler_paiement.assert_called_once_with(
            paiement_id="paiement-001",
            motif="Erreur de saisie",
            annule_par="user-001",
        )
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_auth")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement_abonne(
        self, mock_role: MagicMock, mock_auth: MagicMock, mock_client: MagicMock
    ) -> None:
        """N'avait aucun test — encaissement au niveau abonné (imputation la
        plus ancienne d'abord), pas au niveau d'une facture nommée."""
        mock_auth.return_value = MagicMock(user_id="user-001", username="bah.comptable")
        mock_client.enregistrer_paiement_abonne.return_value = MagicMock(
            paiements=[_paiement_response(facture_id="facture-001"), _paiement_response(facture_id="facture-002")],
            excedent_en_avoir=500.0,
        )
        info = MagicMock()
        result = PaiementMutations().enregistrer_paiement_abonne(
            info,
            abonne_id="abonne-001",
            montant=20000.0,
            date_paiement="2026-07-02",
            mode_paiement="ESPECES",
        )
        self.assertEqual(len(result.paiements), 2)
        self.assertEqual(result.excedent_en_avoir, 500.0)
        mock_client.enregistrer_paiement_abonne.assert_called_once_with(
            abonne_id="abonne-001",
            montant=20000.0,
            date_paiement="2026-07-02",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement_reference_optionnelle(self, mock_role: MagicMock, mock_client: MagicMock) -> None:
        """Le paiement ESPECES n'exige pas de reference_transaction (défaut '')."""
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-002")
        mock_client.enregistrer_paiement.return_value = _paiement_response(
            mode_paiement="ESPECES", reference_transaction=""
        )
        info = MagicMock()
        result = PaiementMutations().enregistrer_paiement(
            info,
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=25000.0,
            date_paiement="2026-07-02",
            mode_paiement="ESPECES",
        )
        self.assertEqual(result.mode_paiement, "ESPECES")
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")


class TestPaiementFrontiereDeRole(SimpleTestCase):
    """Contrairement aux classes ci-dessus (qui mockent `require_role` pour
    isoler la logique métier des resolvers), ces tests laissent le VRAI
    `require_role`/`require_auth` s'exécuter — seul `auth_client.validate_token`
    (la frontière gRPC) est mocké. Ils vérifient que le verrou de rôle est
    réellement appliqué, pas seulement invoqué avec les bons arguments : un
    rôle non autorisé doit être rejeté de bout en bout, pas seulement observé
    par un mock complaisant.

    `impayes` est explicitement ciblé (voir CLAUDE.md racine : régression de
    contrôle de rôle déjà survenue sur ce champ)."""

    def tearDown(self) -> None:
        # `require_auth` pose l'identité dans un ContextVar global — éviter
        # qu'elle ne fuite vers un test suivant (voir test_context.py).
        reset_identity()

    def _info_avec_token(self) -> MagicMock:
        request = MagicMock()
        request.headers.get.return_value = "Bearer tok-valide"
        info = MagicMock()
        info.context = {"request": request}
        return info

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.context.auth_client")
    def test_impayes_refuse_un_role_non_autorise(
        self, mock_auth_client: MagicMock, mock_paiement_client: MagicMock
    ) -> None:
        for role_refuse in ("AGENT", "SUPERVISEUR"):
            with self.subTest(role=role_refuse):
                mock_auth_client.validate_token.return_value = MagicMock(role=role_refuse, user_id="u-1", username="x")
                with self.assertRaises(AuthError) as ctx:
                    PaiementQueries().impayes(self._info_avec_token())
                self.assertEqual(ctx.exception.code, "PERMISSION_DENIED")
                mock_paiement_client.list_impayes.assert_not_called()

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.context.auth_client")
    def test_impayes_autorise_admin_et_comptable(
        self, mock_auth_client: MagicMock, mock_paiement_client: MagicMock
    ) -> None:
        for role_autorise in ("ADMIN", "COMPTABLE"):
            with self.subTest(role=role_autorise):
                mock_auth_client.validate_token.return_value = MagicMock(
                    role=role_autorise, user_id="u-1", username="x"
                )
                mock_paiement_client.list_impayes.return_value = MagicMock(impayes=[])
                # Ne doit pas lever.
                PaiementQueries().impayes(self._info_avec_token())

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.context.auth_client")
    def test_enregistrer_paiement_refuse_un_role_non_autorise(
        self, mock_auth_client: MagicMock, mock_paiement_client: MagicMock
    ) -> None:
        mock_auth_client.validate_token.return_value = MagicMock(role="AGENT", user_id="u-1", username="x")
        with self.assertRaises(AuthError) as ctx:
            PaiementMutations().enregistrer_paiement(
                self._info_avec_token(),
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=1000.0,
                date_paiement="2026-07-02",
                mode_paiement="ESPECES",
            )
        self.assertEqual(ctx.exception.code, "PERMISSION_DENIED")
        mock_paiement_client.enregistrer_paiement.assert_not_called()
