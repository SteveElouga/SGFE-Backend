"""Configuration des URLs du Notification Service.

Ce service n'expose pas d'API HTTP — tout passe par gRPC.
"""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
