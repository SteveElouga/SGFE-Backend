"""Tests des sérialiseurs protobuf — campagne_to_proto / releve_to_proto."""

from django.test import SimpleTestCase

from campagnes.models import Campagne, Releve, StatutCampagne, StatutReleve
from campagnes.serializers import campagne_to_proto, releve_to_proto


class ReleveToProtoTests(SimpleTestCase):
    def test_date_releve_datetime_est_convertie_en_iso(self):
        import datetime

        campagne = Campagne(nom="C", periode_mois=7, periode_annee=2026, statut=StatutCampagne.EN_COURS)
        releve = Releve(
            campagne=campagne,
            abonne_id="abonne-1",
            ancien_index=100.0,
            nouveau_index=150.0,
            consommation=50.0,
            date_releve=datetime.datetime(2026, 7, 15, 10, 0, 0),
            statut=StatutReleve.RELEVE,
        )
        response = releve_to_proto(releve)
        self.assertEqual(response.date_releve, "2026-07-15T10:00:00")

    def test_date_releve_str_brute_nest_pas_cassee(self):
        """Régression ANO-018 : releve_to_proto doit passer par le même
        helper _to_iso que campagne_to_proto (correctif d54133a) — un
        Releve dont date_releve est encore une chaîne brute (juste après
        un .create(), avant tout refresh_from_db()) ne doit jamais lever
        d'AttributeError ('str' object has no attribute 'isoformat')."""
        campagne = Campagne(nom="C", periode_mois=7, periode_annee=2026, statut=StatutCampagne.EN_COURS)
        releve = Releve(
            campagne=campagne,
            abonne_id="abonne-1",
            ancien_index=100.0,
            nouveau_index=150.0,
            consommation=50.0,
            date_releve="2026-07-15T10:00:00",
            statut=StatutReleve.RELEVE,
        )
        response = releve_to_proto(releve)
        self.assertEqual(response.date_releve, "2026-07-15T10:00:00")

    def test_date_releve_absente_retourne_chaine_vide(self):
        campagne = Campagne(nom="C", periode_mois=7, periode_annee=2026, statut=StatutCampagne.EN_COURS)
        releve = Releve(
            campagne=campagne,
            abonne_id="abonne-1",
            ancien_index=100.0,
            date_releve=None,
            statut=StatutReleve.A_RELEVER,
        )
        response = releve_to_proto(releve)
        self.assertEqual(response.date_releve, "")


class CampagneToProtoTests(SimpleTestCase):
    def test_campagne_to_proto_serialise_created_by(self):
        """Régression bug SUPERVISEUR : created_by doit exister dans
        CampagneResponse et être peuplé par le sérialiseur. Sinon
        _verifier_acces_campagne (gateway) lit campagne.created_by sur une
        réponse gRPC qui ne porte pas le champ → AttributeError, et tout accès
        du SUPERVISEUR à une campagne plante. Ce test échoue franchement si le
        champ proto ou sa sérialisation manque."""
        campagne = Campagne(
            nom="C",
            periode_mois=7,
            periode_annee=2026,
            statut=StatutCampagne.EN_COURS,
            numero_mobile_money="",
            generer_factures_auto=False,
            envoyer_whatsapp_auto=False,
            created_by="user-A",
        )
        response = campagne_to_proto(campagne)
        self.assertEqual(response.created_by, "user-A")

    def test_campagne_to_proto_created_by_absent_donne_chaine_vide(self):
        """Un created_by non renseigné se sérialise en chaîne vide (défaut
        proto3), jamais en None — pas de plantage côté gateway."""
        campagne = Campagne(
            nom="C",
            periode_mois=7,
            periode_annee=2026,
            statut=StatutCampagne.EN_COURS,
            created_by="",
        )
        response = campagne_to_proto(campagne)
        self.assertEqual(response.created_by, "")
