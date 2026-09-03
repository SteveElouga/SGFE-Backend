from typing import Any

from django.core.management.base import BaseCommand

from abonnes.grpc_server import serve


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Abonné Service"

    def handle(self, *args: Any, **options: Any) -> None:
        serve()
