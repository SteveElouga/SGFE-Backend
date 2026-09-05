import sys
from collections import namedtuple
from collections.abc import Callable
from pathlib import Path
from typing import Any

import grpc
from django.conf import settings
from schema.grpc_auth import canal_authentifie
from schema.identity_context import get_identity, get_request_id

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import abonne_service_pb2 as abonne_pb
import abonne_service_pb2_grpc as abonne_pb_grpc
import auth_service_pb2 as auth_pb
import auth_service_pb2_grpc as auth_pb_grpc
import campagne_service_pb2 as campagne_pb
import campagne_service_pb2_grpc as campagne_pb_grpc
import config_service_pb2 as config_pb
import config_service_pb2_grpc as config_pb_grpc
import facturation_service_pb2 as facturation_pb
import facturation_service_pb2_grpc as facturation_pb_grpc
import notification_service_pb2 as notification_pb
import notification_service_pb2_grpc as notification_pb_grpc
import paiement_service_pb2 as paiement_pb
import paiement_service_pb2_grpc as paiement_pb_grpc
import reporting_service_pb2 as reporting_pb
import reporting_service_pb2_grpc as reporting_pb_grpc


class _DetailsAppel(
    namedtuple("_DetailsAppel", ("method", "timeout", "metadata", "credentials")),
    grpc.ClientCallDetails,
):
    """`ClientCallDetails` est une interface, pas une classe instanciable.

    Même motif que `_DetailsAppel` de `grpc_auth.py` (reconstruire le tuple
    est le moyen documenté d'enrichir la métadonnée d'un appel sortant) —
    dupliqué ici plutôt qu'importé pour ne pas toucher au fichier synchronisé
    entre les neuf composants (voir l'en-tête de `grpc_auth.py`).
    """


class IdentityClientInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Propage l'identité de la requête gateway courante vers chaque appel gRPC sortant.

    Lit `get_identity()` (posé par `require_auth`, voir `context.py`) : si une
    identité est présente, ajoute les métadonnées `x-user-id`/`x-user-name`/
    `x-user-role` + `x-request-id` (identifiant de corrélation de la requête).
    Appel anonyme (login, refresh, espace abonné public, OTP...) — identité
    absente : **aucune métadonnée ajoutée**, comportement inchangé.

    Posé une fois à la création du canal (voir `_canal_avec_identite`), il
    couvre tous les appels qui y transitent — présents et futurs, comme
    `AuthClientInterceptor` (grpc_auth.py) dont il complète le rôle : ce
    dernier authentifie l'appelant applicatif (« la gateway parle bien »),
    celui-ci documente le « qui » humain derrière cet appel.
    """

    def intercept_unary_unary(
        self,
        continuation: Callable[[grpc.ClientCallDetails, Any], Any],
        client_call_details: grpc.ClientCallDetails,
        request: Any,
    ) -> Any:
        identity = get_identity()
        if identity is None:
            return continuation(client_call_details, request)

        metadata = list(client_call_details.metadata or ())
        metadata.append(("x-user-id", identity.user_id))
        metadata.append(("x-user-name", identity.username))
        metadata.append(("x-user-role", identity.role))
        metadata.append(("x-request-id", get_request_id()))
        return continuation(
            _DetailsAppel(
                client_call_details.method,
                client_call_details.timeout,
                metadata,
                client_call_details.credentials,
            ),
            request,
        )


def _canal_avec_identite(adresse: str) -> grpc.Channel:
    """Ouvre un canal authentifié (voir `grpc_auth.canal_authentifie`) et
    l'enveloppe en plus de `IdentityClientInterceptor`, pour que chaque appel
    sortant porte l'identité de la requête gateway courante."""
    return grpc.intercept_channel(
        canal_authentifie(adresse, settings.INTERNAL_GRPC_KEY),
        IdentityClientInterceptor(),
    )


