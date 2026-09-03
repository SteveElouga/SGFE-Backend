from typing import Any

from django.core.management.base import BaseCommand

from parametres.grpc_server import serve


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Config Service"

    def handle(self, *args: Any, **options: Any) -> None:
        serve()
