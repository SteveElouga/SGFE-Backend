from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from strawberry.django.views import AsyncGraphQLView

from schema.espace_abonne import espace_abonne, espace_abonne_pdf
from schema.facturation_views import facture_pdf
from schema.schema import schema

urlpatterns = [
    path("graphql", csrf_exempt(AsyncGraphQLView.as_view(schema=schema, graphql_ide="graphiql"))),
    # PDF facture back-office (JWT + rôle ADMIN/COMPTABLE)
    path("factures/<str:facture_id>/pdf/", facture_pdf, name="facture_pdf"),
    # EF-NOTIF-003 — Espace abonné public (sans authentification, accès par token WhatsApp)
    path("espace-abonne/<str:token>/", espace_abonne, name="espace_abonne"),
    path("espace-abonne/<str:token>/facture/<str:facture_id>/pdf/", espace_abonne_pdf, name="espace_abonne_pdf"),
]
