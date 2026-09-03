"""Vues HTTP back-office pour les rapports & exports par campagne (écran 13).

Les exports sont des flux binaires (CSV, PDF) mal adaptés à GraphQL : ils sont
donc exposés via des vues Django classiques, protégées par JWT + rôle
(ADMIN/COMPTABLE), qui relaient les RPC des microservices propriétaires de la
donnée.

Routes :
    GET /rapports/factures.csv    → journal des factures (CSV)
    GET /rapports/paiements.csv   → journal des paiements (CSV)
    GET /rapports/synthese/pdf/   → synthèse chiffrée d'une campagne (PDF)

Les deux CSV acceptent `?campagne_id=<uuid>` **ou** `?date_debut=&date_fin=`
(ISO `AAAA-MM-JJ`, bornes incluses), ou les deux, ou aucun.

`campagne_id` était OBLIGATOIRE. Deux conséquences, toutes deux bloquantes pour
une clôture comptable :

  1. **Aucun journal par période.** Un comptable qui voulait son mois devait
     exporter campagne par campagne et recoller les fichiers à la main.
  2. **Les régularisations étaient exportables par aucun chemin.** Une
     régularisation — la dette antérieure à la mise en service, saisie à la main —
     est créée avec `campagne_id=""`, et son `SoldeFacture` aussi. Le filtre par
     campagne ne la trouvait donc jamais, ni elle ni ses paiements. La seule dette
     qu'on saisit à la main était structurellement invisible de la comptabilité.

Le bilan des impayés (PDF, global) reste sur sa route dédiée
`/bilan-impayes/pdf/` (voir facturation_views.py).
"""

import datetime
import io
import logging

import grpc
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse

from schema.context import extract_token
from schema.csv_export import csv_response as _csv_response
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


def _criteres(request: HttpRequest) -> tuple[str, str, str, str] | JsonResponse:
    """Lit et valide les critères d'export ; rend une réponse d'erreur si invalides.

    Retourne `(campagne_id, date_debut, date_fin, suffixe_de_nom_de_fichier)`.

    Aucun critère est permis — c'est ce qu'une clôture d'exercice demande — mais
    une date mal formée est refusée plutôt qu'ignorée : un export
    silencieusement non borné rendrait tout l'historique là où le comptable a
    demandé un mois, et rien ne le lui dirait avant qu'il somme la colonne.
    """
    campagne_id = request.GET.get("campagne_id", "").strip()
    debut = request.GET.get("date_debut", "").strip()
    fin = request.GET.get("date_fin", "").strip()

    for nom, valeur in (("date_debut", debut), ("date_fin", fin)):
        if valeur:
            try:
                datetime.date.fromisoformat(valeur)
            except ValueError:
                return JsonResponse(
                    {"erreur": f"{nom} doit être une date ISO AAAA-MM-JJ (reçu : {valeur})."},
                    status=400,
                )

    if debut and fin and debut > fin:
        return JsonResponse({"erreur": "date_debut doit précéder date_fin."}, status=400)

    # Le nom du fichier porte le critère : trois exports du même mois dans le
    # dossier des téléchargements doivent rester distinguables.
    if campagne_id:
        suffixe = campagne_id
    elif debut or fin:
        suffixe = f"{debut or 'debut'}_{fin or 'fin'}"
    else:
        suffixe = "tout"
    return campagne_id, debut, fin, suffixe


