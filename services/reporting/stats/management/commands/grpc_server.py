from django.core.management.base import BaseCommand

from stats.grpc_server import serve
from stats.schedulers import start_scheduler


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Reporting Service"

    def handle(self, *args, **options):
        start_scheduler()
        serve()
