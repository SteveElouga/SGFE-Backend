"""Commande Django pour démarrer le serveur gRPC du Campagne Service."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Campagne Service"

    def handle(self, *args, **options) -> None:
        from campagnes.grpc_server import serve
        from campagnes.schedulers import start_scheduler

        start_scheduler()
        self.stdout.write("Serveur gRPC Campagne démarré.")
        serve()