def factures_csv(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Exporte un journal des factures en CSV — ADMIN, COMPTABLE.

    Query-string : `campagne_id`, et/ou `date_debut`/`date_fin` (ISO, incluses).
    Aucun critère = tout l'historique.
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    criteres = _criteres(request)
    if isinstance(criteres, JsonResponse):
        return criteres
    campagne_id, date_debut, date_fin, suffixe = criteres

    try:
        # `list_factures` et non `get_factures_par_campagne` : c'est le seul des
        # deux qui accepte une période — et le seul, donc, qui voit une
        # régularisation (campagne_id vide).
        resp = facturation_client.list_factures(
            campagne_id=campagne_id,
            date_debut=date_debut,
            date_fin=date_fin,
        )
    except grpc.RpcError as exc:
        logger.error(
            "ListFactures gRPC error",
            extra={"campagne_id": campagne_id, "date_debut": date_debut, "error": str(exc)},
        )
        return JsonResponse({"erreur": "Export indisponible."}, status=503)

    # `nature`, `motif` et `date_generation` sont neufs dans cet export.
    #
    # Sans `nature`, rien ne distingue dans le fichier une facture de
    # consommation d'une régularisation — dont la consommation vaut 0 et dont
    # les index sont vides. Le comptable lisait des lignes à 0 m³ sans savoir
    # pourquoi, et n'avait aucun moyen de rapprocher le montant d'un motif.
    #
    # `date_generation` est la date sur laquelle porte le filtre de période : un
    # export borné doit porter la colonne qui a servi à le borner, sinon on ne
    # peut pas vérifier son propre extrait.
    header = [
        "numero_facture",
        "nature",
        "motif",
        "abonne_id",
        "campagne_id",
        "ancien_index",
        "nouveau_index",
        "consommation",
        "prix_m3",
        "montant",
        "statut",
        "date_generation",
        "date_releve",
        "date_limite_paiement",
    ]
    rows = [
        [
            f.numero_facture,
            getattr(f, "nature", ""),
            getattr(f, "motif", ""),
            f.abonne_id,
            f.campagne_id,
            f.ancien_index,
            f.nouveau_index,
            f.consommation,
            f.prix_m3,
            f.montant,
            f.statut,
            getattr(f, "date_generation", ""),
            f.date_releve,
            f.date_limite_paiement,
        ]
        for f in resp.factures
    ]
    return _csv_response(f"factures-{suffixe}.csv", header, rows)


def paiements_csv(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Exporte un journal des paiements en CSV — ADMIN, COMPTABLE.

    Query-string : `campagne_id`, et/ou `date_debut`/`date_fin` (ISO, incluses).
    Aucun critère = tout l'historique.
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    criteres = _criteres(request)
    if isinstance(criteres, JsonResponse):
        return criteres
    campagne_id, date_debut, date_fin, suffixe = criteres

    try:
        if date_debut or date_fin:
            # Le filtre par période porte sur `Paiement.date_paiement`, donc il
            # voit TOUS les versements — y compris ceux d'une régularisation,
            # dont le `SoldeFacture` porte un `campagne_id` vide et que
            # `ListPaiementsParCampagne` ne pouvait pas trouver.
            resp = paiement_client.list_paiements(date_debut=date_debut, date_fin=date_fin)
        elif campagne_id:
            resp = paiement_client.list_paiements_par_campagne(campagne_id)
        else:
            resp = paiement_client.list_paiements()
    except grpc.RpcError as exc:
        logger.error(
            "Export paiements gRPC error",
            extra={"campagne_id": campagne_id, "date_debut": date_debut, "error": str(exc)},
        )
        return JsonResponse({"erreur": "Export indisponible."}, status=503)

    # Les colonnes d'annulation sont neuves, et elles changent les totaux.
    #
    # Les paiements annulés étaient DÉJÀ dans l'export — ni le repo ni la vue ne
    # les excluaient — mais rien ne les signalait. Un comptable qui sommait la
    # colonne `montant` comptait donc comme recette des versements annulés, sans
    # aucun moyen de le voir. C'est le pire cas de figure pour un export
    # comptable : faux, et faux en silence.
    header = [
        "paiement_id",
        "facture_id",
        "abonne_id",
        "montant",
        "date_paiement",
        "mode_paiement",
        "reference_transaction",
        "enregistre_par",
        "annule",
        "annule_le",
        "annule_par",
        "motif_annulation",
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
            "OUI" if getattr(p, "annule", False) else "",
            getattr(p, "annule_le", ""),
            getattr(p, "annule_par", ""),
            getattr(p, "motif_annulation", ""),
        ]
        for p in resp.paiements
    ]
    return _csv_response(f"paiements-{suffixe}.csv", header, rows)


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


def recu_paiement_pdf(request: HttpRequest, paiement_id: str) -> FileResponse | JsonResponse:
    """Exporte le reçu PDF d'un versement — ADMIN, COMPTABLE.

    Chemin : `/paiements/<paiement_id>/recu/pdf/?facture_id=<uuid>`. Le
    Facturation Service assemble le versement + la situation du solde (Paiement
    Service), l'identité de l'abonné (Abonné Service) et la facture (base locale)
    puis rend un document A5 (WeasyPrint).
    """
    if request.method != "GET":
        return JsonResponse({"erreur": "Méthode non autorisée."}, status=405)
    erreur = _authoriser(request)
    if erreur is not None:
        return erreur

    facture_id = request.GET.get("facture_id", "").strip()
    if not facture_id:
        return JsonResponse({"erreur": "Paramètre facture_id requis."}, status=400)

    # `montant_versement` et `solde_restant_total` ne sont pas déductibles côté
    # Facturation (ce service ne connaît pas les paiements) : sans eux, le
    # gabarit retombait sur les défauts protobuf à 0 et affichait
    # inconditionnellement « Votre compte est à jour, plus rien n'est dû »,
    # quelle que soit la dette réelle de l'abonné. Même dérive que l'ancien
    # `solde.get("solde_restant") or 0` documentée plus bas dans ce service :
    # un zéro par défaut se lit comme un zéro réel.
    try:
        paiements = paiement_client.list_paiements(facture_id=facture_id).paiements
    except grpc.RpcError as exc:
        logger.error("ListPaiements gRPC error", extra={"paiement_id": paiement_id, "error": str(exc)})
        return JsonResponse({"erreur": "Reçu indisponible."}, status=503)

    versement = next((p for p in paiements if p.paiement_id == paiement_id), None)
    if versement is None:
        return JsonResponse({"erreur": "Paiement introuvable."}, status=404)

    try:
        solde_restant_total = paiement_client.get_dette_abonne(versement.abonne_id).total_du
    except grpc.RpcError as exc:
        logger.error("GetDetteAbonne gRPC error", extra={"paiement_id": paiement_id, "error": str(exc)})
        return JsonResponse({"erreur": "Reçu indisponible."}, status=503)

    try:
        pdf_resp = facturation_client.generer_recu_paiement_pdf(
            paiement_id,
            facture_id,
            montant_versement=versement.montant,
            solde_restant_total=solde_restant_total,
        )
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            return JsonResponse({"erreur": "Paiement introuvable."}, status=404)
        logger.error("GenererRecuPaiementPDF gRPC error", extra={"paiement_id": paiement_id, "error": str(exc)})
        return JsonResponse({"erreur": "Reçu indisponible."}, status=503)

    return FileResponse(
        io.BytesIO(pdf_resp.pdf_content),
        content_type="application/pdf",
        as_attachment=False,
        filename=pdf_resp.filename or f"recu-{paiement_id}.pdf",
    )
