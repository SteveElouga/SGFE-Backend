"""Vue publique de l'espace abonné — EF-NOTIF-003.

Accessible sans authentification via le token partagé dans le lien WhatsApp.
Route JSON       : GET  /espace-abonne/<token>/
Route PDF        : GET  /espace-abonne/<token>/facture/<facture_id>/pdf/
Route CSV        : GET  /espace-abonne/<token>/factures.csv
Paiement en ligne : POST /espace-abonne/<token>/paiement/
                    POST /espace-abonne/<token>/paiement/<session_id>/confirmer/

EF-NOTIF-003 demande « toutes les factures (avec statut), historique de
consommation, statut des paiements » et « boutons d'export : PDF et CSV ».
§8.3 du SRS le redemande. Deux des quatre manquaient :

* la **consommation** — l'abonné voyait des montants, jamais ses mètres cubes.
  Il ne pouvait donc pas vérifier sa facture. Les champs étaient dans
  `FactureResponse` depuis toujours ; personne ne les recopiait.
* l'**export CSV** — seules la vue JSON et le PDF d'une facture existaient.

Le **paiement en ligne** relance la décision §10.2 de l'audit, qui l'avait
écartée (« consultation seule, paiement en ligne reporté »). Implémenté en
mode **sandbox/mock exclusivement** — voir
`services/paiement/paiements/passerelle_paiement.py` : aucune vraie
passerelle n'est branchée, la décision §10.2 est levée mais PAS remplacée par
un vrai paiement en production.
"""

import json
import logging

import grpc
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from schema.csv_export import csv_response
from schema.dtos import DonneesAbonneDict, FactureEspaceDict
from schema.grpc_clients import facturation_client, notification_client, paiement_client

logger = logging.getLogger(__name__)


def _token_response_invalide() -> JsonResponse:
    return JsonResponse({"erreur": "Token invalide ou expiré."}, status=401)


def _donnees_abonne(token: str) -> tuple[DonneesAbonneDict | None, JsonResponse | None]:
    """Collecte tout ce que l'espace abonné montre, ou l'erreur à renvoyer.

    Rend `(donnees, None)` ou `(None, reponse_d_erreur)`.

    Extraite en fonction le jour où l'export CSV est arrivé : la validation du
    token, la lecture des factures et l'enrichissement par les soldes sont
    identiques pour les deux vues. Les écrire deux fois, c'est se garantir qu'un
    jour l'une des deux montrera autre chose que l'autre — sur des données que
    l'abonné va comparer.

    Structure rendue :
    {
      "abonne_id": "...",
      "token_expiration": "YYYY-MM-DD",
      "avoir": 0.0,
      "factures": [
        {
          "facture_id": "...",
          "numero": "...",
          "date_releve": "...",
          "montant": 0.0,
          "statut": "...",
          "date_limite_paiement": "...",
          "solde_restant": 0.0,
          "montant_paye": 0.0,
          "ancien_index": 0.0,
          "nouveau_index": 0.0,
          "consommation": 0.0,
          "prix_m3": 0.0,
          "nature": "CONSOMMATION" | "REGULARISATION",
          "motif": ""
        }
      ]
    }
    """
    # Validation du token via Notification Service
    try:
        token_resp = notification_client.valider_token(token)
    except grpc.RpcError as exc:
        logger.warning("ValiderToken gRPC error", extra={"error": str(exc)})
        return None, _token_response_invalide()

    if not token_resp.is_valid:
        return None, _token_response_invalide()

    abonne_id = token_resp.abonne_id

    # Récupération des factures via Facturation Service
    try:
        factures_resp = facturation_client.list_factures(abonne_id=abonne_id)
    except grpc.RpcError as exc:
        logger.error("ListFactures gRPC error", extra={"abonne_id": abonne_id, "error": str(exc)})
        return None, JsonResponse({"erreur": "Impossible de récupérer les factures."}, status=503)

    # Enrichissement avec les soldes (Paiement Service)
    factures_json: list[FactureEspaceDict] = []
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
                "numero": f.numero_facture,
                "date_releve": f.date_releve,
                "montant": f.montant,
                "statut": f.statut,
                "date_limite_paiement": f.date_limite_paiement,
                "solde_restant": solde_restant,
                "montant_paye": montant_paye,
                # ── Ce qui justifie le montant ─────────────────────────────
                #
                # L'abonné voyait des montants, jamais ses mètres cubes. Il ne
                # pouvait donc pas vérifier sa facture : sans les index et la
                # consommation, un montant n'est qu'un chiffre à croire.
                #
                # EF-NOTIF-003 le demande — « historique de consommation » — et
                # §8.3 le redemande. Les quatre champs étaient dans
                # `FactureResponse` depuis toujours ; personne ne les recopiait.
                #
                # Sur une régularisation, ils valent zéro : c'est le rôle de
                # `nature` et `motif` d'expliquer le montant à leur place.
                "ancien_index": f.ancien_index,
                "nouveau_index": f.nouveau_index,
                "consommation": f.consommation,
                "prix_m3": f.prix_m3,
                # Une régularisation n'a pas de relevé : sans sa nature et son
                # motif, l'abonné lit un montant qu'aucun index ne justifie.
                "nature": f.nature or "CONSOMMATION",
                "motif": f.motif or "",
            }
        )

    # Avoir disponible : ce que la régie doit à l'abonné. Sans lui, un client
    # dont la facture suivante sera réduite d'un trop-perçu lit un montant qu'il
    # ne peut pas rapprocher de sa consommation — et croit à une erreur.
    avoir = 0.0
    try:
        avoir = float(paiement_client.get_avoir_abonne(abonne_id).montant)
    except grpc.RpcError as exc:
        logger.warning("Avoir indisponible", extra={"abonne_id": abonne_id, "error": str(exc)})

    return {
        "abonne_id": abonne_id,
        "token_expiration": token_resp.date_expiration,
        "avoir": avoir,
        "factures": factures_json,
    }, None


