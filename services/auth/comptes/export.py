"""Export RGPD structuré des données d'un utilisateur interne (droit à la
portabilité) — équivalent, côté Auth Service, de `abonnes/export.py` côté
Abonné Service (PR #179).

Contrairement à l'Abonné Service, l'Auth Service n'appelle aucun autre
service gRPC (voir CLAUDE.md du service) : toutes les données exportées sont
natives à ce service, il n'y a donc pas de section « externe » qui puisse
échouer pour cause de service injoignable. La dégradation gracieuse
section par section est néanmoins appliquée, par cohérence avec le reste du
dépôt et par robustesse : une exception inattendue sur UNE section (par
exemple un champ retiré par une migration future) ne doit jamais faire
échouer l'export dans son ensemble.

Contenu de l'export :
  - identité : username, e-mail (si présent), téléphone, rôle ;
  - compte : dates de création et de désactivation, statut actif ;
  - historique de connexion, dans la limite de ce qui est réellement
    trackable aujourd'hui : `last_login` (champ natif Django, hérité
    d'AbstractBaseUser) n'est pas alimenté par `AuthService.login`
    (comptes/services.py, qui authentifie et émet des jetons JWT sans jamais
    l'écrire) — documenté comme tel plutôt que silencieusement absent, même
    principe que la section `diffusions_whatsapp` côté Abonné Service
    (abonnes/export.py).

Ne lit ni n'expose jamais l'`AuditLog` (chantier séparé,
feat/piste-audit-auth) : hors périmètre de ce service au moment de cette
implémentation, et de toute façon hors périmètre RGPD de la portabilité (un
journal de sécurité écrit par le système, pas une donnée fournie par la
personne concernée).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from comptes.models import User
from comptes.repositories import UserRepository

logger = logging.getLogger(__name__)

_NOTE_HISTORIQUE_NON_TRACKE = (
    "Champ last_login natif Django (AbstractBaseUser), non alimenté aujourd'hui : "
    "AuthService.login() authentifie et émet des jetons JWT sans appeler "
    "update_last_login ni écrire d'historique de connexion dédié (comptes/services.py)."
)


class ExportService:
    """Construit l'export RGPD structuré d'un utilisateur interne."""

    def __init__(self, users: UserRepository | None = None) -> None:
        self._users = users or UserRepository()

    def _section(self, nom: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Exécute `fn`, ou dégrade gracieusement la section en cas d'échec."""
        try:
            return {"disponible": True, "donnees": fn()}
        except Exception as exc:  # dégradation gracieuse assumée — voir docstring module
            logger.warning("Export RGPD utilisateur — section %s indisponible : %s", nom, exc)
            return {"disponible": False, "raison": str(exc)}

    def _donnees_identite(self, user: User) -> dict[str, str | None]:
        return {
            "username": user.username,
            "email": user.email or None,
            "telephone": user.phone_number or None,
            "role": user.role,
        }

    def _donnees_compte(self, user: User) -> dict[str, str | bool | None]:
        return {
            "date_creation": user.created_at.isoformat(),
            "date_desactivation": user.date_desactivation.isoformat() if user.date_desactivation else None,
            "actif": user.is_active,
        }

    def _donnees_historique_connexion(self, user: User) -> dict[str, str | None]:
        if user.last_login is None:
            return {"derniere_connexion": None, "note": _NOTE_HISTORIQUE_NON_TRACKE}
        return {"derniere_connexion": user.last_login.isoformat()}

    def exporter(self, user_id: str) -> dict[str, Any]:
        """Construit l'export complet. Lève `User.DoesNotExist` si l'utilisateur
        n'existe pas — sans identité, il n'y a rien à exporter, contrairement
        aux sections qui, elles, dégradent gracieusement."""
        user = self._users.get_by_id(user_id)
        return {
            "genere_le": datetime.now(UTC).isoformat(),
            "user_id": str(user.id),
            "identite": self._section("identite", lambda: self._donnees_identite(user)),
            "compte": self._section("compte", lambda: self._donnees_compte(user)),
            "historique_connexion": self._section(
                "historique_connexion", lambda: self._donnees_historique_connexion(user)
            ),
        }


def exporter_donnees_utilisateur(user_id: str) -> dict[str, Any]:
    """Point d'entrée fonctionnel, partagé par la commande de management et
    le servicer gRPC (`ExporterDonneesUtilisateur`)."""
    return ExportService().exporter(user_id)


def exporter_donnees_utilisateur_json(user_id: str) -> str:
    """Même export, sérialisé en JSON structuré et lisible (indenté)."""
    return json.dumps(exporter_donnees_utilisateur(user_id), ensure_ascii=False, indent=2)
