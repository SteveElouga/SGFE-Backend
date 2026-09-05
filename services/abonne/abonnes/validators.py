import re

from django.utils.translation import gettext_lazy as _

# Format E.164 générique : '+' suivi de 8 à 15 chiffres (l'Abonné Service
# gère des clients potentiellement hors Cameroun, contrairement à l'Auth
# Service qui valide le numéro des agents internes).
_E164_RE = re.compile(r"^\+\d{8,15}$")


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