@require_GET
def espace_abonne(request: HttpRequest, token: str) -> JsonResponse:
    """Vue JSON de l'espace abonné — factures, soldes, consommation, avoir."""
    donnees, erreur = _donnees_abonne(token)
    if erreur is not None:
        return erreur
    return JsonResponse(donnees)


@require_GET
def espace_abonne_csv(request: HttpRequest, token: str) -> HttpResponse | JsonResponse:
    """Export CSV du relevé de compte de l'abonné — EF-NOTIF-003, §8.3.

    Le SRS promet des « boutons d'export : PDF et CSV » à deux endroits. Seuls le
    PDF d'une facture et la vue JSON existaient : l'abonné pouvait télécharger
    une facture à la fois, jamais l'état de son compte.

    Le même token que le reste de l'espace abonné, donc les mêmes garanties : il
    n'identifie qu'un abonné, et ne donne accès qu'à ses factures.
    """
    donnees, erreur = _donnees_abonne(token)
    if erreur is not None:
        return erreur
    assert donnees is not None

    header = [
        "numero_facture",
        "nature",
        "motif",
        "date_releve",
        "ancien_index",
        "nouveau_index",
        "consommation_m3",
        "prix_m3",
        "montant",
        "montant_paye",
        "solde_restant",
        "statut",
        "date_limite_paiement",
    ]
    rows = [
        [
            f["numero"],
            f["nature"],
            f["motif"],
            f["date_releve"],
            f["ancien_index"],
            f["nouveau_index"],
            f["consommation"],
            f["prix_m3"],
            f["montant"],
            f["montant_paye"],
            f["solde_restant"],
            f["statut"],
            f["date_limite_paiement"],
        ]
        for f in donnees["factures"]
    ]

    # Le nom porte l'identifiant de l'abonné, pas son token : un fichier reste
    # dans un dossier de téléchargements, et un token dans un nom de fichier est
    # un identifiant d'accès qui traîne.
    return csv_response(f"mon-compte-{donnees['abonne_id'][:8]}.csv", header, rows)


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


@csrf_exempt
@require_POST
def espace_abonne_paiement_creer(request: HttpRequest, token: str) -> JsonResponse:
    """Ouvre une session de paiement en ligne (mock) — POST /espace-abonne/<token>/paiement/.

    Body JSON `{facture_id, montant}` → `{session_id, url_redirection, expire_a}`.

    **Mode sandbox/mock exclusivement** (voir `passerelle_paiement.py`
    côté Paiement Service) : `url_redirection` pointe vers le mock de
    confirmation du frontend, jamais vers un vrai fournisseur.
    """
    token_resp = notification_client.valider_token(token)
    if not token_resp.is_valid:
        return _token_response_invalide()

    try:
        payload = json.loads(request.body)
        facture_id = str(payload["facture_id"])
        montant = float(payload["montant"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"erreur": "Requête invalide : facture_id et montant sont obligatoires."}, status=400)

    try:
        session_resp = paiement_client.creer_session_paiement(
            facture_id=facture_id, montant=montant, token_espace=token
        )
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.INVALID_ARGUMENT:
            return JsonResponse({"erreur": "Montant invalide."}, status=400)
        logger.error("CreerSessionPaiementEnLigne gRPC error", extra={"facture_id": facture_id, "error": str(exc)})
        return JsonResponse({"erreur": "Impossible de créer la session de paiement."}, status=503)

    return JsonResponse(
        {
            "session_id": session_resp.session_id,
            "url_redirection": session_resp.url_redirection,
            "expire_a": session_resp.expire_a,
        }
    )


@csrf_exempt
@require_POST
def espace_abonne_paiement_confirmer(request: HttpRequest, token: str, session_id: str) -> JsonResponse:
    """Confirme une session de paiement en ligne (mock).

    POST /espace-abonne/<token>/paiement/<session_id>/confirmer/ → `{statut}`
    (`"CONFIRMEE"` | `"ECHOUEE"` | `"EXPIREE"`).

    Anti-IDOR : le token doit être EXACTEMENT celui qui a créé la session,
    sinon 404 — comme le reste de l'espace abonné (voir `espace_abonne_pdf`,
    ANO-002). Un token invalide/expiré renvoie 401, avant même d'appeler le RPC.
    """
    token_resp = notification_client.valider_token(token)
    if not token_resp.is_valid:
        return _token_response_invalide()

    try:
        session_resp = paiement_client.confirmer_session_paiement(session_id=session_id, token_espace=token)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            return JsonResponse({"erreur": "Session de paiement introuvable."}, status=404)
        logger.error("ConfirmerSessionPaiementEnLigne gRPC error", extra={"session_id": session_id, "error": str(exc)})
        return JsonResponse({"erreur": "Confirmation indisponible."}, status=503)

    return JsonResponse({"statut": session_resp.statut})
