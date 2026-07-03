"""Vue publique de l'espace abonné — EF-NOTIF-003.

Accessible sans authentification via le token partagé dans le lien WhatsApp.
Route : GET /espace-abonne/<token>/
Route PDF : GET /espace-abonne/<token>/facture/<facture_id>/pdf/
"""

import logging

import grpc
from django.http import FileResponse, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from schema.grpc_clients import facturation_client, notification_client, paiement_client

logger = logging.getLogger(__name__)


def _token_response_invalide() -> JsonResponse:
    return JsonResponse({"erreur": "Token invalide ou expiré."}, status=401)


@require_GET
def espace_abonne(request: HttpRequest, token: str) -> JsonResponse:
    """Retourne toutes les factures et soldes de l'abonné identifié par le token.

    Réponse JSON :
    {
      "abonne_id": "...",
      "token_expiration": "YYYY-MM-DD",
      "factures": [
        {
          "facture_id": "...",
          "numero": "...",
          "date_releve": "...",
          "montant": 0.0,
          "statut": "...",
          "date_limite_paiement": "...",
          "solde_restant": 0.0,
          "montant_paye": 0.0
        }
      ]
    }
    """
    # Validation du token via Notification Service
    try:
        token_resp = notification_client.valider_token(token)
    except grpc.RpcError as exc:
        logger.warning("ValiderToken gRPC error", extra={"error": str(exc)})
        return _token_response_invalide()

    if not token_resp.is_valid:
        return _token_response_invalide()

    abonne_id = token_resp.abonne_id

    # Récupération des factures via Facturation Service
    try:
        factures_resp = facturation_client.list_factures(abonne_id=abonne_id)
    except grpc.RpcError as exc:
        logger.error("ListFactures gRPC error", extra={"abonne_id": abonne_id, "error": str(exc)})
        return JsonResponse({"erreur": "Impossible de récupérer les factures."}, status=503)

    # Enrichissement avec les soldes (Paiement Service)
    factures_json = []
    for f in factures_resp.factures:
        solde_restant = 0.0
        montant_paye = 0.0
        try:
            solde = paiement_client.get_solde(f.facture_id)
            solde_restant = solde.solde_restant
            montant_paye = solde.montant_paye
        except grpc.RpcError:
            pass  # Solde non disponible — on continue avec les valeurs par défaut

        factures_json.append(
            {
                "facture_id": f.facture_id,
                "numero": f.numero,
                "date_releve": f.date_releve,
                "montant": f.montant,
                "statut": f.statut,
                "date_limite_paiement": f.date_limite_paiement,
                "solde_restant": solde_restant,
                "montant_paye": montant_paye,
            }
        )

    return JsonResponse(
        {
            "abonne_id": abonne_id,
            "token_expiration": token_resp.date_expiration,
            "factures": factures_json,
        }
    )


@require_GET
def espace_abonne_pdf(request: HttpRequest, token: str, facture_id: str) -> FileResponse | JsonResponse:
    """Retourne le PDF d'une facture pour un abonné authentifié par token.

    Le token n'identifie qu'un abonné, pas une facture précise : sans la
    vérification ci-dessous, un abonné pourrait télécharger le PDF de
    n'importe quel autre abonné en devinant/énumérant un facture_id (IDOR).
    """
    try:
        token_resp = notification_client.valider_token(token)
    except grpc.RpcError:
        return _token_response_invalide()

    if not token_resp.is_valid:
        return _token_response_invalide()

    try:
        facture = facturation_client.get_facture(facture_id)
    except grpc.RpcError as exc:
        logger.error("GetFacture gRPC error", extra={"facture_id": facture_id, "error": str(exc)})
        return JsonResponse({"erreur": "Facture introuvable."}, status=404)

    if facture.abonne_id != token_resp.abonne_id:
        logger.warning(
            "Tentative d'accès à une facture d'un autre abonné via l'espace abonné",
            extra={"facture_id": facture_id, "abonne_id_token": token_resp.abonne_id},
        )
        return JsonResponse({"erreur": "Facture introuvable."}, status=404)

    try:
        pdf_resp = facturation_client.get_facture_pdf(facture_id)
    except grpc.RpcError as exc:
        logger.error("GetFacturePDF gRPC error", extra={"facture_id": facture_id, "error": str(exc)})
        return JsonResponse({"erreur": "PDF indisponible."}, status=503)

    import io

    pdf_bytes = pdf_resp.pdf_content
    return FileResponse(
        io.BytesIO(pdf_bytes),
        content_type="application/pdf",
        as_attachment=False,
        filename=f"facture-{facture_id}.pdf",
    )
