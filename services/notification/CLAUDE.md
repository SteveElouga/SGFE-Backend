# CLAUDE.md — Notification Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Envoi de messages WhatsApp aux abonnés (factures, relances, suspensions) et gestion des tokens d'accès tokenisés à l'espace abonné (EF-NOTIF-001 à EF-NOTIF-006).

## Structure

```
services/notification/
├── notification/      # Projet Django (settings, urls, wsgi)
├── notifications/     # App métier : Envoi, TokenAcces, services, grpc_server
│   ├── models.py      # Envoi, TokenAcces
│   ├── services.py    # EnvoiService, TokenService
│   ├── grpc_server.py # Servicer gRPC
│   ├── grpc_clients.py # Clients vers Facturation, Abonné, Config
│   ├── whatsapp_client.py  # HTTP vers whatsapp-web.js
│   ├── message_builder.py  # Constructeurs de messages WhatsApp
│   └── management/commands/grpc_server.py
├── proto/             # Stubs générés depuis proto/*.proto — NE PAS MODIFIER
```

## Spécificités

- **Pas d'API HTTP** — tout passe par gRPC (port 50056).
- **WhatsApp** : HTTP POST vers `whatsapp-service:3000/send` (Node.js whatsapp-web.js).
  Scanner le QR code sur `/qr` pour activer l'envoi.
- **Dégradation gracieuse** : si WhatsApp est indisponible, l'Envoi est marqué
  ECHEC en base sans lever d'erreur gRPC — la facture reste accessible.
- **TokenAcces** : UUID v4 partagé dans l'URL `{FRONTEND_URL}/espace/{token}`.
  Durée configurable via Config Service (clé `token_validite_jours`, défaut 20 jours).
- **ValiderToken** : ne lève jamais d'erreur gRPC — retourne `is_valid=False`
  si le token est expiré, révoqué ou inexistant.

## Démarrage local

```bash
cd services/notification
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50056
python manage.py test notifications  # tests (utilisent sqlite en mémoire)
```

## Génération des stubs proto

```bash
python -m grpc_tools.protoc -I ../../proto/ \
  --python_out=proto/ --grpc_python_out=proto/ \
  ../../proto/notification_service.proto \
  ../../proto/facturation_service.proto \
  ../../proto/abonne_service.proto \
  ../../proto/config_service.proto
```
