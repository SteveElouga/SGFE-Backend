"""Commande Django pour démarrer le serveur gRPC du Paiement Service."""

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Paiement Service"

    def handle(self, *args: Any, **options: Any) -> None:
        from paiements.grpc_server import serve
        from paiements.schedulers import start_scheduler

        start_scheduler()
        self.stdout.write("Serveur gRPC Paiement démarré.")
        serve()
