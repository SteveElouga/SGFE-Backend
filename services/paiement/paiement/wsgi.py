"""Configuration WSGI du Paiement Service."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paiement.settings")

application = get_wsgi_application()
