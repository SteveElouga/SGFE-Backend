from django.core.management.base import BaseCommand

from stats.grpc_server import serve


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Reporting Service"

    def handle(self, *args, **options):
        serve()
