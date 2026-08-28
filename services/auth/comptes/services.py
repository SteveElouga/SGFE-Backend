from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from comptes.email_client import email_client
from comptes.models import User, _generate_otp
from comptes.repositories import (
    PasswordSetupTokenRepository,
    PhoneOtpTokenRepository,
    RevokedTokenRepository,
    UserRepository,
)
from comptes.validators import validate_phone_cameroon
from comptes.whatsapp_client import whatsapp_client


_MSG_INVALID_CREDENTIALS = "Identifiants invalides"

# Hash factice servant à égaliser le temps de réponse du login quand
# l'identifiant est inconnu : on exécute quand même un check_password (coût
# bcrypt) pour ne pas offrir d'oracle temporel d'énumération des comptes.
_DUMMY_PASSWORD_HASH = make_password("anti-timing-enumeration")


class AuthenticationError(Exception):
    """Échec d'authentification (identifiants invalides, compte verrouillé/inactif)."""


def _refuser_si_anterieur_au_mot_de_passe(user, jeton) -> None:
    """Refuse un jeton émis avant le dernier changement de mot de passe.

    Changer son mot de passe parce qu'on pense son compte compromis n'a de sens
    que si le geste ferme les sessions de l'intrus. La liste noire ne peut pas
    le faire : elle révoque un jeton nommément, et les jetons émis ne sont
    stockés nulle part.

    Comparer la date d'émission (`iat`) à l'horodatage du changement révoque
    d'un coup tout ce qui a été émis avant, sur tous les appareils, sans rien
    stocker par session.

    Les comptes dont le mot de passe n'a jamais changé depuis l'ajout du champ
    passent sans contrainte : leur `password_changed_at` est nul, et refuser
    leurs jetons déconnecterait tout le monde au déploiement.
    """
    change_le = getattr(user, "password_changed_at", None)
    if change_le is None:
        return
    emis_le = jeton.payload.get("iat")
    if emis_le is None:
        return

    # La comparaison se fait à la seconde entière, des deux côtés.
    #
    # `iat` est une seconde epoch — c'est ce que la spécification JWT prévoit —
    # tandis que l'horodatage en base porte les microsecondes. Comparer les deux
    # tels quels refuse le jeton émis dans la même seconde que le changement :
    # c'est-à-dire celui qu'on vient de délivrer à la personne qui vient de
    # changer son mot de passe. Elle serait déconnectée à l'instant même où elle
    # se reconnecte.
    #
    # Tronquer les deux à la seconde laisse passer ce cas et ne rouvre rien :
    # la fenêtre est celle d'une seconde partagée avec l'acte de changement.
    if int(emis_le) < int(change_le.timestamp()):
        raise AuthenticationError("Session fermée par un changement de mot de passe")


