"""Commande Django pour démarrer le serveur gRPC du Notification Service.

Usage : python manage.py grpc_server
"""

from typing import Any

from django.core.management.base import BaseCommand

from notifications.grpc_server import serve
from notifications.schedulers import start_scheduler


class Command(BaseCommand):
    help = "Démarre le serveur gRPC du Notification Service"

    def handle(self, *args: Any, **options: Any) -> None:
        start_scheduler()
        serve()
