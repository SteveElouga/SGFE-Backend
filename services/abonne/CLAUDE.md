# CLAUDE.md — Abonné Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Gestion des abonnés et de leurs compteurs (EF-ABO-001 à EF-ABO-006). Dépendance directe de `campagne-service` (qui consomme `ListAbonnesActifs`) et `facturation-service`.

## Structure

```
services/abonne/
├── abonne/          # Projet Django (settings, urls, wsgi)
├── abonnes/         # App métier : Abonne, Compteur, HistoriqueCompteur
│   ├── services.py     # AbonneService, CompteurService, NumerotationService
│   └── management/commands/grpc_server.py   # `python manage.py grpc_server`
├── proto/           # Stubs générés depuis proto/abonne_service.proto — NE PAS MODIFIER
```

## Spécificités

- **Numérotation** : `numero_abonne` auto-généré au format `AB-XXXX` (séquentiel, 4 chiffres) par `NumerotationService.generer()` — basé sur le dernier numéro existant, pas de compteur séparé en base.
- **Création d'abonné** : un compteur est **obligatoire** à la création (`CreateAbonne` crée l'abonné ET son compteur en une seule opération côté `AbonneService.create_abonne`).
- **Un seul compteur actif à la fois** : `CompteurService.get_compteur_actif` lève `ObjectDoesNotExist` s'il n'y en a aucun (ne devrait jamais arriver en usage normal).
- **Remplacement de compteur** (`RemplacerCompteur`) : archive l'ancien (`statut=REMPLACE`), crée le nouveau (`statut=ACTIF`), trace l'opération dans `HistoriqueCompteur`. Valide que `index_fermeture >= index_initial` de l'ancien compteur (sinon `ValidationError` → gRPC `INVALID_ARGUMENT`).
- Un abonné suspendu (`SuspendreAbonne`) n'apparaît plus dans `ListAbonnesActifs` (utilisé par `campagne-service` pour ne pas l'ajouter aux nouvelles campagnes).
- Ce service n'appelle aucun autre service gRPC (pas de `grpc_clients.py`). Aucun contrôle de rôle ici : c'est la responsabilité de la Gateway (ce service ne vérifie que l'appelant est bien la Gateway, pas le rôle de l'utilisateur final).

## Démarrage local

```bash
cd services/abonne
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50052
python manage.py test abonnes     # tests (utilisent sqlite en mémoire)
```
