"""URLs du Facturation Service — aucune route HTTP (tout passe par gRPC)."""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
