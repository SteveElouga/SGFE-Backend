# CLAUDE.md — Notification Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Envoi de messages WhatsApp aux abonnés (factures, relances, suspensions) et gestion des tokens d'accès tokenisés à l'espace abonné (EF-NOTIF-001 à EF-NOTIF-006).

## Structure

```
services/notification/
├── notification/      # Projet Django (settings, urls, wsgi)
├── notifications/     # App métier : Envoi, TokenAcces, Diffusion, services, grpc_server
│   ├── models.py      # Envoi, TokenAcces, Diffusion, DiffusionEnvoi
│   ├── services.py    # EnvoiService, TokenService, DiffusionService
│   ├── grpc_server.py # Servicer gRPC
│   ├── grpc_clients.py # Clients vers Facturation, Abonné, Config, Paiement
│   ├── whatsapp_client.py  # HTTP vers whatsapp-web.js
│   ├── message_builder.py  # Constructeurs de messages WhatsApp
│   ├── event_publisher.py  # Publication Redis (canal "diffusion:events")
│   ├── schedulers.py       # APScheduler : diffusion_processor (15s), retry WhatsApp (15 min)
│   └── management/commands/grpc_server.py
├── proto/             # Stubs générés depuis proto/*.proto — NE PAS MODIFIER
```

## Spécificités

- **Pas d'API HTTP** — tout passe par gRPC (port 50056).
- **WhatsApp** : HTTP POST vers `whatsapp-service:3000/send` (Node.js whatsapp-web.js).
  L'appairage se fait depuis **Configuration › WhatsApp & Tokens** dans le
  frontend — il n'y a pas de route `/qr` exposée aux utilisateurs.
- **Client Paiement** : `GetDetteAbonne` et `GetSolde`, pour que le message
  WhatsApp annonce le **même total que le PDF** qu'il transporte — consommation
  du mois, plus la dette antérieure, moins l'avoir imputé. Les deux appels
  dégradent : si Paiement est injoignable, le message part avec la
  consommation seule plutôt que de ne pas partir.
- **Dégradation gracieuse** : si WhatsApp est indisponible, l'Envoi est marqué
  ECHEC en base sans lever d'erreur gRPC — la facture reste accessible.
- **TokenAcces** : UUID v4 partagé dans l'URL `{FRONTEND_URL}/espace/{token}`.
  Durée configurable via Config Service (clé `token_validite_jours`, défaut 20 jours).
- **ValiderToken** : ne lève jamais d'erreur gRPC — retourne `is_valid=False`
  si le token est expiré, révoqué ou inexistant.
- **Diffusion** : message libre (`CreerDiffusion`) envoyé à un ensemble
  d'abonnés déjà résolu côté gateway (le filtrage quartier/camp/statut se fait
  côté frontend, jamais ici). `DiffusionService.creer_diffusion` résout le
  téléphone de chaque abonné via Abonné Service, avec dégradation **par
  abonné** : un abonné introuvable ou injoignable ne bloque pas les autres, sa
  ligne est simplement omise. `nb_total`/`nb_envoyes`/`nb_echecs` ne sont
  jamais stockés — recalculés par agrégation sur `DiffusionEnvoi` à chaque
  lecture (`GetDiffusion`/`ListDiffusions`), pour ne jamais afficher un
  compteur qui a dérivé de l'état réel.

## Jobs de fond (APScheduler, `schedulers.py`)

Contrairement aux crons Paiement/Campagne (une passe quotidienne à heure
fixe), ces deux jobs tournent en continu par petits lots (`IntervalTrigger`,
pas `CronTrigger`) — verrou consultatif PostgreSQL par job (anti double-envoi
en réplication), même patron que les autres services.

- **`diffusion_processor_job`** (15s, `id="diffusion_processor"`) : envoie un
  lot de 5 `DiffusionEnvoi` `EN_ATTENTE` (throttle délibéré — pas une limite
  technique de whatsapp-service, mais pour ne pas ressembler à du spam sur le
  compte WhatsApp Web partagé par tout le système), puis referme (`TERMINEE`)
  toute `Diffusion` dont il ne reste plus de ligne `EN_ATTENTE` et publie sur
  Redis (`diffusion:events`) — écouté par la subscription GraphQL
  `diffusionProgressionUpdated` côté gateway.
- **`retry_envois_echec_job`** (15 min) : retente les `Envoi` en `ECHEC` sous
  le plafond `MAX_TENTATIVES_AUTO`, en rejouant `Envoi.dernier_message` à
  l'identique (jamais recalculé).

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
  ../../proto/config_service.proto \
  ../../proto/paiement_service.proto
```
