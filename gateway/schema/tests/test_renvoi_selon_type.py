"""Le renvoi d'un envoi renvoie **le même message**, pas toujours une facture.

`renvoyer_envoi` appelait `renvoyer_facture` quel que soit le type de la ligne.
Le bouton « Renvoyer » de l'écran de suivi s'affiche pourtant sur chaque ligne :
sur un reçu, il envoyait une facture à l'abonné ; sur un avertissement, une
facture aussi. Le seul type pour lequel il faisait ce qu'il annonce était
`FACTURE`.

Rien ne le signalait, parce que l'appel réussissait : un envoi partait, la ligne
passait à ENVOYE, et le toast disait « renvoyé ». C'est l'abonné qui recevait le
mauvais document.

Chaque test de ce fichier échoue sur le code d'avant.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.notification_mutations import _TYPE_TO_ETAPE, NotificationMutations


def _envoi(**kwargs: object) -> MagicMock:
    defaults: dict[str, object] = dict(
        envoi_id="envoi-001",
        abonne_id="abonne-001",
        facture_id="facture-001",
        type_envoi="FACTURE",
        statut="ENVOYE",
        date_envoi="2026-08-02T10:00:00",
        telnyx_message_id="msg-1",
        erreur="",
        paiement_id="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _paiement(**kwargs: object) -> MagicMock:
    defaults: dict[str, object] = dict(paiement_id="paie-1", montant=7500.0, annule=False)
    defaults.update(kwargs)
    return MagicMock(**defaults)


@patch("schema.notification_mutations.require_role")
@patch("schema.notification_mutations.require_auth")
class TestRenvoiSelonType(SimpleTestCase):
    """Un renvoi reprend le chemin d'envoi de son propre type."""

    @patch("schema.notification_mutations.notification_client")
    def test_une_facture_se_renvoie_comme_une_facture(
        self, mock_notif: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        mock_notif.get_envoi.return_value = _envoi(type_envoi="FACTURE")
        mock_notif.renvoyer_facture.return_value = _envoi(envoi_id="envoi-2")

        NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        mock_notif.renvoyer_facture.assert_called_once_with(facture_id="facture-001")

    @patch("schema.notification_mutations.paiement_client")
    @patch("schema.notification_mutations.notification_client")
    def test_un_recu_se_renvoie_comme_un_recu(
        self, mock_notif: MagicMock, mock_paie: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        """Le cœur du défaut : c'est le reçu qui repart, avec ses chiffres."""
        mock_notif.get_envoi.return_value = _envoi(type_envoi="RECU", paiement_id="paie-1")
        mock_paie.list_paiements.return_value = MagicMock(paiements=[_paiement()])
        mock_paie.get_dette_abonne.return_value = MagicMock(total_du=2500.0)
        mock_notif.envoyer_recu.return_value = _envoi(envoi_id="envoi-2", type_envoi="RECU")

        NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        mock_notif.renvoyer_facture.assert_not_called()
        mock_notif.envoyer_recu.assert_called_once_with(
            paiement_id="paie-1",
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=7500.0,
            solde_restant=2500.0,
        )

    @patch("schema.notification_mutations.config_client")
    @patch("schema.notification_mutations.notification_client")
    def test_une_relance_se_renvoie_a_son_etape(
        self, mock_notif: MagicMock, mock_config: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        mock_notif.get_envoi.return_value = _envoi(type_envoi="RELANCE_2")
        mock_config.get_config.return_value = MagicMock(valeur="10")
        mock_notif.envoyer_relance.return_value = _envoi(envoi_id="envoi-2", type_envoi="RELANCE_2")

        NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        mock_notif.renvoyer_facture.assert_not_called()
        self.assertEqual(mock_notif.envoyer_relance.call_args.kwargs["etape"], 2)

    @patch("schema.notification_mutations.config_client")
    @patch("schema.notification_mutations.notification_client")
    def test_un_avertissement_renvoie_le_delai_configure(
        self, mock_notif: MagicMock, mock_config: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        """Le délai de suspension est configurable, et le message l'annonce.

        Le cron des impayés le transmet ; un renvoi manuel doit le lire lui-même,
        sinon l'avertissement renvoyé n'annonce plus aucun délai là où l'original
        en annonçait un — le défaut déjà corrigé sur le gabarit du message,
        réintroduit par le chemin du renvoi.
        """
        mock_notif.get_envoi.return_value = _envoi(type_envoi="AVERTISSEMENT")
        mock_config.get_config.return_value = MagicMock(valeur="7")
        mock_notif.envoyer_relance.return_value = _envoi(envoi_id="envoi-2")

        NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        kwargs = mock_notif.envoyer_relance.call_args.kwargs
        self.assertEqual(kwargs["etape"], 3)
        self.assertEqual(kwargs["jours_avant_suspension"], 7)


@patch("schema.notification_mutations.require_role")
@patch("schema.notification_mutations.require_auth")
class TestRenvoiRecuRefus(SimpleTestCase):
    """Un reçu qui ne vaut plus rien n'est pas renvoyé."""

    @patch("schema.notification_mutations.paiement_client")
    @patch("schema.notification_mutations.notification_client")
    def test_un_versement_annule_ne_repart_pas(
        self, mock_notif: MagicMock, mock_paie: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        """Renvoyer ce reçu affirmerait un encaissement qui n'existe plus.

        Et l'abonné a déjà reçu, à l'annulation, le message qui le lui disait :
        lui renvoyer ensuite le reçu du versement annulé le contredirait
        directement.
        """
        mock_notif.get_envoi.return_value = _envoi(type_envoi="RECU", paiement_id="paie-1")
        mock_paie.list_paiements.return_value = MagicMock(paiements=[_paiement(annule=True)])

        with self.assertRaises(ValueError) as ctx:
            NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        self.assertIn("annulé", str(ctx.exception))
        mock_notif.envoyer_recu.assert_not_called()
        mock_notif.renvoyer_facture.assert_not_called()

    @patch("schema.notification_mutations.paiement_client")
    @patch("schema.notification_mutations.notification_client")
    def test_un_recu_sans_versement_connu_le_dit(
        self, mock_notif: MagicMock, mock_paie: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        """Les reçus émis avant que l'envoi ne garde son versement.

        Ils existent en base : le champ est arrivé après eux. Le renvoi le dit
        au lieu de deviner un versement — ou, comme avant, d'envoyer une facture
        à la place.
        """
        mock_notif.get_envoi.return_value = _envoi(type_envoi="RECU", paiement_id="")

        with self.assertRaises(ValueError):
            NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        mock_notif.renvoyer_facture.assert_not_called()
        mock_paie.list_paiements.assert_not_called()

    @patch("schema.notification_mutations.paiement_client")
    @patch("schema.notification_mutations.notification_client")
    def test_un_solde_indisponible_arrete_le_renvoi(
        self, mock_notif: MagicMock, mock_paie: MagicMock, _auth: MagicMock, _role: MagicMock
    ) -> None:
        """Plutôt qu'un reçu annonçant « reste à payer : 0 ».

        C'est la dégradation silencieuse qui a déjà fait imprimer un faux total
        sur un PDF persisté : `or 0` transformait une valeur inconnue en zéro.
        Ici on refuse d'envoyer.
        """
        import grpc

        mock_notif.get_envoi.return_value = _envoi(type_envoi="RECU", paiement_id="paie-1")
        mock_paie.list_paiements.return_value = MagicMock(paiements=[_paiement()])
        mock_paie.get_dette_abonne.side_effect = grpc.RpcError("injoignable")

        with self.assertRaises(ValueError):
            NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")

        mock_notif.envoyer_recu.assert_not_called()


class TestTableDesEtapes(SimpleTestCase):
    def test_la_table_de_la_gateway_correspond_a_celle_du_service(self) -> None:
        """Les deux tables vivent dans deux processus, sans code partagé.

        Une étape ajoutée d'un seul côté ne produirait aucune erreur : le type
        inconnu retomberait sur le renvoi de facture, et l'abonné recevrait une
        facture au lieu du message attendu. C'est exactement la panne que ce
        fichier corrige, revenue par la porte de derrière.

        La table du service est recopiée ici plutôt qu'importée : le service de
        notification n'est pas installé dans l'environnement de la gateway. La
        recopie est le sujet du test, pas son défaut — c'est elle qu'on vérifie.
        """
        service = {
            "RETABLISSEMENT": 0,
            "RELANCE_1": 1,
            "RELANCE_2": 2,
            "AVERTISSEMENT": 3,
            "SUSPENSION": 4,
            "ANNULATION_PAIEMENT": 5,
        }
        self.assertEqual(_TYPE_TO_ETAPE, service)
