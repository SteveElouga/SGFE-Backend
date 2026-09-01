from django.conf import settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from strawberry.django.views import AsyncGraphQLView

from schema.espace_abonne import espace_abonne, espace_abonne_csv, espace_abonne_pdf
from schema.facturation_views import bilan_impayes_pdf, facture_pdf
from schema.rapports_views import factures_csv, paiements_csv, recu_paiement_pdf, synthese_pdf
from schema.schema import schema

urlpatterns = [
    # GraphiQL activé uniquement en dev ; désactivé en prod (graphql_ide=None).
    path(
        "graphql",
        csrf_exempt(
            AsyncGraphQLView.as_view(
                schema=schema,
                graphql_ide="graphiql" if settings.DEBUG else None,
            )
        ),
    ),
    # PDF facture back-office (JWT + rôle ADMIN/COMPTABLE)
    path("factures/<str:facture_id>/pdf/", facture_pdf, name="facture_pdf"),
    # PDF bilan des impayés back-office (JWT + rôle ADMIN/COMPTABLE)
    path("bilan-impayes/pdf/", bilan_impayes_pdf, name="bilan_impayes_pdf"),
    # Exports rapports par campagne (écran 13) — JWT + rôle ADMIN/COMPTABLE
    path("rapports/factures.csv", factures_csv, name="rapports_factures_csv"),
    path("rapports/paiements.csv", paiements_csv, name="rapports_paiements_csv"),
    path("rapports/synthese/pdf/", synthese_pdf, name="rapports_synthese_pdf"),
    # PDF reçu de paiement back-office (JWT + rôle ADMIN/COMPTABLE)
    path("paiements/<str:paiement_id>/recu/pdf/", recu_paiement_pdf, name="recu_paiement_pdf"),
    # EF-NOTIF-003 — Espace abonné public (sans authentification, accès par token WhatsApp)
    path("espace-abonne/<str:token>/", espace_abonne, name="espace_abonne"),
    # Relevé de compte en CSV — EF-NOTIF-003 promet « export PDF et CSV ».
    # Déclarée AVANT la route PDF n'a pas d'importance ici (les chemins ne se
    # recouvrent pas), mais après la route JSON oui : `<str:token>` ne capture
    # pas de slash, donc `factures.csv` ne peut pas être pris pour un token.
    path("espace-abonne/<str:token>/factures.csv", espace_abonne_csv, name="espace_abonne_csv"),
    path("espace-abonne/<str:token>/facture/<str:facture_id>/pdf/", espace_abonne_pdf, name="espace_abonne_pdf"),
]
