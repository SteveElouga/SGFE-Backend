"""URLs du Paiement Service — service gRPC uniquement, pas d'API REST."""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
