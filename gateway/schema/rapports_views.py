"""Vues HTTP back-office pour les rapports & exports par campagne (écran 13).

Les exports sont des flux binaires (CSV, PDF) mal adaptés à GraphQL : ils sont
donc exposés via des vues Django classiques, protégées par JWT + rôle
(ADMIN/COMPTABLE), qui relaient les RPC des microservices propriétaires de la
donnée.

Routes (toutes en query-string `?campagne_id=<uuid>`) :
    GET /rapports/factures.csv    → liste des factures de la campagne (CSV)
    GET /rapports/paiements.csv   → liste des paiements de la campagne (CSV)
    GET /rapports/synthese/pdf/   → synthèse chiffrée de la campagne (PDF)

Le bilan des impayés (PDF, global) reste sur sa route dédiée
`/bilan-impayes/pdf/` (voir facturation_views.py).
"""

import csv
import io
import logging

import grpc
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse

from schema.context import extract_token
from schema.grpc_clients import auth_client, facturation_client, paiement_client

logger = logging.getLogger(__name__)

# Mêmes rôles que le gating GraphQL des rapports (dashboard/factures).
_ROLES_AUTORISES = ("ADMIN", "COMPTABLE")


def _authoriser(request: HttpRequest) -> JsonResponse | None:
    """Valide le JWT + le rôle ; retourne une réponse d'erreur ou None si OK.

    Authentification par JWT (`Authorization: Bearer <token>`), validé auprès
    d'auth-service — même contrat que les autres vues back-office.
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
    return None


def _csv_response(filename: str, header: list[str], rows: list[list[object]]) -> HttpResponse:
    """Sérialise des lignes en CSV UTF-8 (BOM pour Excel) en pièce jointe."""
    buffer = io.StringIO()
    # `;` comme séparateur : Excel FR l'attend par défaut pour un CSV localisé.
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    # BOM UTF-8 pour qu'Excel interprète correctement les accents.
    content = "﻿" + buffer.getvalue()
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def factures_csv(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Exporte les factures d'une campagne en CSV — ADMIN, COMPTABLE.

    Query-string : `?campagne_id=<uuid>` (obligatoire).
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    campagne_id = request.GET.get("campagne_id", "").strip()
    if not campagne_id:
        return JsonResponse({"erreur": "Paramètre campagne_id requis."}, status=400)

    try:
        resp = facturation_client.get_factures_par_campagne(campagne_id)
    except grpc.RpcError as exc:
        logger.error("GetFacturesParCampagne gRPC error", extra={"campagne_id": campagne_id, "error": str(exc)})
        return JsonResponse({"erreur": "Export indisponible."}, status=503)

    header = [
        "numero_facture",
        "abonne_id",
        "ancien_index",
        "nouveau_index",
        "consommation",
        "prix_m3",
        "montant",
        "statut",
        "date_releve",
        "date_limite_paiement",
    ]
    rows = [
        [
            f.numero_facture,
            f.abonne_id,
            f.ancien_index,
            f.nouveau_index,
            f.consommation,
            f.prix_m3,
            f.montant,
            f.statut,
            f.date_releve,
            f.date_limite_paiement,
        ]
        for f in resp.factures
    ]
    return _csv_response(f"factures-{campagne_id}.csv", header, rows)


def paiements_csv(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Exporte les paiements d'une campagne en CSV — ADMIN, COMPTABLE.

    Query-string : `?campagne_id=<uuid>` (obligatoire).
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    campagne_id = request.GET.get("campagne_id", "").strip()
    if not campagne_id:
        return JsonResponse({"erreur": "Paramètre campagne_id requis."}, status=400)

    try:
        resp = paiement_client.list_paiements_par_campagne(campagne_id)
    except grpc.RpcError as exc:
        logger.error("ListPaiementsParCampagne gRPC error", extra={"campagne_id": campagne_id, "error": str(exc)})
        return JsonResponse({"erreur": "Export indisponible."}, status=503)

    header = [
        "paiement_id",
        "facture_id",
        "abonne_id",
        "montant",
        "date_paiement",
        "mode_paiement",
        "reference_transaction",
        "enregistre_par",
    ]
    rows = [
        [
            p.paiement_id,
            p.facture_id,
            p.abonne_id,
            p.montant,
            p.date_paiement,
            p.mode_paiement,
            p.reference_transaction,
            p.enregistre_par,
        ]
        for p in resp.paiements
    ]
    return _csv_response(f"paiements-{campagne_id}.csv", header, rows)


def synthese_pdf(request: HttpRequest) -> FileResponse | JsonResponse:
    """Exporte la synthèse chiffrée d'une campagne en PDF — ADMIN, COMPTABLE.

    Query-string : `?campagne_id=<uuid>` (obligatoire). Le document agrège les
    stats des trois domaines (campagne, facturation, paiements) fournies par le
    Reporting Service et est rendu par le Facturation Service (WeasyPrint).
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    campagne_id = request.GET.get("campagne_id", "").strip()
    if not campagne_id:
        return JsonResponse({"erreur": "Paramètre campagne_id requis."}, status=400)

    try:
        pdf_resp = facturation_client.generer_synthese_campagne_pdf(campagne_id)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            return JsonResponse({"erreur": "Aucune donnée pour cette campagne."}, status=404)
        logger.error("GenererSyntheseCampagnePDF gRPC error", extra={"campagne_id": campagne_id, "error": str(exc)})
        return JsonResponse({"erreur": "Synthèse indisponible."}, status=503)

    return FileResponse(
        io.BytesIO(pdf_resp.pdf_content),
        content_type="application/pdf",
        as_attachment=False,
        filename=pdf_resp.filename or f"synthese-{campagne_id}.pdf",
    )
