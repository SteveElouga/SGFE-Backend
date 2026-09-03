"""Commande Django pour démarrer le serveur gRPC du Facturation Service."""

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Facturation Service"

    def handle(self, *args: Any, **options: Any) -> None:
        from factures.grpc_server import serve

        self.stdout.write("Serveur gRPC Facturation démarré.")
        serve()