class AuthService:
    """Logique métier d'authentification et de gestion des tokens JWT."""

    def __init__(self) -> None:
        self.users = UserRepository()
        self.revoked_tokens = RevokedTokenRepository()

    def login(self, identifier: str, password: str) -> tuple[str, str, int]:
        """Authentifie un utilisateur par son username ou son numéro de téléphone.

        Anti-énumération : un identifiant inconnu, un mauvais mot de passe et un
        compte verrouillé renvoient le MÊME message générique (et le même coût de
        calcul), pour ne révéler ni l'existence d'un compte ni son état. Le
        verrouillage continue de bloquer les tentatives (défense anti-bruteforce).
        Seul un compte désactivé par un admin est signalé explicitement — et
        uniquement après vérification du mot de passe, donc sans valeur
        d'énumération (il faut déjà connaître le mot de passe).
        """
        try:
            user = self.users.get_by_username_or_phone(identifier)
        except ObjectDoesNotExist as exc:
            # Égalise le temps de réponse avec le cas « compte connu, mauvais
            # mot de passe » (pas d'oracle temporel d'énumération).
            check_password(password, _DUMMY_PASSWORD_HASH)
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS) from exc

        if user.locked_until and user.locked_until > timezone.now():
            # Verrou actif : on bloque sans révéler l'état « verrouillé ».
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS)

        if not check_password(password, user.password):
            self._enregistrer_echec(user)
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS)

        if not user.is_active:
            raise AuthenticationError("Compte désactivé")

        user.failed_attempts = 0
        user.locked_until = None
        self.users.save(user)

        return self._generer_tokens(user)

    def _enregistrer_echec(self, user: User) -> None:
        user.failed_attempts += 1
        if user.failed_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)
        self.users.save(user)

    def _generer_tokens(self, user: User) -> tuple[str, str, int]:
        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        access = refresh.access_token
        access["role"] = user.role
        expires_in = int(settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds())
        return str(access), str(refresh), expires_in

    def validate_token(self, token: str) -> User:
        try:
            access = AccessToken(token)
        except TokenError as exc:
            raise AuthenticationError("Token invalide ou expiré") from exc

        if self.revoked_tokens.is_revoked(access["jti"]):
            raise AuthenticationError("Token révoqué")

        try:
            user = self.users.get_by_id(access["user_id"])
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Utilisateur introuvable") from exc

        _refuser_si_anterieur_au_mot_de_passe(user, access)
        return user

    def refresh_token(self, refresh_token: str) -> tuple[str, str, int]:
        try:
            refresh = RefreshToken(refresh_token)
        except TokenError as exc:
            raise AuthenticationError("Refresh token invalide ou expiré") from exc

        if self.revoked_tokens.is_revoked(refresh["jti"]):
            raise AuthenticationError("Refresh token révoqué")

        try:
            user = self.users.get_by_id(refresh["user_id"])
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Utilisateur introuvable") from exc

        _refuser_si_anterieur_au_mot_de_passe(user, refresh)

        nouveaux_tokens = self._generer_tokens(user)

        # Rotation : révoque l'ancien refresh token maintenant qu'un nouveau
        # couple a été émis. Sans cela, un refresh token intercepté reste
        # utilisable jusqu'à son expiration naturelle (7j) même après avoir
        # servi une fois.
        self.revoked_tokens.revoke(
            token_jti=refresh["jti"],
            expires_at=timezone.datetime.fromtimestamp(refresh["exp"], tz=timezone.get_current_timezone()),
        )

        return nouveaux_tokens

    def logout(self, token: str) -> None:
        try:
            access = AccessToken(token)
        except TokenError as exc:
            raise AuthenticationError("Token invalide") from exc

        self.revoked_tokens.revoke(
            token_jti=access["jti"],
            expires_at=timezone.datetime.fromtimestamp(access["exp"], tz=timezone.get_current_timezone()),
        )


