"""Validation de format/borne des entrées GraphQL les plus critiques.

Contexte : certains `@strawberry.input` n'imposent aucune contrainte de
format au niveau du type — ex. `nouveau_index: float`
(`campagne_types.SaisirIndexInput`/`CorrigerReleveInput`) accepte une valeur
négative, `telephone_whatsapp: str` (`abonne_types.CreateAbonneInput`) est
libre, une date reçue en `str` peut être n'importe quelle chaîne. Voir
docs/CONFORMITE_SOC2_OWASP.md item #10 (ASVS V2).

Strawberry (0.315, voir gateway/requirements.txt) n'a pas de mécanisme de
validation de champ natif pour un `@strawberry.input` simple (pas de
`Field(gt=0)` façon Pydantic ici) — la validation se fait donc explicitement,
en tout début de resolver, avant tout appel gRPC. Chaque validateur lève
`InputValidationError`, traduite par `GrpcErrorExtension` (voir
`extensions.py`, même patron que `AuthError`/`PermissionError`) en
`GraphQLError` avec `extensions.code = "INVALID_ARGUMENT"` — le même code
que celui déjà renvoyé pour un `INVALID_ARGUMENT` gRPC (le frontend n'a pas à
distinguer une validation locale d'une validation distante).

Les services gRPC en aval (`campagne-service`, `abonne-service`) revalident
déjà une partie de ces règles (défense en profondeur, voir
`services/campagne/campagnes/services.py`) : ces validateurs ne les
remplacent pas, ils rejettent juste le cas trivialement invalide le plus tôt
possible, avec un message clair plutôt qu'un aller-retour gRPC.
"""

from __future__ import annotations

import re
from datetime import date


class InputValidationError(Exception):
    """Entrée GraphQL invalide (format, borne) détectée avant tout appel gRPC.

    `code` est le code machine renvoyé au frontend via `extensions.code` —
    voir le docstring du module.
    """

    code = "INVALID_ARGUMENT"


# Même convention que `services/abonne/abonnes/validators.py::validate_telephone_whatsapp`
# (E.164 générique : l'Abonné Service gère des clients potentiellement hors
# Cameroun) — délibérément PAS celle, plus stricte, de
# `services/auth/comptes/validators.py::validate_phone_cameroon` (réservée aux
# comptes utilisateurs internes, toujours camerounais). `telephone_whatsapp`
# est un champ du domaine Abonné : c'est sa convention à lui qui s'applique ici.
_TELEPHONE_E164_RE = re.compile(r"^\+\d{8,15}$")


def valider_index(valeur: float, nom_champ: str) -> None:
    """Vérifie qu'un index de compteur est positif ou nul.

    Lève `InputValidationError` sinon. Défense en profondeur : les services
    (`campagne-service`, `facturation-service`) revalident déjà la cohérence
    entre ancien et nouvel index — ce contrôle-ci rejette le cas trivialement
    invalide (valeur négative) le plus tôt possible, à la gateway.
    """
    if valeur < 0:
        raise InputValidationError(f"{nom_champ} doit être positif ou nul (reçu : {valeur})")


def valider_telephone_whatsapp(valeur: str) -> None:
    """Vérifie qu'un numéro WhatsApp respecte le format E.164 attendu
    (`+<indicatif><numéro>`, 8 à 15 chiffres) — voir le docstring du module
    pour le choix de cette convention plutôt que la variante camerounaise
    stricte de l'Auth Service.
    """
    cleaned = valeur.strip().replace(" ", "").replace("-", "")
    if not cleaned or not _TELEPHONE_E164_RE.match(cleaned):
        raise InputValidationError("telephone_whatsapp invalide, format attendu : +<indicatif><numéro> (E.164)")


def valider_date_iso(valeur: str, nom_champ: str) -> None:
    """Vérifie qu'une date reçue en `@strawberry.input` (typée `str` — pas
    `datetime.date` — convention déjà en vigueur dans ce dépôt) est bien au
    format ISO 8601 `AAAA-MM-JJ`.

    Une chaîne vide est tolérée sans validation : plusieurs champs date sont
    optionnels côté service (ex. `CreateCampagneInput.date_planifiee = ""`
    signifie « non planifiée ») — seule une valeur non vide mais mal formée
    est rejetée.
    """
    if not valeur:
        return
    try:
        date.fromisoformat(valeur)
    except ValueError as exc:
        raise InputValidationError(f"{nom_champ} invalide, format attendu : AAAA-MM-JJ (ISO 8601)") from exc
