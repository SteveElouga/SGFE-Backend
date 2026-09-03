"""Ce qu'un encaissement déclenche en aval — et qui ne se déclenchait pas.

`EnregistrerPaiement` (le caissier vise une facture) portait sept effets aval.
`EnregistrerPaiementAbonne` — que sa propre documentation appelle « le geste
courant », et que l'interface emploie — appelait la couche métier et retournait.
Aucun appel.

Personne ne l'avait vu parce que rien ne le regardait : aucun test n'observait
l'aval de ce RPC, et les deux chemins sont justes séparément — c'est l'un des
deux qui était muet.

Ce fichier regarde. Chaque test nomme la conséquence métier de son absence.
"""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import PaiementServicer
from paiements.models import ModePaiement, SoldeFacture, StatutSolde, SuiviImpaye
from paiements.repositories import SoldeFactureRepository

ABONNE = "abonne-comptoir"


def _contexte() -> MagicMock:
    return MagicMock(spec=grpc.ServicerContext)


def _solde(facture_id: str, montant: float, jours: int = 0, campagne_id: str = "") -> SoldeFacture:
    """Un solde dû, exigible il y a `jours` jours (plus grand = plus ancien)."""
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=ABONNE,
        montant_total=Decimal(str(montant)),
        date_limite_paiement=date.today() - timedelta(days=jours),
        campagne_id=campagne_id,
    )


def _suivi(facture_id: str) -> SuiviImpaye:
    return SuiviImpaye.objects.create(
        facture_id=facture_id,
        abonne_id=ABONNE,
        date_depassement=date.today() - timedelta(days=10),
        etape_actuelle=3,
    )


def _requete_abonne(montant: float) -> pb.EnregistrerPaiementAbonneRequest:
    return pb.EnregistrerPaiementAbonneRequest(
        abonne_id=ABONNE,
        montant=montant,
        date_paiement=date.today().isoformat(),
        mode_paiement=ModePaiement.ESPECES,
        reference_transaction="",
        enregistre_par="caissier",
    )