class UserAdminService:
    """Gestion CRUD des utilisateurs (réservée au rôle ADMIN, vérifié côté appelant)."""

    def __init__(self) -> None:
        self.users = UserRepository()
        self.password_setup = PasswordSetupService()
        self.phone_otp = PhoneOtpService()

    def create_user(self, username: str, phone_number: str, role: str, email: str = "") -> User:
        """Crée un utilisateur et déclenche le flux d'activation adapté au rôle.

        ADMIN : activation par e-mail (lien de définition du mot de passe).
        Autres rôles : activation par OTP WhatsApp.
        """
        phone = validate_phone_cameroon(phone_number)

        if role == "ADMIN":
            if not email:
                raise ValueError("L'e-mail est obligatoire pour le rôle ADMIN")
            user = self.users.create(username=username, email=email, phone_number=phone, role=role)
            self.password_setup.send_activation_email(user)
        else:
            user = self.users.create(username=username, phone_number=phone, role=role)
            self.phone_otp.send_otp(user)

        return user

    def update_user(self, user_id: str, email: str, role: str, phone_number: str = "") -> User:
        """Met à jour un utilisateur.

        Tant que le compte n'est pas encore activé, les identifiants d'activation
        suivent le contact corrigé — sans jamais désactiver un compte déjà actif :
        - non-ADMIN dont le téléphone change : nouvel OTP WhatsApp sur le nouveau numéro ;
        - ADMIN dont l'e-mail change : nouveau lien d'activation sur le nouvel e-mail.

        Un compte déjà activé n'est jamais impacté par un changement de contact.
        """
        user = self.users.get_by_id(user_id)
        old_phone = user.phone_number
        old_email = user.email
        if email:
            user.email = email
        if role:
            user.role = role
        if phone_number:
            user.phone_number = validate_phone_cameroon(phone_number)
        saved_user = self.users.save(user)

        phone_changed = phone_number and saved_user.phone_number != old_phone
        email_changed = email and saved_user.email != old_email
        pending_activation = not saved_user.is_active and not saved_user.has_usable_password()
        if pending_activation:
            if saved_user.role != "ADMIN" and phone_changed:
                self.phone_otp.send_otp(saved_user)
            elif saved_user.role == "ADMIN" and email_changed:
                self.password_setup.send_activation_email(saved_user)

        return saved_user

    def deactivate_user(self, user_id: str, caller_id: str | None = None) -> User:
        """Désactive un compte, sauf s'il s'agit du propre compte de l'appelant
        ou du dernier administrateur actif du système (protège contre le
        verrouillage total de l'administration)."""
        user = self.users.get_by_id(user_id)

        if caller_id and str(user.id) == str(caller_id):
            raise ValueError("Vous ne pouvez pas désactiver votre propre compte")

        if user.role == "ADMIN" and user.is_active and self.users.count_active_admins(exclude_id=user.id) == 0:
            raise ValueError("Impossible de désactiver le dernier administrateur actif")

        user.is_active = False
        return self.users.save(user)

    def reactivate_user(self, user_id: str) -> User:
        user = self.users.get_by_id(user_id)
        user.is_active = True
        return self.users.save(user)

    def resend_credentials(self, user_id: str) -> User:
        """Renvoi manuel (déclenché par un admin) des identifiants d'accès.

        Envoie un lien d'activation si le compte est encore en attente (jamais
        de mot de passe défini), sinon un lien/OTP de réinitialisation — sert
        aussi bien pour « Renvoyer le lien d'activation » que pour
        « Réinitialiser le mot de passe » côté frontend, ces deux actions
        partageant le même mécanisme sous-jacent (voir PasswordSetupService).
        """
        user = self.users.get_by_id(user_id)
        pending_activation = not user.has_usable_password()
        if user.role == "ADMIN":
            if pending_activation:
                self.password_setup.send_activation_email(user)
            else:
                self.password_setup.send_password_reset_email(user)
        else:
            self.phone_otp.send_otp(user)
        return user

    def get_user(self, user_id: str) -> User:
        return self.users.get_by_id(user_id)

    def list_users(self) -> list[User]:
        return self.users.list_all()


class PasswordSetupService:
    """Activation de compte et réinitialisation de mot de passe par e-mail (ADMIN uniquement).

    Les deux flux partagent le même mécanisme : un token à usage unique,
    limité dans le temps, envoyé par e-mail, qui permet de définir un
    nouveau mot de passe.
    """

    def __init__(self) -> None:
        self.users = UserRepository()
        self.tokens = PasswordSetupTokenRepository()

    def _create_token_and_send(self, user: User, subject: str, intro_html: str) -> None:
        expires_at = timezone.now() + timedelta(hours=settings.PASSWORD_SETUP_TOKEN_VALIDITY_HOURS)
        setup_token = self.tokens.create(user=user, expires_at=expires_at)
        link = f"{settings.FRONTEND_URL}/set-password?token={setup_token.token}"
        email_client.send(
            to_email=user.email,
            to_name=user.username,
            subject=subject,
            html_content=f'{intro_html}<p><a href="{link}">{link}</a></p>'
            f"<p>Ce lien expire dans {settings.PASSWORD_SETUP_TOKEN_VALIDITY_HOURS} heures.</p>",
        )

    def send_activation_email(self, user: User) -> None:
        self._create_token_and_send(
            user,
            subject="Activez votre compte SGFE",
            intro_html=f"<p>Bonjour {user.username},</p><p>Votre compte a été créé. Définissez votre mot de passe :</p>",
        )

    def send_password_reset_email(self, user: User) -> None:
        self._create_token_and_send(
            user,
            subject="Réinitialisation de votre mot de passe SGFE",
            intro_html=f"<p>Bonjour {user.username},</p><p>Cliquez sur le lien pour définir un nouveau mot de passe :</p>",
        )

    def request_password_reset(self, email: str) -> None:
        # Ne jamais révéler si l'e-mail existe ou non : succès silencieux dans les deux cas.
        try:
            user = self.users.get_by_email(email)
        except ObjectDoesNotExist:
            return
        # Seul l'ADMIN peut réinitialiser par e-mail
        if user.role != "ADMIN":
            return
        self.send_password_reset_email(user)

    def set_password_with_token(self, token: str, new_password: str) -> None:
        try:
            setup_token = self.tokens.get_valid(token)
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Token invalide") from exc

        if not setup_token.is_valid():
            raise AuthenticationError("Token invalide ou expiré")

        from django.db import transaction

        user = setup_token.user
        with transaction.atomic():
            user.set_password(new_password)
            user.is_active = True
            # Ferme toutes les sessions ouvertes : c'est ce qu'attend quelqu'un
            # qui change son mot de passe parce qu'il pense son compte compromis.
            user.password_changed_at = timezone.now()
            self.users.save(user)
            setup_token.mark_used()


