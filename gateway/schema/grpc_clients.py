import sys
from pathlib import Path

import grpc
from django.conf import settings

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


class AuthServiceClient:
    """Client gRPC vers auth-service:50051 (voir proto/auth_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.AUTH_GRPC_HOST}:{settings.AUTH_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
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
        self._channel = grpc.insecure_channel(address)
        self._stub = abonne_pb_grpc.AbonneServiceStub(self._channel)

    def get_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.GetAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def list_abonnes(self, statut: str = "") -> abonne_pb.ListAbonnesResponse:
        return self._stub.ListAbonnes(abonne_pb.ListAbonnesRequest(statut=statut))

    def list_abonnes_actifs(self) -> abonne_pb.ListAbonnesResponse:
        return self._stub.ListAbonnesActifs(abonne_pb.EmptyRequest())

    def create_abonne(self, **kwargs) -> abonne_pb.AbonneResponse:
        return self._stub.CreateAbonne(abonne_pb.CreateAbonneRequest(**kwargs))

    def update_abonne(self, abonne_id: str, **kwargs) -> abonne_pb.AbonneResponse:
        return self._stub.UpdateAbonne(abonne_pb.UpdateAbonneRequest(abonne_id=abonne_id, **kwargs))

    def suspendre_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.SuspendreAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def reactiver_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ReactiverAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def resilier_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ResilierAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def update_compteur(self, abonne_id: str, **kwargs) -> abonne_pb.CompteurResponse:
        return self._stub.UpdateCompteur(abonne_pb.UpdateCompteurRequest(abonne_id=abonne_id, **kwargs))

    def remplacer_compteur(self, abonne_id: str, **kwargs) -> abonne_pb.CompteurResponse:
        return self._stub.RemplacerCompteur(abonne_pb.RemplacerCompteurRequest(abonne_id=abonne_id, **kwargs))

    def get_historique_compteur(self, abonne_id: str) -> abonne_pb.ListHistoriqueResponse:
        return self._stub.GetHistoriqueCompteur(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))


class ConfigServiceClient:
    """Client gRPC vers config-service:50058 (voir proto/config_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = config_pb_grpc.ConfigServiceStub(self._channel)

    def get_infos_societe(self) -> config_pb.InfosSocieteResponse:
        return self._stub.GetInfosSociete(config_pb.EmptyRequest())

    def update_infos_societe(self, **kwargs) -> config_pb.InfosSocieteResponse:
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
        self._channel = grpc.insecure_channel(address)
        self._stub = campagne_pb_grpc.CampagneServiceStub(self._channel)

    def create_campagne(self, **kwargs) -> campagne_pb.CampagneResponse:
        return self._stub.CreateCampagne(campagne_pb.CreateCampagneRequest(**kwargs))

    def get_campagne(self, campagne_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.GetCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def list_campagnes(self, created_by: str = "", agent_id: str = "") -> campagne_pb.ListCampagnesResponse:
        return self._stub.ListCampagnes(campagne_pb.ListCampagnesRequest(created_by=created_by, agent_id=agent_id))

    def assigner_agent(self, campagne_id: str, agent_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.AssignerAgent(campagne_pb.AssignerAgentRequest(campagne_id=campagne_id, agent_id=agent_id))

    def saisir_index(self, **kwargs) -> campagne_pb.ReleveResponse:
        return self._stub.SaisirIndex(campagne_pb.SaisirIndexRequest(**kwargs))

    def marquer_non_releve(self, **kwargs) -> campagne_pb.ReleveResponse:
        return self._stub.MarquerNonReleve(campagne_pb.MarquerNonReleveRequest(**kwargs))

    def get_releve(self, releve_id: str) -> campagne_pb.ReleveResponse:
        return self._stub.GetReleve(campagne_pb.ReleveIdRequest(releve_id=releve_id))

    def list_releves(self, campagne_id: str) -> campagne_pb.ListRelevesResponse:
        return self._stub.ListReleves(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_progression(self, campagne_id: str) -> campagne_pb.ProgressionResponse:
        return self._stub.GetProgression(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def cloturer_campagne(self, campagne_id: str) -> campagne_pb.CampagneResponse:
        return self._stub.CloturerCampagne(campagne_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_dernier_index(self, abonne_id: str) -> campagne_pb.DernierIndexResponse:
        return self._stub.GetDernierIndex(campagne_pb.AbonneIdRequest(abonne_id=abonne_id))


class FacturationServiceClient:
    """Client gRPC vers facturation-service:50054 (voir proto/facturation_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
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
        self, campagne_id: str = "", abonne_id: str = "", statut: str = ""
    ) -> facturation_pb.ListFacturesResponse:
        return self._stub.ListFactures(
            facturation_pb.ListFacturesRequest(campagne_id=campagne_id, abonne_id=abonne_id, statut=statut)
        )

    def get_factures_par_campagne(self, campagne_id: str) -> facturation_pb.ListFacturesResponse:
        return self._stub.GetFacturesParCampagne(facturation_pb.CampagneIdRequest(campagne_id=campagne_id))

    def get_facture_pdf(self, facture_id: str) -> facturation_pb.PDFResponse:
        return self._stub.GetFacturePDF(facturation_pb.FactureIdRequest(facture_id=facture_id))

    def update_statut_facture(self, facture_id: str, statut: str) -> facturation_pb.FactureResponse:
        return self._stub.UpdateStatutFacture(facturation_pb.UpdateStatutRequest(facture_id=facture_id, statut=statut))


class PaiementServiceClient:
    """Client gRPC vers paiement-service:50055 (voir proto/paiement_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = paiement_pb_grpc.PaiementServiceStub(self._channel)

    def get_solde(self, facture_id: str) -> paiement_pb.SoldeResponse:
        return self._stub.GetSolde(paiement_pb.FactureIdRequest(facture_id=facture_id))

    def list_paiements(self, facture_id: str = "", abonne_id: str = "") -> paiement_pb.ListPaiementsResponse:
        return self._stub.ListPaiements(paiement_pb.ListPaiementsRequest(facture_id=facture_id, abonne_id=abonne_id))

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

    def list_impayes(self) -> paiement_pb.ListImpayesResponse:
        return self._stub.ListImpayes(paiement_pb.EmptyRequest())

    def get_suivi_impaye(self, facture_id: str) -> paiement_pb.SuiviImpayeResponse:
        return self._stub.GetSuiviImpaye(paiement_pb.FactureIdRequest(facture_id=facture_id))


class NotificationServiceClient:
    """Client gRPC vers notification-service:50056 (voir proto/notification_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = notification_pb_grpc.NotificationServiceStub(self._channel)

    def envoyer_facture(self, facture_id: str, abonne_id: str) -> notification_pb.EnvoiResponse:
        return self._stub.EnvoyerFacture(
            notification_pb.EnvoyerFactureRequest(facture_id=facture_id, abonne_id=abonne_id)
        )

    def renvoyer_facture(self, facture_id: str) -> notification_pb.EnvoiResponse:
        return self._stub.ReenvoyerFacture(notification_pb.FactureIdRequest(facture_id=facture_id))

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


auth_client = AuthServiceClient()
abonne_client = AbonneServiceClient()
config_client = ConfigServiceClient()
campagne_client = CampagneServiceClient()
facturation_client = FacturationServiceClient()
paiement_client = PaiementServiceClient()
notification_client = NotificationServiceClient()
