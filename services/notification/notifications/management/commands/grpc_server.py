"""Commande Django pour démarrer le serveur gRPC du Notification Service.

Usage : python manage.py grpc_server
"""

from django.core.management.base import BaseCommand

from notifications.grpc_server import serve


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Notification Service"

    def handle(self, *args, **options):
        serve()
