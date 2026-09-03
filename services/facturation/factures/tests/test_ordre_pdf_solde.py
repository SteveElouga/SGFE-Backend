"""Le PDF est rendu APRÈS l'initialisation du solde, jamais avant.

C'est `initialiser_solde` qui impute l'avoir de l'abonné sur la facture. Généré
avant lui, le PDF interrogeait un solde qui n'existait pas encore :
`_lire_avoir_impute` échouait, dégradait à zéro, et le document **persisté**
annonçait le total plein.

Le message WhatsApp part ensuite et relit `GetSolde.avoir_impute`, non nul cette
fois. Pour un abonné avec un avoir et sans autre facture impayée, l'envoi portait
donc deux totaux différents : le message en déduisait l'avoir, la pièce jointe
non. Et le PDF n'est pas régénéré tant que l'abonné n'a pas de dette, donc le
document restait faux.

Le chemin de régénération faisait déjà les deux dans le bon ordre, avec le
commentaire qui l'explique. Deux chemins pour une même chose, l'un juste, l'autre
non, et personne ne les avait comparés — c'est cet écart que ce fichier verrouille.
"""

import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from django.test import TestCase

from factures.models import Facture, Tarif
from factures.pdf_generator import InfosSociete
from factures.services import ReleveData
from factures.tests.helpers import service_avec_clients_mockes


class TestOrdrePdfEtSolde(TestCase):
    def setUp(self) -> None:
        Tarif.objects.create(prix_m3=Decimal("500"), is_active=True, date_effet=datetime.date(2026, 1, 1))
        self.svc = service_avec_clients_mockes()
        self.releve = ReleveData(
            abonne_id="ab-1",
            ancien_index=100.0,
            nouveau_index=143.0,
            consommation=43.0,
            date_releve="2026-07-01",
        )

    def _generer(self, journal: list[str]) -> None:
        """Génère une facture en enregistrant l'ordre des deux gestes."""
        self.svc._paiement_client.initialiser_solde.side_effect = (  # type: ignore[attr-defined]
            lambda **_: journal.append("solde")
        )

        with patch.object(
            self.svc,
            "_regenerer_et_persister",
            side_effect=lambda *a, **k: journal.append("pdf"),
        ):
            self.svc.generer_factures(
                campagne_id="camp-1",
                releves=[self.releve],
                delai_paiement_jours=5,
                societe=InfosSociete(nom="Hydro CI"),
                envoyer_whatsapp_auto=False,
            )

    def test_le_solde_est_initialise_avant_le_rendu_du_pdf(self) -> None:
        journal: list[str] = []
        self._generer(journal)
        self.assertEqual(journal, ["solde", "pdf"])

    def test_le_message_whatsapp_part_apres_le_pdf(self) -> None:
        """L'ordre complet : solde, puis PDF, puis message.

        Le message annonce le total que la pièce jointe porte. Les deux lisent
        donc le même état, et dans cet ordre-là seulement.
        """
        journal: list[str] = []
        self.svc._paiement_client.initialiser_solde.side_effect = (  # type: ignore[attr-defined]
            lambda **_: journal.append("solde")
        )
        self.svc._notification_client.envoyer_facture.side_effect = (  # type: ignore[attr-defined]
            lambda **_: journal.append("message")
        )

        with patch.object(
            self.svc,
            "_regenerer_et_persister",
            side_effect=lambda *a, **k: journal.append("pdf"),
        ):
            self.svc.generer_factures(
                campagne_id="camp-1",
                releves=[self.releve],
                delai_paiement_jours=5,
                societe=InfosSociete(nom="Hydro CI"),
                envoyer_whatsapp_auto=True,
            )

        self.assertEqual(journal, ["solde", "pdf", "message"])

    def test_la_facture_existe_meme_si_le_rendu_du_pdf_echoue(self) -> None:
        """Le rendu est sorti de la boucle de réessai — elle protège la
        numérotation, pas WeasyPrint. Un rendu qui échoue ne doit pas empêcher la
        facture d'exister : `get_pdf_bytes` régénère à la demande.
        """
        self.svc._paiement_client.initialiser_solde.return_value = None  # type: ignore[attr-defined]
        with patch.object(self.svc, "_regenerer_et_persister", side_effect=RuntimeError("WeasyPrint KO")):
            with self.assertRaises(RuntimeError):
                self.svc.generer_factures(
                    campagne_id="camp-1",
                    releves=[self.releve],
                    delai_paiement_jours=5,
                    societe=InfosSociete(nom="Hydro CI"),
                    envoyer_whatsapp_auto=False,
                )

        # La facture ET son solde ont été créés avant l'échec du rendu.
        self.assertEqual(Facture.objects.count(), 1)
        self.svc._paiement_client.initialiser_solde.assert_called_once()  # type: ignore[attr-defined]

    def test_l_avoir_impute_est_lisible_au_moment_du_rendu(self) -> None:
        """Le cœur du défaut : la valeur que le PDF va réellement imprimer.

        Le mock reproduit la réalité plutôt que de la contourner : `get_solde` ne
        rend un solde **qu'après** que `initialiser_solde` a été appelé, et
        dégrade à `None` avant — comme le vrai client, dont l'appel échoue sur une
        facture dont le solde n'existe pas encore.

        Le PDF rendu trop tôt lisait donc zéro. C'est cette valeur-là qui était
        imprimée sur le document que l'abonné garde, pendant que le message
        WhatsApp, parti après, annonçait l'avoir déduit.
        """
        solde_cree = False

        def _initialiser(**_: Any) -> None:
            nonlocal solde_cree
            solde_cree = True

        def _get_solde(_facture_id: str) -> dict[str, Any] | None:
            if not solde_cree:
                return None  # le client dégrade : le solde n'existe pas encore
            return {
                "montant_total": 21500.0,
                "montant_paye": 3000.0,
                "solde_restant": 18500.0,
                "statut": "PARTIELLE",
                "avoir_impute": 3000.0,
            }

        self.svc._paiement_client.initialiser_solde.side_effect = _initialiser  # type: ignore[attr-defined]
        self.svc._paiement_client.get_solde.side_effect = _get_solde  # type: ignore[attr-defined]
        vus: list[Decimal] = []

        def _capturer(facture: Facture, **_: Any) -> None:
            vus.append(self.svc._lire_avoir_impute(str(facture.id)))

        with patch.object(self.svc, "_regenerer_et_persister", side_effect=_capturer):
            self.svc.generer_factures(
                campagne_id="camp-1",
                releves=[self.releve],
                delai_paiement_jours=5,
                societe=InfosSociete(nom="Hydro CI"),
                envoyer_whatsapp_auto=False,
            )

        # 3 000, et non 0 : le rendu voit l'avoir que l'initialisation a imputé.
        self.assertEqual(vus, [Decimal("3000.0")])