class AuthServiceClient:
    """Client gRPC vers auth-service:50051 (voir proto/auth_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.AUTH_GRPC_HOST}:{settings.AUTH_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = auth_pb_grpc.AuthServiceStub(self._channel)

    def login(self, identifier: str, password: str) -> auth_pb.TokenResponse:
        return self._stub.Login(auth_pb.LoginRequest(identifier=identifier, password=password))

    def validate_token(self, token: str) -> auth_pb.UserPayload:
        return self._stub.ValidateToken(auth_pb.TokenRequest(token=token))

    def refresh_token(self, refresh_token: str) -> auth_pb.TokenResponse:
        return self._stub.RefreshToken(auth_pb.RefreshRequest(refresh_token=refresh_token))

    def logout(self, token: str) -> auth_pb.StatusResponse:
        return self._stub.Logout(auth_pb.TokenRequest(token=token))

    def create_user(self, username: str, phone_number: str, role: str, email: str = "") -> auth_pb.UserResponse:
        return self._stub.CreateUser(
            auth_pb.CreateUserRequest(username=username, email=email, phone_number=phone_number, role=role)
        )

    def update_user(
        self, user_id: str, email: str = "", role: str = "", phone_number: str = ""
    ) -> auth_pb.UserResponse:
        return self._stub.UpdateUser(
            auth_pb.UpdateUserRequest(user_id=user_id, email=email, role=role, phone_number=phone_number)
        )

    def deactivate_user(self, user_id: str, caller_id: str = "") -> auth_pb.UserResponse:
        return self._stub.DeactivateUser(auth_pb.DeactivateUserRequest(user_id=user_id, caller_id=caller_id))

    def reactivate_user(self, user_id: str) -> auth_pb.UserResponse:
        return self._stub.ReactivateUser(auth_pb.UserIdRequest(user_id=user_id))

    def anonymiser_utilisateur(self, user_id: str) -> auth_pb.UserResponse:
        """RGPD — droit à l'effacement. Auth Service refuse (INVALID_ARGUMENT)
        si le compte est encore actif (is_active=true)."""
        return self._stub.AnonymiserUtilisateur(auth_pb.UserIdRequest(user_id=user_id))

    def exporter_donnees_utilisateur(self, user_id: str) -> auth_pb.ExportDonneesUtilisateurResponse:
        """RGPD — droit à la portabilité. Réponse synchrone (voir
        comptes/export.py côté Auth Service pour le détail des sections)."""
        return self._stub.ExporterDonneesUtilisateur(auth_pb.UserIdRequest(user_id=user_id))

    def reset_user_password(self, user_id: str) -> auth_pb.UserResponse:
        return self._stub.ResetUserPassword(auth_pb.UserIdRequest(user_id=user_id))

    def get_user(self, user_id: str) -> auth_pb.UserResponse:
        return self._stub.GetUser(auth_pb.UserIdRequest(user_id=user_id))

    def list_users(self) -> auth_pb.ListUsersResponse:
        return self._stub.ListUsers(auth_pb.EmptyRequest())

    def request_password_reset(self, email: str) -> auth_pb.StatusResponse:
        return self._stub.RequestPasswordReset(auth_pb.EmailRequest(email=email))

    def set_password_with_token(self, token: str, new_password: str) -> auth_pb.StatusResponse:
        return self._stub.SetPasswordWithToken(auth_pb.SetPasswordRequest(token=token, new_password=new_password))

    def request_phone_otp(self, phone_number: str) -> auth_pb.StatusResponse:
        return self._stub.RequestPhoneOtp(auth_pb.PhoneRequest(phone_number=phone_number))

    def verify_otp_and_set_password(
        self, phone_number: str, otp_code: str, new_password: str
    ) -> auth_pb.StatusResponse:
        return self._stub.VerifyOtpAndSetPassword(
            auth_pb.VerifyOtpRequest(phone_number=phone_number, otp_code=otp_code, new_password=new_password)
        )


class AbonneServiceClient:
    """Client gRPC vers abonne-service:50052 (voir proto/abonne_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = abonne_pb_grpc.AbonneServiceStub(self._channel)

    def get_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.GetAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def list_abonnes(
        self, statut: str = "", limit: int | None = None, offset: int | None = None
    ) -> abonne_pb.ListAbonnesResponse:
        """Abonnés filtrés. `limit`/`offset` optionnels — omis, le champ
        proto3 `optional` correspondant reste non défini côté serveur, qui
        renvoie alors la liste complète (rétrocompatibilité stricte)."""
        kwargs: dict[str, object] = {"statut": statut}
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        return self._stub.ListAbonnes(abonne_pb.ListAbonnesRequest(**kwargs))

    def count_abonnes(self, statut: str = "") -> int:
        """Nombre total d'abonnés pour ce filtre, sans rapatrier la liste :
        demande la page la plus petite possible (`limit=0`) et ne lit que
        `total` sur la réponse."""
        return int(self.list_abonnes(statut, limit=0, offset=0).total)

    def list_abonnes_actifs(self) -> abonne_pb.ListAbonnesResponse:
        return self._stub.ListAbonnesActifs(abonne_pb.EmptyRequest())

    def list_zones(self) -> abonne_pb.ListZonesResponse:
        return self._stub.ListZones(abonne_pb.EmptyRequest())

    def create_abonne(self, **kwargs: Any) -> abonne_pb.AbonneResponse:
        return self._stub.CreateAbonne(abonne_pb.CreateAbonneRequest(**kwargs))

    def update_abonne(self, abonne_id: str, **kwargs: Any) -> abonne_pb.AbonneResponse:
        return self._stub.UpdateAbonne(abonne_pb.UpdateAbonneRequest(abonne_id=abonne_id, **kwargs))

    def suspendre_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.SuspendreAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def reactiver_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ReactiverAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def resilier_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ResilierAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def anonymiser_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        """RGPD — droit à l'effacement. Abonné Service refuse (INVALID_ARGUMENT)
        si l'abonné n'est pas déjà RESILIE."""
        return self._stub.AnonymiserAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def exporter_donnees_abonne(self, abonne_id: str) -> abonne_pb.ExportDonneesAbonneResponse:
        """RGPD — droit à la portabilité. Réponse synchrone (voir
        abonnes/export.py côté Abonné Service pour le choix de format/volume)."""
        return self._stub.ExporterDonneesAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def update_compteur(self, abonne_id: str, **kwargs: Any) -> abonne_pb.CompteurResponse:
        return self._stub.UpdateCompteur(abonne_pb.UpdateCompteurRequest(abonne_id=abonne_id, **kwargs))

    def remplacer_compteur(self, abonne_id: str, **kwargs: Any) -> abonne_pb.CompteurResponse:
        return self._stub.RemplacerCompteur(abonne_pb.RemplacerCompteurRequest(abonne_id=abonne_id, **kwargs))

    def get_historique_compteur(self, abonne_id: str) -> abonne_pb.ListHistoriqueResponse:
        return self._stub.GetHistoriqueCompteur(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))


