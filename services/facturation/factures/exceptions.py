"""Exceptions métier du Facturation Service (mappées en codes gRPC par l'interceptor)."""

from django.core.exceptions import ValidationError


class PreconditionError(ValidationError):
    """Précondition métier non satisfaite (ex. aucun tarif actif avant génération).

    Sous-classe `ValidationError` (donc toujours attrapée par un
    `except ValidationError`), mais mappée en **FAILED_PRECONDITION** par
    l'interceptor — et non en INVALID_ARGUMENT comme une validation d'argument.
    """
