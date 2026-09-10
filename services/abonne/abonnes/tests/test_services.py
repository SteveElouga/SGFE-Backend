from decimal import Decimal
from typing import Any

from django.db import IntegrityError
from django.test import TestCase

from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur
from abonnes.services import AbonneService, CompteurService, NumerotationService, ValidationError


def _create_abonne(service: AbonneService, **overrides: Any) -> Abonne:
    defaults: dict[str, Any] = dict(
        nom="Doe",
        prenom="John",
        telephone_whatsapp="+24100000000",
        adresse="Quartier X",
        numero_compteur=1,
        quartier="Centre",
        camp=1,
        index_initial=0,
        date_pose="2024-01-01",
    )
    defaults.update(overrides)
    return service.create_abonne(**defaults)


class NumerotationServiceTests(TestCase):
    def test_first_numero_is_ab_0001(self) -> None:
        self.assertEqual(NumerotationService().generer(), "AB-0001")

    def test_increments_sequentially(self) -> None:
        service = AbonneService()
        _create_abonne(service)
        self.assertEqual(NumerotationService().generer(), "AB-0002")
        _create_abonne(service, numero_compteur=2)
        self.assertEqual(NumerotationService().generer(), "AB-0003")


class AbonneServiceTests(TestCase):
    def setUp(self) -> None:
        self.service = AbonneService()

    def test_create_abonne_creates_compteur(self) -> None:
        abonne = _create_abonne(self.service)
        compteur = CompteurService().get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.statut, StatutCompteur.ACTIF)
        self.assertEqual(compteur.numero_compteur, 1)

    def test_create_abonne_position_par_defaut_vide(self) -> None:
        abonne = _create_abonne(self.service)
        compteur = CompteurService().get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.position, "")

    def test_create_abonne_avec_position(self) -> None:
        abonne = _create_abonne(self.service, position="3e maison à gauche")
        compteur = CompteurService().get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.position, "3e maison à gauche")

    def test_create_abonne_assigns_sequential_numero(self) -> None:
        a1 = _create_abonne(self.service)
        a2 = _create_abonne(self.service, numero_compteur=2)
        self.assertEqual(a1.numero_abonne, "AB-0001")
        self.assertEqual(a2.numero_abonne, "AB-0002")

    def test_update_abonne_partial(self) -> None:
        abonne = _create_abonne(self.service)
        updated = self.service.update_abonne(
            str(abonne.id), nom="", prenom="", telephone_whatsapp="+24199999999", adresse=""
        )
        self.assertEqual(updated.nom, "Doe")
        self.assertEqual(updated.telephone_whatsapp, "+24199999999")

    def test_suspendre_then_reactiver(self) -> None:
        abonne = _create_abonne(self.service)
        suspendu = self.service.suspendre_abonne(str(abonne.id))
        self.assertEqual(suspendu.statut, StatutAbonne.SUSPENDU)
        reactive = self.service.reactiver_abonne(str(abonne.id))
        self.assertEqual(reactive.statut, StatutAbonne.ACTIF)

    def test_suspendre_abonne_deja_suspendu_raises(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.suspendre_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.suspendre_abonne(str(abonne.id))

    def test_reactiver_abonne_non_suspendu_raises(self) -> None:
        abonne = _create_abonne(self.service)
        with self.assertRaises(ValidationError):
            self.service.reactiver_abonne(str(abonne.id))

    def test_resilier_abonne(self) -> None:
        abonne = _create_abonne(self.service)
        resilie = self.service.resilier_abonne(str(abonne.id))
        self.assertEqual(resilie.statut, StatutAbonne.RESILIE)

    def test_resilier_abonne_desactive_le_compteur_actif(self) -> None:
        """Régression ANO-017 : le compteur actif doit passer à DESACTIVE
        lors de la résiliation de l'abonné (il n'est ni remplacé, ni
        toujours en service)."""
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        compteur = Compteur.objects.get(abonne_id=abonne.id)
        self.assertEqual(compteur.statut, StatutCompteur.DESACTIVE)

    def test_resilier_abonne_deja_resilie_raises(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.resilier_abonne(str(abonne.id))

    def test_suspendre_abonne_resilie_raises(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.suspendre_abonne(str(abonne.id))

    def test_anonymiser_abonne_actif_raises(self) -> None:
        abonne = _create_abonne(self.service)
        with self.assertRaises(ValidationError):
            self.service.anonymiser_abonne(str(abonne.id))

    def test_anonymiser_abonne_suspendu_raises(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.suspendre_abonne(str(abonne.id))
        with self.assertRaises(ValidationError):
            self.service.anonymiser_abonne(str(abonne.id))

    def test_anonymiser_abonne_resilie_remplace_les_champs_nominatifs(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        anonymise = self.service.anonymiser_abonne(str(abonne.id))
        self.assertEqual(anonymise.nom, AbonneService.NOM_ANONYMISE)
        self.assertEqual(anonymise.prenom, AbonneService.PRENOM_ANONYMISE)
        self.assertEqual(anonymise.telephone_whatsapp, AbonneService.TELEPHONE_ANONYMISE)
        self.assertEqual(anonymise.adresse, AbonneService.ADRESSE_ANONYMISEE)

    def test_anonymiser_abonne_preserve_id_et_numero_et_statut(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        anonymise = self.service.anonymiser_abonne(str(abonne.id))
        self.assertEqual(anonymise.id, abonne.id)
        self.assertEqual(anonymise.numero_abonne, abonne.numero_abonne)
        self.assertEqual(anonymise.statut, StatutAbonne.RESILIE)

    def test_anonymiser_abonne_est_idempotent(self) -> None:
        abonne = _create_abonne(self.service)
        self.service.resilier_abonne(str(abonne.id))
        premier = self.service.anonymiser_abonne(str(abonne.id))
        second = self.service.anonymiser_abonne(str(abonne.id))
        self.assertEqual(second.nom, premier.nom)
        self.assertEqual(second.prenom, premier.prenom)
        self.assertEqual(second.telephone_whatsapp, premier.telephone_whatsapp)
        self.assertEqual(second.adresse, premier.adresse)
        self.assertEqual(second.statut, StatutAbonne.RESILIE)

    def test_create_abonne_with_invalid_telephone_raises(self) -> None:
        with self.assertRaises(ValidationError):
            _create_abonne(self.service, telephone_whatsapp="pas-un-numero")

    def test_create_abonne_with_empty_telephone_raises(self) -> None:
        with self.assertRaises(ValidationError):
            _create_abonne(self.service, telephone_whatsapp="")

    def test_update_abonne_with_invalid_telephone_raises(self) -> None:
        abonne = _create_abonne(self.service)
        with self.assertRaises(ValidationError):
            self.service.update_abonne(str(abonne.id), nom="", prenom="", telephone_whatsapp="123", adresse="")

    def test_create_abonne_is_atomic_on_compteur_failure(self) -> None:
        # numero_compteur=1 existe déjà : la création du Compteur échoue
        # (contrainte unique) et ne doit pas laisser un Abonne orphelin.
        _create_abonne(self.service)
        with self.assertRaises(IntegrityError):
            _create_abonne(self.service, numero_compteur=1)
        self.assertEqual(Abonne.objects.count(), 1)

    def test_list_abonnes_actifs_excludes_suspendus(self) -> None:
        a1 = _create_abonne(self.service)
        a2 = _create_abonne(self.service, numero_compteur=2)
        self.service.suspendre_abonne(str(a2.id))
        actifs = {a.id for a in self.service.list_abonnes_actifs()}
        self.assertIn(a1.id, actifs)
        self.assertNotIn(a2.id, actifs)

    def test_list_abonnes_filters_by_statut(self) -> None:
        a1 = _create_abonne(self.service)
        self.service.suspendre_abonne(str(a1.id))
        suspendus = self.service.list_abonnes(StatutAbonne.SUSPENDU)
        self.assertEqual([a.id for a in suspendus], [a1.id])

    def test_list_abonnes_sans_pagination_renvoie_tout(self) -> None:
        # Rétrocompatibilité stricte : `limit`/`offset` omis (valeur par
        # défaut `None`) doit préserver le comportement historique.
        for i in range(1, 4):
            _create_abonne(self.service, numero_compteur=i)
        self.assertEqual(len(self.service.list_abonnes()), 3)

    def test_list_abonnes_avec_pagination_tronque_et_decale(self) -> None:
        for i in range(1, 6):
            _create_abonne(self.service, numero_compteur=i)
        page = self.service.list_abonnes(limit=2, offset=1)
        self.assertEqual([a.numero_abonne for a in page], ["AB-0002", "AB-0003"])

    def test_list_abonnes_pagination_hors_limites_renvoie_liste_vide(self) -> None:
        _create_abonne(self.service)
        page = self.service.list_abonnes(limit=10, offset=100)
        self.assertEqual(page, [])

    def test_list_abonnes_pagination_se_combine_au_filtre_statut(self) -> None:
        # La pagination doit s'appliquer sur le résultat FILTRÉ par statut,
        # pas sur la table brute.
        a1 = _create_abonne(self.service, numero_compteur=1)
        _create_abonne(self.service, numero_compteur=2)
        self.service.suspendre_abonne(str(a1.id))
        page = self.service.list_abonnes(StatutAbonne.SUSPENDU, limit=1, offset=0)
        self.assertEqual([a.id for a in page], [a1.id])
        self.assertEqual(self.service.count_abonnes(StatutAbonne.SUSPENDU), 1)

    def test_list_abonnes_filtre_par_ids(self) -> None:
        a1 = _create_abonne(self.service)
        a2 = _create_abonne(self.service, numero_compteur=2)
        _create_abonne(self.service, numero_compteur=3)
        resultat = self.service.list_abonnes(ids=[str(a1.id), str(a2.id)])
        self.assertEqual({a.id for a in resultat}, {a1.id, a2.id})

    def test_count_abonnes_filtre_par_ids(self) -> None:
        a1 = _create_abonne(self.service)
        _create_abonne(self.service, numero_compteur=2)
        self.assertEqual(self.service.count_abonnes(ids=[str(a1.id)]), 1)

    def test_count_abonnes_ignore_la_pagination(self) -> None:
        for i in range(1, 4):
            _create_abonne(self.service, numero_compteur=i)
        self.assertEqual(self.service.count_abonnes(), 3)
        # Le total ne varie pas selon une éventuelle pagination de la liste.
        self.service.list_abonnes(limit=1)
        self.assertEqual(self.service.count_abonnes(), 3)


class CompteurServiceTests(TestCase):
    def setUp(self) -> None:
        self.abonne_service = AbonneService()
        self.compteur_service = CompteurService()

    def test_remplacer_compteur_archives_old_and_creates_new(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0)
        ancien = self.compteur_service.get_compteur_actif(str(abonne.id))

        nouveau = self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Nouveau Quartier",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )

        ancien.refresh_from_db()
        self.assertEqual(ancien.statut, StatutCompteur.REMPLACE)
        self.assertEqual(nouveau.statut, StatutCompteur.ACTIF)
        self.assertEqual(nouveau.numero_compteur, 2)
        self.assertEqual(self.compteur_service.get_compteur_actif(str(abonne.id)).id, nouveau.id)

    def test_remplacer_compteur_enregistre_le_motif(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
            motif="Compteur défectueux",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique[0].motif, "Compteur défectueux")

    def test_remplacer_compteur_transporte_la_nouvelle_position(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1, position="Ancienne")
        nouveau = self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
            nouvelle_position="Près du portail bleu",
        )
        self.assertEqual(nouveau.position, "Près du portail bleu")

    def test_remplacer_compteur_position_optionnelle_defaut_vide(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        nouveau = self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        self.assertEqual(nouveau.position, "")

    def test_remplacer_compteur_motif_optionnel_defaut_vide(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique[0].motif, "")

    def test_remplacer_compteur_with_index_fermeture_below_initial_raises(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=50)

        with self.assertRaises(ValidationError):
            self.compteur_service.remplacer_compteur(
                abonne_id=str(abonne.id),
                index_fermeture=10,
                nouveau_numero_compteur=2,
                nouveau_quartier="Q",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            )

    def test_update_compteur_modifie_les_champs_fournis(self) -> None:
        abonne = _create_abonne(self.abonne_service, quartier="Ancien", camp=1)
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id), quartier="Nouveau", camp=2, index_initial=None, date_pose=None
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.quartier, "Nouveau")
        self.assertEqual(compteur.camp, 2)

    def test_update_compteur_ne_modifie_pas_les_champs_non_fournis(self) -> None:
        abonne = _create_abonne(self.abonne_service, quartier="Bastos", index_initial=50)
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id), quartier="Nlongkak", camp=None, index_initial=None, date_pose=None
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.quartier, "Nlongkak")
        self.assertEqual(float(compteur.index_initial), 50.0)

    def test_update_compteur_position(self) -> None:
        abonne = _create_abonne(self.abonne_service, position="Ancienne position")
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id),
            quartier=None,
            camp=None,
            index_initial=None,
            date_pose=None,
            position="Près du transformateur",
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.position, "Près du transformateur")

    def test_update_compteur_position_non_fournie_ne_change_rien(self) -> None:
        abonne = _create_abonne(self.abonne_service, position="Position initiale")
        self.compteur_service.update_compteur(
            abonne_id=str(abonne.id),
            quartier="Autre",
            camp=None,
            index_initial=None,
            date_pose=None,
        )
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.position, "Position initiale")

    def test_remplacer_compteur_is_atomic_on_numero_collision(self) -> None:
        # nouveau_numero_compteur=1 collisionne avec le compteur actif
        # lui-même : la création échoue et l'ancien compteur ne doit pas
        # rester archivé sans qu'un nouveau compteur ACTIF existe.
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        ancien = self.compteur_service.get_compteur_actif(str(abonne.id))

        with self.assertRaises(IntegrityError):
            self.compteur_service.remplacer_compteur(
                abonne_id=str(abonne.id),
                index_fermeture=10,
                nouveau_numero_compteur=1,
                nouveau_quartier="Q",
                nouveau_camp=2,
                nouvel_index_initial=0,
                date_remplacement="2024-06-01",
            )

        ancien.refresh_from_db()
        self.assertEqual(ancien.statut, StatutCompteur.ACTIF)
        self.assertEqual(self.compteur_service.get_compteur_actif(str(abonne.id)).id, ancien.id)

    def test_get_historique_returns_entry_after_remplacement(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0, numero_compteur=1)
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=100,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(len(historique), 1)
        self.assertEqual(float(historique[0].index_fermeture), 100.0)
        self.assertEqual(historique[0].nouveau_compteur.numero_compteur, 2)

    def test_get_historique_empty_for_new_abonne(self) -> None:
        abonne = _create_abonne(self.abonne_service)
        historique = self.compteur_service.get_historique(str(abonne.id))
        self.assertEqual(historique, [])


