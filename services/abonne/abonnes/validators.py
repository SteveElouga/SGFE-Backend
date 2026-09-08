import re
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext_lazy as _

# Format E.164 générique : '+' suivi de 8 à 15 chiffres (l'Abonné Service
# gère des clients potentiellement hors Cameroun, contrairement à l'Auth
# Service qui valide le numéro des agents internes).
_E164_RE = re.compile(r"^\+\d{8,15}$")

# Bornes géographiques plausibles (import CSV de coordonnées de compteurs).
_LATITUDE_MIN = Decimal("-90")
_LATITUDE_MAX = Decimal("90")
_LONGITUDE_MIN = Decimal("-180")
_LONGITUDE_MAX = Decimal("180")


class ValidationError(Exception):
    """Violation d'une règle métier (ex. abonné non actif, index invalide, téléphone invalide)."""


def validate_telephone_whatsapp(telephone: str) -> str:
    """Valide un numéro WhatsApp au format E.164 et le retourne normalisé.

    Lève ValidationError si le format est invalide.
    """
    if not telephone:
        raise ValidationError(_("Le numéro WhatsApp est obligatoire"))
    cleaned = telephone.strip().replace(" ", "").replace("-", "")
    if not _E164_RE.match(cleaned):
        raise ValidationError(_("Numéro WhatsApp invalide, format attendu : +<indicatif><numéro> (E.164)"))
    return cleaned


def _validate_decimal_dans_bornes(valeur: str, nom_champ: str, borne_min: Decimal, borne_max: Decimal) -> Decimal:
    """Parse `valeur` en `Decimal` et vérifie qu'elle reste dans `[borne_min, borne_max]`.

    Lève ValidationError si `valeur` n'est pas un nombre décimal valide, ou si
    elle est hors bornes — jamais de valeur corrompue acceptée silencieusement
    (import CSV de coordonnées, voir CompteurService.importer_coordonnees).
    """
    try:
        decimale = Decimal(valeur)
    except (InvalidOperation, TypeError):
        raise ValidationError(
            _("{champ} invalide : {valeur!r} n'est pas un nombre décimal").format(champ=nom_champ, valeur=valeur)
        ) from None
    if decimale < borne_min or decimale > borne_max:
        raise ValidationError(
            _("{champ} hors des bornes plausibles [{min}, {max}] : {valeur}").format(
                champ=nom_champ, min=borne_min, max=borne_max, valeur=decimale
            )
        )
    return decimale


def validate_latitude(valeur: str) -> Decimal:
    """Valide une latitude brute (chaîne, telle que lue dans un CSV) : doit
    être un `Decimal` valide dans `[-90, 90]`. Lève ValidationError sinon."""
    return _validate_decimal_dans_bornes(valeur, "latitude", _LATITUDE_MIN, _LATITUDE_MAX)


def validate_longitude(valeur: str) -> Decimal:
    """Valide une longitude brute (chaîne, telle que lue dans un CSV) : doit
    être un `Decimal` valide dans `[-180, 180]`. Lève ValidationError sinon."""
    return _validate_decimal_dans_bornes(valeur, "longitude", _LONGITUDE_MIN, _LONGITUDE_MAX)
