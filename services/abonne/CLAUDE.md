# CLAUDE.md — Abonné Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Gestion des abonnés et de leurs compteurs (EF-ABO-001 à EF-ABO-006). Dépendance directe de `campagne-service` (GetAbonne, vérifie le statut ACTIF avant relevé), `facturation-service` (GetAbonne) et `paiement-service` (SuspendreAbonne, cron impayés). `ListAbonnesActifs` est consommé par la Gateway, pas par campagne-service.

## Structure

```
services/abonne/
├── abonne/          # Projet Django (settings, urls, wsgi)
├── abonnes/         # App métier : Abonne, Compteur, HistoriqueCompteur
│   ├── services.py     # AbonneService, CompteurService, NumerotationService
│   └── management/commands/grpc_server.py   # `python manage.py grpc_server`
├── proto/           # Stubs abonne_service.proto (servi) + campagne/facturation/paiement/notification_service.proto (consommés par grpc_clients.py pour l'export RGPD) — NE PAS MODIFIER
```

## Spécificités

- **Numérotation** : `numero_abonne` auto-généré au format `AB-XXXX` (séquentiel, 4 chiffres) par `NumerotationService.generer()` — basé sur le dernier numéro existant, pas de compteur séparé en base.
- **Création d'abonné** : un compteur est **obligatoire** à la création (`CreateAbonne` crée l'abonné ET son compteur en une seule opération côté `AbonneService.create_abonne`).
- **Un seul compteur actif à la fois** : `CompteurService.get_compteur_actif` lève `ObjectDoesNotExist` s'il n'y en a aucun (ne devrait jamais arriver en usage normal).
- **Remplacement de compteur** (`RemplacerCompteur`) : archive l'ancien (`statut=REMPLACE`), crée le nouveau (`statut=ACTIF`), trace l'opération dans `HistoriqueCompteur`. Valide que `index_fermeture >= index_initial` de l'ancien compteur (sinon `ValidationError` → gRPC `INVALID_ARGUMENT`).
- Un abonné suspendu (`SuspendreAbonne`) n'apparaît plus dans `ListAbonnesActifs` (utilisé par `campagne-service` pour ne pas l'ajouter aux nouvelles campagnes).
- Ce service appelle désormais les 4 autres services via `abonnes/grpc_clients.py` (ajouté pour l'export RGPD `ExporterDonneesAbonne`, voir `abonnes/export.py`) : Campagne, Facturation, Paiement, Notification — uniquement pour agréger les données d'un abonné (chemin froid, pas de dégradation gracieuse interne, les `grpc.RpcError` remontent tels quels). Aucun contrôle de rôle ici : c'est la responsabilité de la Gateway. L'authentification gRPC entrante (clé interne partagée `INTERNAL_GRPC_KEY`, voir `abonnes/grpc_auth.py`) vérifie seulement qu'il s'agit d'un composant interne autorisé — Gateway, mais aussi `campagne-service` (GetAbonne) et `paiement-service` (SuspendreAbonne), qui appellent ce service directement — jamais le rôle de l'utilisateur final.

## Démarrage local

```bash
cd services/abonne
.venv/bin/python manage.py migrate
.venv/bin/python manage.py grpc_server      # démarre le serveur gRPC sur le port 50052
.venv/bin/python manage.py test abonnes     # tests (utilisent sqlite en mémoire)
```

> `source .venv/bin/activate` ne fonctionne pas ici : le script fixe en dur l'ancien
> chemin du dépôt avant son déplacement. Appeler le binaire du venv directement
> (ci-dessus), ou recréer le venv à l'emplacement actuel.
