from django.test import TestCase

from abonnes.grpc_server import AbonneServiceServicer
from abonnes.services import ValidationError
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

    def test_list_abonnes_actifs_excludes_suspended(self):
        created = self._create()
        self.servicer.SuspendreAbonne(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        response = self.servicer.ListAbonnesActifs(pb.EmptyRequest(), self.context)
        self.assertEqual(len(response.abonnes), 0)

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

    def test_get_compteur(self):
        created = self._create()
        response = self.servicer.GetCompteur(pb.AbonneIdRequest(abonne_id=created.abonne_id), self.context)
        self.assertEqual(response.numero_compteur, 1)

    def test_update_compteur(self):
        created = self._create(quartier="Ancien", camp=1)
        response = self.servicer.UpdateCompteur(
            pb.UpdateCompteurRequest(abonne_id=created.abonne_id, quartier="Nouveau", camp=2),
            self.context,
        )
        self.assertEqual(response.quartier, "Nouveau")
        self.assertEqual(response.camp, 2)

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
