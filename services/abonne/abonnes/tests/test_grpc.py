import json
from unittest.mock import Mock

from django.test import TestCase

from abonnes.grpc_server import AbonneServiceServicer
from abonnes.services import AbonneService, ValidationError
from proto import abonne_service_pb2 as pb


class FakeContext:
    def abort(self, code, details):
        raise AssertionError("context.abort() ne devrait pas être appelé directement par le servicer")


class AbonneServiceServicerTests(TestCase):
    def setUp(self):
        self.servicer = AbonneServiceServicer()
        self.context = FakeContext()

    def _create(self, **overrides):
        defaults = dict(
            nom="Doe",
            prenom="John",
            telephone_whatsapp="+24100000000",
            adresse="Quartier X",
            numero_compteur=1,
            quartier="Centre",
            camp=1,
            index_initial=0.0,
            date_pose="2024-01-01",
        )
        defaults.update(overrides)
        return self.servicer.CreateAbonne(pb.CreateAbonneRequest(**defaults), self.context)

    def test_create_abonne_returns_response_with_compteur(self):
        response = self._create()
        self.assertEqual(response.numero_abonne, "AB-0001")
        self.assertEqual(response.statut, "ACTIF")
        self.assertEqual(response.compteur.numero_compteur, 1)

    def test_get_abonne(self):
        created = self._create()
        response = self.servicer.GetAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(response.numero_abonne, "AB-0001")

    def test_get_abonne_not_found_raises(self):
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetAbonne(pb.AbonneIdRequest(abonne_id="00000000-0000-0000-0000-000000000000"), self.context)

    def test_list_abonnes(self):
        self._create()
        self._create(numero_compteur=2)
        response = self.servicer.ListAbonnes(pb.ListAbonnesRequest(), self.context)
        self.assertEqual(len(response.abonnes), 2)

    def test_list_abonnes_sans_pagination_total_egale_la_liste_rendue(self):
        # Non-régression : `limit`/`offset` omis (champs proto3 `optional` non
        # définis) doit préserver le comportement historique — tout est rendu.
        self._create()
        self._create(numero_compteur=2)
        self._create(numero_compteur=3)
        response = self.servicer.ListAbonnes(pb.ListAbonnesRequest(), self.context)
        self.assertEqual(len(response.abonnes), 3)
        self.assertEqual(response.total, 3)

    def test_list_abonnes_avec_pagination_tronque_et_decale(self):
        for i in range(1, 6):
            self._create(numero_compteur=i)
        response = self.servicer.ListAbonnes(pb.ListAbonnesRequest(limit=2, offset=1), self.context)
        self.assertEqual([a.numero_abonne for a in response.abonnes], ["AB-0002", "AB-0003"])
        # Le total porte sur l'ensemble filtré, pas sur la seule page rendue.
        self.assertEqual(response.total, 5)

    def test_list_abonnes_pagination_hors_limites_renvoie_liste_vide_pas_une_erreur(self):
        self._create()
        response = self.servicer.ListAbonnes(pb.ListAbonnesRequest(limit=10, offset=100), self.context)
        self.assertEqual(len(response.abonnes), 0)
        self.assertEqual(response.total, 1)

    def test_list_abonnes_filtre_statut_et_pagination_combines(self):
        # La pagination doit porter sur le résultat FILTRÉ par statut, pas sur
        # la table brute.
        self._create(numero_compteur=1)
        suspendu = self._create(numero_compteur=2)
        self._create(numero_compteur=3)
        self.servicer.SuspendreAbonne(pb.AbonneIdRequest(abonne_id=suspendu.abonne_id), self.context)
        response = self.servicer.ListAbonnes(pb.ListAbonnesRequest(statut="ACTIF", limit=1, offset=0), self.context)
        self.assertEqual(len(response.abonnes), 1)
        self.assertEqual(response.total, 2)

    def test_list_abonnes_actifs_excludes_suspended(self):
        created = self._create()
        self.servicer.SuspendreAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        response = self.servicer.ListAbonnesActifs(pb.EmptyRequest(), self.context)
        self.assertEqual(len(response.abonnes), 0)

    def test_list_zones_agrege_par_quartier_camp(self):
        # 2 abonnés Centre·1, 1 abonné Plateau·3, 1 abonné Centre·2.
        self._create(numero_compteur=1, quartier="Centre", camp=1)
        self._create(numero_compteur=2, quartier="Centre", camp=1)
        self._create(numero_compteur=3, quartier="Plateau", camp=3)
        self._create(numero_compteur=4, quartier="Centre", camp=2)
        response = self.servicer.ListZones(pb.EmptyRequest(), self.context)
        zones = {(z.quartier, z.camp): z.nb_abonnes for z in response.zones}
        self.assertEqual(zones[("Centre", 1)], 2)
        self.assertEqual(zones[("Centre", 2)], 1)
        self.assertEqual(zones[("Plateau", 3)], 1)

    def test_list_zones_exclut_abonne_suspendu(self):
        self._create(numero_compteur=1, quartier="Centre", camp=1)
        suspendu = self._create(numero_compteur=2, quartier="Centre", camp=1)
        self.servicer.SuspendreAbonne(pb.AbonneIdRequest(abonne_id=suspendu.abonne_id), self.context)
        response = self.servicer.ListZones(pb.EmptyRequest(), self.context)
        zones = {(z.quartier, z.camp): z.nb_abonnes for z in response.zones}
        self.assertEqual(zones[("Centre", 1)], 1)

    def test_update_abonne(self):
        created = self._create()
        response = self.servicer.UpdateAbonne(
            pb.UpdateAbonneRequest(
                abonne_id=created.abonne_id, nom="Smith", prenom="", telephone_whatsapp="", adresse=""
            ),
            self.context,
        )
        self.assertEqual(response.nom, "Smith")

    def test_suspendre_et_reactiver(self):
        created = self._create()
        suspendu = self.servicer.SuspendreAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(suspendu.statut, "SUSPENDU")
        reactive = self.servicer.ReactiverAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(reactive.statut, "ACTIF")

    def test_resilier_abonne(self):
        created = self._create()
        resilie = self.servicer.ResilierAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(resilie.statut, "RESILIE")

    def test_resilier_abonne_deja_resilie_raises(self):
        created = self._create()
        self.servicer.ResilierAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        with self.assertRaises(ValidationError):
            self.servicer.ResilierAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)

    def test_anonymiser_abonne_actif_raises(self):
        created = self._create()
        with self.assertRaises(ValidationError):
            self.servicer.AnonymiserAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)

    def test_anonymiser_abonne_resilie(self):
        created = self._create()
        self.servicer.ResilierAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        response = self.servicer.AnonymiserAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(response.nom, AbonneService.NOM_ANONYMISE)
        self.assertEqual(response.prenom, AbonneService.PRENOM_ANONYMISE)
        self.assertEqual(response.telephone_whatsapp, AbonneService.TELEPHONE_ANONYMISE)
        self.assertEqual(response.adresse, AbonneService.ADRESSE_ANONYMISEE)
        self.assertEqual(response.statut, "RESILIE")
        self.assertEqual(response.abonne_id, created.abonne_id)
        self.assertEqual(response.numero_abonne, created.numero_abonne)

    def test_get_compteur(self):
        created = self._create()
        response = self.servicer.GetCompteur(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(response.numero_compteur, 1)

    def test_create_abonne_transporte_la_position(self):
        created = self._create(position="3e maison à gauche")
        self.assertEqual(created.compteur.position, "3e maison à gauche")

    def test_update_compteur(self):
        created = self._create(quartier="Ancien", camp=1)
        response = self.servicer.UpdateCompteur(
            pb.UpdateCompteurRequest(abonne_id=created.abonne_id, quartier="Nouveau", camp=2),
            self.context,
        )
        self.assertEqual(response.quartier, "Nouveau")
        self.assertEqual(response.camp, 2)

    def test_update_compteur_position(self):
        created = self._create(position="Ancienne")
        response = self.servicer.UpdateCompteur(
            pb.UpdateCompteurRequest(abonne_id=created.abonne_id, position="Nouvelle position"),
            self.context,
        )
        self.assertEqual(response.position, "Nouvelle position")

    def test_remplacer_compteur(self):
        created = self._create()
        response = self.servicer.RemplacerCompteur(
            pb.RemplacerCompteurRequest(
                abonne_id=created.abonne_id,
                index_fermeture=100,
                nouveau_numero_compteur=2,
                nouveau_quartier="Nouveau",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            ),
            self.context,
        )
        self.assertEqual(response.numero_compteur, 2)

    def test_remplacer_compteur_transporte_la_nouvelle_position(self):
        created = self._create()
        response = self.servicer.RemplacerCompteur(
            pb.RemplacerCompteurRequest(
                abonne_id=created.abonne_id,
                index_fermeture=100,
                nouveau_numero_compteur=2,
                nouveau_quartier="Nouveau",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
                nouvelle_position="Près du portail bleu",
            ),
            self.context,
        )
        self.assertEqual(response.position, "Près du portail bleu")

    def test_remplacer_compteur_motif_persiste_et_ressort_dans_historique(self):
        created = self._create()
        self.servicer.RemplacerCompteur(
            pb.RemplacerCompteurRequest(
                abonne_id=created.abonne_id,
                index_fermeture=100,
                nouveau_numero_compteur=2,
                nouveau_quartier="Nouveau",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
                motif="Compteur défectueux",
            ),
            self.context,
        )
        historique = self.servicer.GetHistoriqueCompteur(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(len(historique.historique), 1)
        self.assertEqual(historique.historique[0].motif, "Compteur défectueux")

    def test_remplacer_compteur_invalid_index_raises(self):
        created = self._create(index_initial=50.0)
        with self.assertRaises(ValidationError):
            self.servicer.RemplacerCompteur(
                pb.RemplacerCompteurRequest(
                    abonne_id=created.abonne_id,
                    index_fermeture=10,
                    nouveau_numero_compteur=2,
                    nouveau_quartier="Q",
                    nouveau_camp=2,
                    nouvel_index_initial=0,
                    date_remplacement="2024-06-01",
                ),
                self.context,
            )

    def test_exporter_donnees_abonne_renvoie_un_json_structure(self):
        created = self._create()
        # Les 4 clients gRPC sortants de l'export sont mockés : ce test ne
        # doit pas dépendre de campagne/facturation/paiement/notification
        # réellement joignables (voir abonnes/tests/test_export.py pour la
        # couverture détaillée de la dégradation gracieuse).
        self.servicer.export_service._campagne_client = Mock(list_releves_abonne=Mock(return_value=[]))
        self.servicer.export_service._facturation_client = Mock(list_factures_abonne=Mock(return_value=[]))
        self.servicer.export_service._paiement_client = Mock(list_paiements_abonne=Mock(return_value=[]))
        self.servicer.export_service._notification_client = Mock(list_envois_abonne=Mock(return_value=[]))

        response = self.servicer.ExporterDonneesAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)

        donnees = json.loads(response.json_export)
        self.assertEqual(donnees["abonne_id"], created.abonne_id)
        self.assertEqual(donnees["identite"]["nom"], "Doe")
        self.assertTrue(donnees["releves"]["disponible"])
        self.assertFalse(donnees["diffusions_whatsapp"]["disponible"])

    def test_exporter_donnees_abonne_introuvable_raises(self):
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.ExporterDonneesAbonne(
                pb.AbonneIdRequest(abonne_id="00000000-0000-0000-0000-000000000000"), self.context
            )
