from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Auth Service"

    def handle(self, *args: Any, **options: Any) -> None:
        from comptes.grpc_server import serve
        from comptes.schedulers import start_scheduler

        start_scheduler()
        serve()