class PhoneOtpService:
    """Activation de compte et réinitialisation de mot de passe par OTP WhatsApp.

    Utilisé pour tous les rôles (ADMIN peut aussi passer par ce flux en plus de l'e-mail).
    L'OTP est valide pendant PHONE_OTP_VALIDITY_MINUTES minutes, à usage unique.
    """

    def __init__(self) -> None:
        self.users = UserRepository()
        self.otp_tokens = PhoneOtpTokenRepository()

    def send_otp(self, user: User) -> None:
        """Génère un OTP, invalide les précédents, et l'envoie par WhatsApp."""
        self.otp_tokens.invalidate_previous(user)
        raw_otp = _generate_otp()
        expires_at = timezone.now() + timedelta(minutes=settings.PHONE_OTP_VALIDITY_MINUTES)
        self.otp_tokens.create(user=user, raw_otp=raw_otp, expires_at=expires_at)
        phone_encoded = user.phone_number.replace("+", "%2B")
        activation_url = f"{settings.FRONTEND_URL}/activer-compte?phone={phone_encoded}"
        whatsapp_client.send(
            to_phone=user.phone_number,
            message=(
                f"Bonjour {user.username},\n\n"
                f"Votre code d'activation SGFE : *{raw_otp}*\n\n"
                f"Activez votre compte ici :\n{activation_url}\n\n"
                f"Saisissez le code et définissez votre mot de passe.\n"
                f"Valable {settings.PHONE_OTP_VALIDITY_MINUTES} minutes. Ne partagez jamais ce code."
            ),
        )

    def request_otp_by_phone(self, phone_number: str) -> None:
        """Point d'entrée public : résout l'utilisateur par téléphone et envoie un OTP.

        Succès silencieux si le numéro n'existe pas (ne révèle pas les comptes).
        """
        phone = validate_phone_cameroon(phone_number)
        try:
            user = self.users.get_by_phone(phone)
        except ObjectDoesNotExist:
            return
        # Bloquer les comptes explicitement désactivés par un admin (is_active=False
        # + mot de passe utilisable). Les comptes en attente d'activation
        # (is_active=False + mot de passe inutilisable) peuvent recevoir un OTP
        # pour le renvoi du code d'activation.
        if not user.is_active and user.has_usable_password():
            return
        self.send_otp(user)

    def verify_otp_and_set_password(self, phone_number: str, otp_code: str, new_password: str) -> None:
        """Vérifie l'OTP et définit le nouveau mot de passe si valide."""
        phone = validate_phone_cameroon(phone_number)

        try:
            user = self.users.get_by_phone(phone)
        except ObjectDoesNotExist as exc:
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS) from exc

        otp_token = self.otp_tokens.get_latest_valid(user)
        if otp_token is None or not otp_token.is_valid():
            raise AuthenticationError("Code OTP invalide ou expiré")

        if not otp_token.check_otp(otp_code):
            # Comptabilise l'échec et invalide le token au-delà du plafond :
            # sans cela le code à 6 chiffres serait brute-forçable pendant toute
            # la fenêtre de validité (aucun verrou côté vérification OTP).
            otp_token.register_failed_attempt(settings.MAX_OTP_ATTEMPTS)
            raise AuthenticationError("Code OTP invalide ou expiré")

        from django.db import transaction

        with transaction.atomic():
            user.set_password(new_password)
            user.is_active = True
            # Même règle que pour le reset par e-mail : le nouveau mot de passe
            # ferme ce qui était ouvert avant lui.
            user.password_changed_at = timezone.now()
            self.users.save(user)
            otp_token.mark_used()