class ImporterCoordonneesTests(TestCase):
    """Import CSV en masse des coordonnées GPS de compteurs
    (CompteurService.importer_coordonnees) — rapprochement par
    numero_compteur, dégradation gracieuse par ligne."""

    def setUp(self) -> None:
        self.abonne_service = AbonneService()
        self.compteur_service = CompteurService()

    def test_import_reussi_met_a_jour_latitude_longitude_et_date_maj(self) -> None:
        abonne = _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "1", "latitude": "3.866667", "longitude": "11.516667"}]
        )
        self.assertEqual(resultat["nb_importees"], 1)
        self.assertEqual(resultat["erreurs"], [])
        compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        self.assertEqual(compteur.latitude, Decimal("3.866667"))
        self.assertEqual(compteur.longitude, Decimal("11.516667"))
        self.assertIsNotNone(compteur.date_maj_position)

    def test_import_numero_introuvable_va_dans_les_erreurs_sans_lever(self) -> None:
        _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "999", "latitude": "3.866667", "longitude": "11.516667"}]
        )
        self.assertEqual(resultat["nb_importees"], 0)
        self.assertEqual(len(resultat["erreurs"]), 1)
        self.assertEqual(resultat["erreurs"][0]["numero_compteur"], "999")

    def test_import_coordonnee_invalide_va_dans_les_erreurs_sans_lever(self) -> None:
        _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "1", "latitude": "pas-un-nombre", "longitude": "11.516667"}]
        )
        self.assertEqual(resultat["nb_importees"], 0)
        self.assertEqual(len(resultat["erreurs"]), 1)
        self.assertEqual(resultat["erreurs"][0]["numero_compteur"], "1")

    def test_import_latitude_hors_bornes_va_dans_les_erreurs(self) -> None:
        _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "1", "latitude": "120", "longitude": "11.516667"}]
        )
        self.assertEqual(resultat["nb_importees"], 0)
        self.assertEqual(len(resultat["erreurs"]), 1)

    def test_import_longitude_hors_bornes_va_dans_les_erreurs(self) -> None:
        _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "1", "latitude": "3.866667", "longitude": "200"}]
        )
        self.assertEqual(resultat["nb_importees"], 0)
        self.assertEqual(len(resultat["erreurs"]), 1)

    def test_import_partiel_certaines_lignes_reussissent_dautres_echouent(self) -> None:
        abonne_1 = _create_abonne(self.abonne_service, numero_compteur=1)
        abonne_2 = _create_abonne(self.abonne_service, numero_compteur=2)
        resultat = self.compteur_service.importer_coordonnees(
            [
                {"numero_compteur": "1", "latitude": "3.866667", "longitude": "11.516667"},
                {"numero_compteur": "999", "latitude": "3.866667", "longitude": "11.516667"},
                {"numero_compteur": "2", "latitude": "pas-un-nombre", "longitude": "11.516667"},
            ]
        )
        self.assertEqual(resultat["nb_importees"], 1)
        self.assertEqual(len(resultat["erreurs"]), 2)
        self.assertEqual(
            {e["numero_compteur"] for e in resultat["erreurs"]},
            {"999", "2"},
        )
        compteur_1 = self.compteur_service.get_compteur_actif(str(abonne_1.id))
        self.assertEqual(compteur_1.latitude, Decimal("3.866667"))
        # La ligne en échec ne doit pas avoir touché le compteur 2.
        compteur_2 = self.compteur_service.get_compteur_actif(str(abonne_2.id))
        self.assertIsNone(compteur_2.latitude)

    def test_import_numero_compteur_non_numerique_va_dans_les_erreurs(self) -> None:
        _create_abonne(self.abonne_service, numero_compteur=1)
        resultat = self.compteur_service.importer_coordonnees(
            [{"numero_compteur": "abc", "latitude": "3.866667", "longitude": "11.516667"}]
        )
        self.assertEqual(resultat["nb_importees"], 0)
        self.assertEqual(resultat["erreurs"][0]["numero_compteur"], "abc")

    def test_import_liste_vide_renvoie_resultat_vide(self) -> None:
        resultat = self.compteur_service.importer_coordonnees([])
        self.assertEqual(resultat, {"nb_importees": 0, "erreurs": []})
