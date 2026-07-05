from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from strawberry.django.views import AsyncGraphQLView

from schema.espace_abonne import espace_abonne, espace_abonne_pdf
from schema.facturation_views import bilan_impayes_pdf, facture_pdf
from schema.rapports_views import factures_csv, paiements_csv, synthese_pdf
from schema.schema import schema

urlpatterns = [
    path("graphql", csrf_exempt(AsyncGraphQLView.as_view(schema=schema, graphql_ide="graphiql"))),
    # PDF facture back-office (JWT + rôle ADMIN/COMPTABLE)
    path("factures/<str:facture_id>/pdf/", facture_pdf, name="facture_pdf"),
    # PDF bilan des impayés back-office (JWT + rôle ADMIN/COMPTABLE)
    path("bilan-impayes/pdf/", bilan_impayes_pdf, name="bilan_impayes_pdf"),
    # Exports rapports par campagne (écran 13) — JWT + rôle ADMIN/COMPTABLE
    path("rapports/factures.csv", factures_csv, name="rapports_factures_csv"),
    path("rapports/paiements.csv", paiements_csv, name="rapports_paiements_csv"),
    path("rapports/synthese/pdf/", synthese_pdf, name="rapports_synthese_pdf"),
    # EF-NOTIF-003 — Espace abonné public (sans authentification, accès par token WhatsApp)
    path("espace-abonne/<str:token>/", espace_abonne, name="espace_abonne"),
    path("espace-abonne/<str:token>/facture/<str:facture_id>/pdf/", espace_abonne_pdf, name="espace_abonne_pdf"),
]
