"""Vues HTTP back-office pour Facturation (hors GraphQL).

Le PDF d'une facture est un flux binaire, mal adapté à GraphQL : il est donc
exposé via une vue Django classique, protégée par JWT + rôle (ADMIN/COMPTABLE),
qui relaie le RPC `GetFacturePDF` du Facturation Service.

Route : GET /factures/<facture_id>/pdf/
"""

import io
import logging

import grpc
from django.http import FileResponse, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from schema.context import extract_token
from schema.grpc_clients import auth_client, facturation_client

logger = logging.getLogger(__name__)

# Mêmes rôles que le gating GraphQL des factures (require_role ADMIN/COMPTABLE).
_ROLES_AUTORISES = ("ADMIN", "COMPTABLE")


@require_GET
def facture_pdf(request: HttpRequest, facture_id: str) -> FileResponse | JsonResponse:
    """Retourne le PDF d'une facture pour un utilisateur ADMIN ou COMPTABLE.

    Authentification par JWT (en-tête `Authorization: Bearer <token>`), validé
    auprès d'auth-service — même contrat que le gating GraphQL des factures.
    Contrairement à l'espace abonné (tokenisé), pas de contrôle d'appartenance :
    un ADMIN/COMPTABLE a le droit de consulter toutes les factures.
    """
    token = extract_token(request)
    if not token:
        return JsonResponse({"erreur": "Authentification requise."}, status=401)

    try:
        user = auth_client.validate_token(token)
    except grpc.RpcError:
        return JsonResponse({"erreur": "Token invalide ou expiré."}, status=401)

    if user.role not in _ROLES_AUTORISES:
        return JsonResponse({"erreur": "Accès non autorisé."}, status=403)

    try:
        pdf_resp = facturation_client.get_facture_pdf(facture_id)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            return JsonResponse({"erreur": "Facture introuvable."}, status=404)
        logger.error("GetFacturePDF gRPC error", extra={"facture_id": facture_id, "error": str(exc)})
        return JsonResponse({"erreur": "PDF indisponible."}, status=503)

    return FileResponse(
        io.BytesIO(pdf_resp.pdf_content),
        content_type="application/pdf",
        as_attachment=False,
        filename=f"facture-{facture_id}.pdf",
    )
