import re

from django.utils.translation import gettext_lazy as _

# Numéros mobiles camerounais : +237 suivi de 9 chiffres commençant par 6
_CAMEROON_PHONE_RE = re.compile(r"^\+2376\d{8}$")


def validate_phone_cameroon(phone: str) -> str:
    """Valide un numéro de téléphone camerounais et le retourne normalisé.

    Format attendu : +2376XXXXXXXX (13 caractères, préfixe mobile camerounais).
    Lève ValueError si le format est invalide.
    """
    if not phone:
        raise ValueError(_("Le numéro de téléphone est obligatoire"))
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if not _CAMEROON_PHONE_RE.match(cleaned):
        raise ValueError(
            _(
                "Numéro de téléphone invalide — format attendu : +2376XXXXXXXX "
                "(indicatif +237, suivi de 9 chiffres commençant par 6)"
            )
        )
    return cleaned