class ConfigServiceClient:
    """Client gRPC vers config-service:50058 (voir proto/config_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = config_pb_grpc.ConfigServiceStub(self._channel)

    def get_infos_societe(self) -> config_pb.InfosSocieteResponse:
        return self._stub.GetInfosSociete(config_pb.EmptyRequest())

    def update_infos_societe(self, **kwargs: Any) -> config_pb.InfosSocieteResponse:
        return self._stub.UpdateInfosSociete(config_pb.UpdateInfosRequest(**kwargs))

    def get_config(self, cle: str) -> config_pb.ConfigResponse:
        return self._stub.GetConfig(config_pb.ConfigKeyRequest(cle=cle))

    def update_config(self, cle: str, valeur: str) -> config_pb.ConfigResponse:
        return self._stub.UpdateConfig(config_pb.UpdateConfigRequest(cle=cle, valeur=valeur))

    def list_configs(self) -> config_pb.ListConfigsResponse:
        return self._stub.ListConfigs(config_pb.EmptyRequest())


class CampagneServiceClient:
    """Client gRPC vers campagne-service:50053 (voir proto/campagne_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.CAMPAGNE_GRPC_HOST}:{settings.CAMPAGNE_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = campagne_pb_grpc.CampagneServiceStub(self._channel)

    def create_campagne(self, **kwargs: Any) -> campagne_pb.CampagneResponse:
        return self._stub.CreateCampagne(campagne_pb.CreateCampagneRequest(**kwargs))

    def get_campagne(self, campagne_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.GetCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def list_campagnes(self, created_by: str = "", agent_id: str = "") -> campagne_pb.ListCampagnesResponse:
        return self._stub.ListCampagnes(campagne_pb.ListCampagnesRequest(created_by=created_by, agent_id=agent_id))

    def assigner_agent(self, campagne_id: str, agent_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.AssignerAgent(campagne_pb.AssignerAgentRequest(campagne_id=campagne_id, agent_id=agent_id))

    def ajouter_abonnes_campagne(self, campagne_id: str, abonne_ids: list[str]) -> campagne_pb.AjouterAbonnesResponse:
        return self._stub.AjouterAbonnesCampagne(
            campagne_pb.AjouterAbonnesCampagneRequest(campagne_id=campagne_id, abonne_ids=abonne_ids)
        )

    def affecter_zones(self, **kwargs: Any) -> campagne_pb.ListAgentsCampagneResponse:
        return self._stub.AffecterZones(campagne_pb.AffecterZonesRequest(**kwargs))

    def list_agents_campagne(self, campagne_id: str) -> campagne_pb.ListAgentsCampagneResponse:
        return self._stub.ListAgentsCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def saisir_index(self, **kwargs: Any) -> campagne_pb.ReleveResponse:
        return self._stub.SaisirIndex(campagne_pb.SaisirIndexRequest(**kwargs))

    def corriger_releve(self, **kwargs: Any) -> campagne_pb.ReleveResponse:
        return self._stub.CorrigerReleve(campagne_pb.CorrigerReleveRequest(**kwargs))

    def marquer_non_releve(self, **kwargs: Any) -> campagne_pb.ReleveResponse:
        return self._stub.MarquerNonReleve(campagne_pb.MarquerNonReleveRequest(**kwargs))

    def get_releve(self, releve_id: str) -> campagne_pb.ReleveResponse:
        return self._stub.GetReleve(campagne_pb.ReleveIdRequest(releve_id=releve_id))

    def list_releves(self, campagne_id: str) -> campagne_pb.ListRelevesResponse:
        return self._stub.ListReleves(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def list_releves_tournee(self, campagne_id: str, agent_id: str) -> campagne_pb.ListRelevesResponse:
        return self._stub.ListRelevesTournee(
            campagne_pb.ListRelevesTourneeRequest(campagne_id=campagne_id, agent_id=agent_id)
        )

    def get_progression(self, campagne_id: str) -> campagne_pb.ProgressionResponse:
        return self._stub.GetProgression(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_resume_cloture(self, campagne_id: str) -> campagne_pb.ResumeClotureResponse:
        return self._stub.GetResumeCloture(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def demarrer_campagne(self, campagne_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.DemarrerCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def cloturer_campagne(self, campagne_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.CloturerCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_dernier_index(self, abonne_id: str) -> campagne_pb.DernierIndexResponse:
        return self._stub.GetDernierIndex(campagne_pb.AbonneIdRequest(abonne_id=abonne_id))


class FacturationServiceClient:
    """Client gRPC vers facturation-service:50054 (voir proto/facturation_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = facturation_pb_grpc.FacturationServiceStub(self._channel)

    def get_tarif_actuel(self) -> facturation_pb.TarifResponse:
        return self._stub.GetTarifActuel(facturation_pb.EmptyRequest())

    def update_tarif(self, prix_m3: float, date_effet: str) -> facturation_pb.TarifResponse:
        return self._stub.UpdateTarif(facturation_pb.UpdateTarifRequest(prix_m3=prix_m3, date_effet=date_effet))

    def generer_factures(
        self, campagne_id: str, envoyer_whatsapp_auto: bool = True
    ) -> facturation_pb.GenererFacturesResponse:
        return self._stub.GenererFactures(
            facturation_pb.GenererFacturesRequest(
                campagne_id=campagne_id,
                envoyer_whatsapp_auto=envoyer_whatsapp_auto,
            )
        )

    def get_facture(self, facture_id: str) -> facturation_pb.FactureResponse:
        return self._stub.GetFacture(facturation_pb.FactureIdRequest(facture_id=facture_id))

    def list_factures(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: str = "",
        date_fin: str = "",
        limit: int | None = None,
        offset: int | None = None,
    ) -> facturation_pb.ListFacturesResponse:
        """Factures filtrées. `date_debut`/`date_fin` : bornes ISO incluses, sur
        la date de génération — la seule que portent les deux natures de facture.

        `limit`/`offset` optionnels — omis, le champ proto3 `optional`
        correspondant reste non défini côté serveur, qui renvoie alors la
        liste complète filtrée (rétrocompatibilité stricte)."""
        kwargs: dict[str, object] = {
            "campagne_id": campagne_id,
            "abonne_id": abonne_id,
            "statut": statut,
            "date_debut": date_debut,
            "date_fin": date_fin,
        }
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        return self._stub.ListFactures(facturation_pb.ListFacturesRequest(**kwargs))

    def count_factures(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: str = "",
        date_fin: str = "",
    ) -> int:
        """Nombre total de factures pour ce filtre, sans rapatrier la liste :
        demande la page la plus petite possible (`limit=0`) et ne lit que
        `total` sur la réponse."""
        return int(
            self.list_factures(
                campagne_id=campagne_id,
                abonne_id=abonne_id,
                statut=statut,
                date_debut=date_debut,
                date_fin=date_fin,
                limit=0,
                offset=0,
            ).total
        )

    def get_factures_par_campagne(self, campagne_id: str) -> facturation_pb.ListFacturesResponse:
        return self._stub.GetFacturesParCampagne(facturation_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_facture_pdf(self, facture_id: str) -> facturation_pb.PDFResponse:
        return self._stub.GetFacturePDF(facturation_pb.FactureIdRequest(facture_id=facture_id))

    def generer_bilan_impayes_pdf(self) -> facturation_pb.PDFResponse:
        return self._stub.GenererBilanImpayesPDF(facturation_pb.EmptyRequest())

    def generer_synthese_campagne_pdf(self, campagne_id: str) -> facturation_pb.PDFResponse:
        return self._stub.GenererSyntheseCampagnePDF(facturation_pb.CampagneIdRequest(campagne_id=campagne_id))

    def generer_recu_paiement_pdf(
        self,
        paiement_id: str,
        facture_id: str,
        montant_versement: float = 0.0,
        solde_restant_total: float = 0.0,
    ) -> facturation_pb.PDFResponse:
        return self._stub.GenererRecuPaiementPDF(
            facturation_pb.GenererRecuRequest(
                paiement_id=paiement_id,
                facture_id=facture_id,
                montant_versement=montant_versement,
                solde_restant_total=solde_restant_total,
            )
        )

    def annuler_facture(self, facture_id: str, motif: str, annule_par: str) -> facturation_pb.FactureResponse:
        return self._stub.AnnulerFacture(
            facturation_pb.AnnulerFactureRequest(facture_id=facture_id, motif=motif, annule_par=annule_par)
        )

    def regenerer_facture(
        self, facture_id: str, motif: str, regenere_par: str
    ) -> facturation_pb.RegenererFactureResponse:
        return self._stub.RegenererFacture(
            facturation_pb.RegenererFactureRequest(facture_id=facture_id, motif=motif, regenere_par=regenere_par)
        )

    def creer_regularisation(
        self, abonne_id: str, montant: float, motif: str, date_limite_paiement: str = ""
    ) -> facturation_pb.FactureResponse:
        return self._stub.CreerRegularisation(
            facturation_pb.CreerRegularisationRequest(
                abonne_id=abonne_id,
                montant=montant,
                motif=motif,
                date_limite_paiement=date_limite_paiement,
            )
        )

    def update_statut_facture(self, facture_id: str, statut: str) -> facturation_pb.FactureResponse:
        return self._stub.UpdateStatutFacture(facturation_pb.UpdateStatutRequest(facture_id=facture_id, statut=statut))


class PaiementServiceClient:
    """Client gRPC vers paiement-service:50055 (voir proto/paiement_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = paiement_pb_grpc.PaiementServiceStub(self._channel)

    def get_solde(self, facture_id: str) -> paiement_pb.SoldeResponse:
        return self._stub.GetSolde(paiement_pb.FactureIdRequest(facture_id=facture_id))

    def list_paiements(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: str = "",
        date_fin: str = "",
        limit: int | None = None,
        offset: int | None = None,
    ) -> paiement_pb.ListPaiementsResponse:
        """Paiements filtrés. `date_debut`/`date_fin` : bornes ISO incluses, sur
        la date de paiement — la date de caisse, celle qu'un journal demande.

        `limit`/`offset` optionnels — omis, le champ proto3 `optional`
        correspondant reste non défini côté serveur, qui renvoie alors la
        liste complète filtrée (rétrocompatibilité stricte)."""
        kwargs: dict[str, object] = {
            "facture_id": facture_id,
            "abonne_id": abonne_id,
            "date_debut": date_debut,
            "date_fin": date_fin,
        }
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        return self._stub.ListPaiements(paiement_pb.ListPaiementsRequest(**kwargs))

    def count_paiements(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: str = "",
        date_fin: str = "",
    ) -> int:
        """Nombre total de paiements pour ce filtre, sans rapatrier la liste :
        demande la page la plus petite possible (`limit=0`) et ne lit que
        `total` sur la réponse."""
        return int(
            self.list_paiements(
                facture_id=facture_id,
                abonne_id=abonne_id,
                date_debut=date_debut,
                date_fin=date_fin,
                limit=0,
                offset=0,
            ).total
        )

    def list_paiements_par_campagne(self, campagne_id: str) -> paiement_pb.ListPaiementsResponse:
        return self._stub.ListPaiementsParCampagne(paiement_pb.CampagneIdRequest(campagne_id=campagne_id))

    def enregistrer_paiement(
        self,
        facture_id: str,
        abonne_id: str,
        montant: float,
        date_paiement: str,
        mode_paiement: str,
        reference_transaction: str = "",
        enregistre_par: str = "",
    ) -> paiement_pb.PaiementResponse:
        return self._stub.EnregistrerPaiement(
            paiement_pb.EnregistrerPaiementRequest(
                facture_id=facture_id,
                abonne_id=abonne_id,
                montant=montant,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction=reference_transaction,
                enregistre_par=enregistre_par,
            )
        )

    def annuler_paiement(self, paiement_id: str, motif: str, annule_par: str) -> paiement_pb.PaiementResponse:
        return self._stub.AnnulerPaiement(
            paiement_pb.AnnulerPaiementRequest(
                paiement_id=paiement_id,
                motif=motif,
                annule_par=annule_par,
            )
        )

    def list_impayes(self) -> paiement_pb.ListImpayesResponse:
        return self._stub.ListImpayes(paiement_pb.EmptyRequest())

    def get_suivi_impaye(self, facture_id: str) -> paiement_pb.SuiviImpayeResponse:
        return self._stub.GetSuiviImpaye(paiement_pb.FactureIdRequest(facture_id=facture_id))

    def get_dette_abonne(self, abonne_id: str, hors_facture_id: str = "") -> paiement_pb.DetteAbonneResponse:
        return self._stub.GetDetteAbonne(
            paiement_pb.DetteAbonneRequest(abonne_id=abonne_id, hors_facture_id=hors_facture_id)
        )

    def enregistrer_paiement_abonne(
        self,
        abonne_id: str,
        montant: float,
        date_paiement: str,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
    ) -> paiement_pb.PaiementAbonneResponse:
        return self._stub.EnregistrerPaiementAbonne(
            paiement_pb.EnregistrerPaiementAbonneRequest(
                abonne_id=abonne_id,
                montant=montant,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction=reference_transaction,
                enregistre_par=enregistre_par,
            )
        )

    def crediter_avoir(self, abonne_id: str, montant: float, motif: str, cree_par: str) -> paiement_pb.AvoirResponse:
        return self._stub.CrediterAvoir(
            paiement_pb.CrediterAvoirRequest(
                abonne_id=abonne_id,
                montant=montant,
                motif=motif,
                cree_par=cree_par,
            )
        )

    def get_avoir_abonne(self, abonne_id: str) -> paiement_pb.AvoirResponse:
        return self._stub.GetAvoirAbonne(paiement_pb.AbonneIdRequest(abonne_id=abonne_id))

    def creer_session_paiement(
        self, facture_id: str, montant: float, token_espace: str
    ) -> paiement_pb.SessionPaiementResponse:
        """Ouvre une session de paiement en ligne (mock, espace abonné public).

        Voir `services/paiement/paiements/passerelle_paiement.py` — mode
        sandbox/mock exclusivement, aucune vraie passerelle branchée.
        """
        return self._stub.CreerSessionPaiementEnLigne(
            paiement_pb.CreerSessionPaiementRequest(
                facture_id=facture_id,
                montant=montant,
                token_espace=token_espace,
            )
        )

    def confirmer_session_paiement(self, session_id: str, token_espace: str) -> paiement_pb.SessionPaiementResponse:
        """Confirme une session de paiement en ligne (mock, espace abonné public)."""
        return self._stub.ConfirmerSessionPaiementEnLigne(
            paiement_pb.ConfirmerSessionPaiementRequest(
                session_id=session_id,
                token_espace=token_espace,
            )
        )


class NotificationServiceClient:
    """Client gRPC vers notification-service:50056 (voir proto/notification_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = notification_pb_grpc.NotificationServiceStub(self._channel)

    def envoyer_facture(self, facture_id: str, abonne_id: str) -> notification_pb.EnvoiResponse:
        return self._stub.EnvoyerFacture(
            notification_pb.EnvoyerFactureRequest(facture_id=facture_id, abonne_id=abonne_id)
        )

    def renvoyer_facture(self, facture_id: str) -> notification_pb.EnvoiResponse:
        return self._stub.ReenvoyerFacture(notification_pb.FactureIdRequest(facture_id=facture_id))

    def envoyer_recu(
        self,
        paiement_id: str,
        facture_id: str,
        abonne_id: str,
        montant: float,
        solde_restant: float,
    ) -> notification_pb.EnvoiResponse:
        """Envoie (ou renvoie) le reçu d'un versement.

        Le service de paiement appelle ce même RPC après un encaissement ; la
        gateway s'en sert pour le renvoi à la demande. Les deux passent les
        mêmes chiffres, lus au moment de l'envoi : le montant du versement, qui
        est fixe, et la dette restante, qui ne l'est pas — un reçu renvoyé six
        semaines plus tard doit annoncer le solde du jour, pas celui d'alors,
        sinon il contredit tous les autres écrans.
        """
        return self._stub.EnvoyerRecu(
            notification_pb.EnvoyerRecuRequest(
                paiement_id=paiement_id,
                facture_id=facture_id,
                abonne_id=abonne_id,
                montant=montant,
                solde_restant=solde_restant,
            )
        )

    def envoyer_relance(
        self,
        facture_id: str,
        abonne_id: str,
        etape: int,
        jours_avant_suspension: int = 0,
    ) -> notification_pb.EnvoiResponse:
        """Envoie (ou renvoie) le message de relance d'une étape donnée."""
        return self._stub.EnvoyerRelance(
            notification_pb.EnvoyerRelanceRequest(
                facture_id=facture_id,
                abonne_id=abonne_id,
                etape=etape,
                jours_avant_suspension=jours_avant_suspension,
            )
        )

    def get_envoi(self, envoi_id: str) -> notification_pb.EnvoiResponse:
        return self._stub.GetEnvoi(notification_pb.EnvoiIdRequest(envoi_id=envoi_id))

    def list_envois(self, facture_id: str = "", abonne_id: str = "") -> notification_pb.ListEnvoisResponse:
        return self._stub.ListEnvois(notification_pb.ListEnvoisRequest(facture_id=facture_id, abonne_id=abonne_id))

    def revoquer_token(self, token_id: str) -> notification_pb.StatusResponse:
        return self._stub.RevoquerToken(notification_pb.TokenIdRequest(token_id=token_id))

    def valider_token(self, token: str) -> notification_pb.ValiderTokenResponse:
        return self._stub.ValiderToken(notification_pb.ValiderTokenRequest(token=token))

    def get_whatsapp_qr(self) -> notification_pb.WhatsAppQrResponse:
        return self._stub.GetWhatsAppQr(notification_pb.EmptyRequest())

    def revoquer_tous_tokens(self) -> notification_pb.RevoquerTousTokensResponse:
        return self._stub.RevoquerTousTokens(notification_pb.EmptyRequest())

    def tester_envoi(self, phone_number: str) -> notification_pb.StatusResponse:
        return self._stub.TesterEnvoi(notification_pb.TesterEnvoiRequest(phone_number=phone_number))

    def creer_diffusion(
        self, message: str, abonne_ids: list[str], created_by: str
    ) -> notification_pb.DiffusionResponse:
        """Crée une diffusion. Le ciblage (quartier/camp/statut/sélection
        manuelle) est déjà résolu par l'appelant en liste d'abonne_id — ce
        client n'a pas à connaître la logique de filtrage des abonnés."""
        return self._stub.CreerDiffusion(
            notification_pb.CreerDiffusionRequest(message=message, abonne_ids=abonne_ids, created_by=created_by)
        )

    def get_diffusion(self, diffusion_id: str) -> notification_pb.DiffusionResponse:
        return self._stub.GetDiffusion(notification_pb.DiffusionIdRequest(diffusion_id=diffusion_id))

    def list_diffusions(self) -> notification_pb.ListDiffusionsResponse:
        return self._stub.ListDiffusions(notification_pb.EmptyRequest())


class ReportingServiceClient:
    """Client gRPC vers reporting-service:50057 (voir proto/reporting_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.REPORTING_GRPC_HOST}:{settings.REPORTING_GRPC_PORT}"
        self._channel = _canal_avec_identite(address)
        self._stub = reporting_pb_grpc.ReportingServiceStub(self._channel)

    def get_dashboard(self) -> reporting_pb.DashboardResponse:
        return self._stub.GetDashboard(reporting_pb.EmptyRequest())

    def get_stats_campagne(self, campagne_id: str) -> reporting_pb.StatsCampagneResponse:
        return self._stub.GetStatsCampagne(reporting_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_stats_globales(self) -> reporting_pb.StatsGlobalesResponse:
        return self._stub.GetStatsGlobales(reporting_pb.EmptyRequest())


auth_client = AuthServiceClient()
abonne_client = AbonneServiceClient()
config_client = ConfigServiceClient()
campagne_client = CampagneServiceClient()
facturation_client = FacturationServiceClient()
paiement_client = PaiementServiceClient()
notification_client = NotificationServiceClient()
reporting_client = ReportingServiceClient()