@patch("paiements.grpc_server.publish_paiement_event")
@patch("paiements.grpc_server.publish_reporting_event")
@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.grpc_server.NotificationServiceClient")
@patch("paiements.grpc_server.FacturationServiceClient")
class TestAvalEncaissementComptoir(TestCase):
    """`EnregistrerPaiementAbonne` — le geste courant, qui ne propageait rien."""

    def _servicer(self) -> PaiementServicer:
        # Construit APRÈS les patches : le servicer instancie ses clients dans
        # son __init__, un servicer créé avant garderait les vrais.
        return PaiementServicer()

    def test_statut_pousse_vers_facturation_pour_chaque_facture_touchee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Sans cela, la facture reste affichée IMPAYÉE après avoir été réglée.

        Deux écrans du même outil finissent en désaccord sur qui doit quoi, et
        c'est ce désaccord qui ruine la confiance dans l'outil.
        """
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(10000), _contexte())

        pousses = {
            c.kwargs["facture_id"]: c.kwargs["statut"]
            for c in mock_fact.return_value.update_statut_facture.call_args_list
        }
        self.assertEqual(pousses, {"juin": StatutSolde.PAYEE, "juillet": StatutSolde.PAYEE})

    def test_suivi_impaye_resolu_pour_chaque_facture_soldee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Sans cela, le cron de relance garde une dette éteinte dans son viseur."""
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        s_juin, s_juillet = _suivi("juin"), _suivi("juillet")
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(10000), _contexte())

        s_juin.refresh_from_db()
        s_juillet.refresh_from_db()
        self.assertEqual(s_juin.resolu_le, date.today())
        self.assertEqual(s_juillet.resolu_le, date.today())

    def test_un_seul_recu_pour_le_versement_avec_le_montant_recu(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Aucun reçu ne partait, alors que tout le mécanisme existe.

        Un seul, pas un par facture : l'abonné a fait un geste, il reçoit un
        justificatif. Et il porte ce qu'il a tendu, pas la part imputée à l'une
        des trois lignes.
        """
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(10000), _contexte())

        mock_notif.return_value.envoyer_recu.assert_called_once()
        kwargs = mock_notif.return_value.envoyer_recu.call_args.kwargs
        self.assertEqual(kwargs["abonne_id"], ABONNE)
        self.assertAlmostEqual(kwargs["montant"], 10000.0)
        self.assertAlmostEqual(kwargs["solde_restant"], 0.0)

    def test_le_recu_annonce_la_dette_totale_restante(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Et non le reste d'une facture parmi plusieurs.

        Un acompte de 4 000 sur 10 000 dus laisse 6 000. Annoncer autre chose sur
        le document que l'abonné garde, c'est lui faire dire l'inverse du vrai.
        """
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(4000), _contexte())

        kwargs = mock_notif.return_value.envoyer_recu.call_args.kwargs
        self.assertAlmostEqual(kwargs["solde_restant"], 6000.0)

    def test_abonne_reactive_quand_il_ne_doit_plus_rien(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """LE défaut le plus grave : un abonné suspendu qui paie restait coupé.

        Sa ligne d'eau restait fermée en base, et le seul recours était une
        intervention manuelle d'un ADMIN. C'est le point qui provoque un litige
        client dès la première semaine.
        """
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(10000), _contexte())

        mock_abonne.return_value.reactiver_abonne.assert_called_once_with(ABONNE)

    def test_pas_de_reactivation_tant_qu_il_reste_une_dette(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """RS-005 : « paiement INTÉGRAL après suspension → ACTIF ».

        « Intégral » qualifie la dette, pas une ligne de la dette. L'abonné règle
        ici la plus ancienne de ses deux factures : il doit encore un mois, l'eau
        ne revient pas. Le code lisait la règle au niveau d'une facture et
        rouvrait la ligne d'eau — perte de recette directe.
        """
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(5000), _contexte())

        # La facture de juin est bien soldée...
        self.assertEqual(SoldeFacture.objects.get(pk="juin").statut, StatutSolde.PAYEE)
        # ...mais l'abonné doit encore juillet.
        mock_abonne.return_value.reactiver_abonne.assert_not_called()
        mock_notif_svc.return_value.envoyer_relance.assert_not_called()

    def test_stats_reporting_par_facture_avec_sa_campagne_et_sa_part(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Le montant encaissé du tableau de bord ignorait ces recettes.

        Et chaque part va à SA campagne : publier le montant reçu en entier sur
        la campagne de la première facture attribuerait toute la recette d'un
        versement à une seule campagne.
        """
        _solde("juin", 5000, jours=60, campagne_id="camp-juin")
        _solde("juillet", 5000, jours=30, campagne_id="camp-juillet")
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(8000), _contexte())

        paiements = [
            (c.kwargs["campagne_id"], c.kwargs["montant_paiement"])
            for c in mock_rep.call_args_list
            if c.kwargs.get("type_update") == "PAIEMENT"
        ]
        self.assertEqual(sorted(paiements), [("camp-juillet", 3000.0), ("camp-juin", 5000.0)])
        self.assertAlmostEqual(sum(m for _, m in paiements), 8000.0)

    def test_evenement_publie_pour_chaque_ecriture(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Sans cela, aucun écran ouvert ne se rafraîchit sur un encaissement."""
        _solde("juin", 5000, jours=60)
        _solde("juillet", 5000, jours=30)
        servicer = self._servicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(10000), _contexte())

        self.assertEqual(mock_pub.call_count, 2)

    def test_relances_suspendues_apres_un_acompte(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """EF-IMP-004. Sans cela, l'abonné qui verse un acompte au comptoir
        continue d'être relancé, et se fait suspendre à J+10 malgré son geste.
        """
        _solde("juin", 5000, jours=60)
        suivi = _suivi("juin")
        servicer = self._servicer()

        with patch("paiements.services.ConfigServiceClient") as mock_config:
            mock_config.return_value.get_delais_impayes.return_value = {"suspension_relances": 5}
            servicer.EnregistrerPaiementAbonne(_requete_abonne(2000), _contexte())

        suivi.refresh_from_db()
        self.assertEqual(suivi.relances_suspendues_jusqu, date.today() + timedelta(days=5))

    def test_versement_sans_dette_ne_propage_rien(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Un abonné qui ne doit rien et verse quand même : tout part en avoir.

        Aucune écriture d'imputation, donc rien à propager — et surtout pas un
        reçu qui prétendrait régler une facture inexistante.
        """
        servicer = self._servicer()

        reponse = servicer.EnregistrerPaiementAbonne(_requete_abonne(3000), _contexte())

        self.assertEqual(len(reponse.paiements), 0)
        self.assertAlmostEqual(reponse.excedent_en_avoir, 3000.0)
        mock_notif.return_value.envoyer_recu.assert_not_called()
        mock_fact.return_value.update_statut_facture.assert_not_called()


@patch("paiements.grpc_server.publish_paiement_event")
@patch("paiements.grpc_server.publish_reporting_event")
@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.grpc_server.NotificationServiceClient")
@patch("paiements.grpc_server.FacturationServiceClient")
class TestAvalCascade(TestCase):
    """`EnregistrerPaiement` — les factures éteintes par débordement.

    Le chemin de la facture nommée propageait bien ses effets, mais **seulement
    pour la facture visée**. Un versement qui dépasse déborde sur les impayés :
    ces factures-là étaient éteintes en base et n'en informaient personne.
    """

    def _requete(self, facture_id: str, montant: float) -> pb.EnregistrerPaiementRequest:
        return pb.EnregistrerPaiementRequest(
            facture_id=facture_id,
            abonne_id=ABONNE,
            montant=montant,
            date_paiement=date.today().isoformat(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )

    def test_la_vieille_facture_eteinte_par_cascade_remonte_son_statut(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Elle restait IMPAYÉE côté Facturation — donc dans le back-office et
        dans le PDF régénéré — alors que la base Paiement la savait soldée.
        """
        _solde("ancienne", 3000, jours=90)
        _solde("aout", 5000, jours=0)
        servicer = PaiementServicer()

        # 8 000 sur une facture de 5 000 : 3 000 débordent sur « ancienne ».
        servicer.EnregistrerPaiement(self._requete("aout", 8000), _contexte())

        pousses = {
            c.kwargs["facture_id"]: c.kwargs["statut"]
            for c in mock_fact.return_value.update_statut_facture.call_args_list
        }
        self.assertEqual(pousses, {"aout": StatutSolde.PAYEE, "ancienne": StatutSolde.PAYEE})

    def test_le_suivi_de_la_vieille_facture_est_resolu(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Son `resolu_le` restait vide : trace comptable perdue, et le cron
        gardait dans son viseur une dette que l'argent avait éteinte.
        """
        _solde("ancienne", 3000, jours=90)
        _solde("aout", 5000, jours=0)
        suivi = _suivi("ancienne")
        servicer = PaiementServicer()

        servicer.EnregistrerPaiement(self._requete("aout", 8000), _contexte())

        suivi.refresh_from_db()
        self.assertEqual(suivi.resolu_le, date.today())

    def test_un_seul_recu_meme_avec_cascade(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Un geste, un justificatif — pas un par ligne touchée."""
        _solde("ancienne", 3000, jours=90)
        _solde("aout", 5000, jours=0)
        servicer = PaiementServicer()

        servicer.EnregistrerPaiement(self._requete("aout", 8000), _contexte())

        mock_notif.return_value.envoyer_recu.assert_called_once()
        self.assertAlmostEqual(mock_notif.return_value.envoyer_recu.call_args.kwargs["montant"], 8000.0)


@patch("paiements.grpc_server.publish_paiement_event")
@patch("paiements.grpc_server.publish_reporting_event")
@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.grpc_server.NotificationServiceClient")
@patch("paiements.grpc_server.FacturationServiceClient")
class TestMessageDeRetablissement(TestCase):
    """Le message « votre ligne d'eau est rétablie » ne part que sur un vrai rétablissement.

    Il partait à chaque facture soldée. Or l'immense majorité des abonnés qui
    règlent leur dette n'ont jamais été coupés : ils recevaient donc l'annonce
    d'un rétablissement qui n'avait pas eu lieu — en plus du reçu, soit deux
    messages pour un geste, annonçant deux montants différents du même versement.
    """

    def test_message_envoye_quand_une_suspension_est_levee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        _solde("juin", 5000, jours=60)
        mock_abonne.return_value.reactiver_abonne.return_value = True
        servicer = PaiementServicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(5000), _contexte())

        mock_notif_svc.return_value.envoyer_relance.assert_called_once()
        self.assertEqual(mock_notif_svc.return_value.envoyer_relance.call_args.kwargs["etape"], 0)

    def test_aucun_message_si_l_abonne_n_etait_pas_suspendu(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Le cas le plus fréquent. Le reçu suffit : il confirme l'argent reçu.

        `reactiver_abonne` rend False — Abonné Service refuse de réactiver un
        abonné déjà ACTIF, et le client traduit ce refus en « rien à faire ».
        """
        _solde("juin", 5000, jours=60)
        mock_abonne.return_value.reactiver_abonne.return_value = False
        servicer = PaiementServicer()

        servicer.EnregistrerPaiementAbonne(_requete_abonne(5000), _contexte())

        mock_notif_svc.return_value.envoyer_relance.assert_not_called()
        # Mais le reçu, lui, part bien : l'abonné a payé, il a droit à sa preuve.
        mock_notif.return_value.envoyer_recu.assert_called_once()
