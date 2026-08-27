# CLAUDE.md — Paiement Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Gestion des paiements, des soldes de factures et du suivi des impayés.

## Structure

```
services/paiement/
├── paiement/          # Projet Django (settings, urls, wsgi)
├── paiements/         # App métier : Paiement, SoldeFacture, SuiviImpaye
│   ├── management/commands/grpc_server.py
│   └── schedulers.py  # APScheduler cron 8h00
├── proto/             # Stubs générés depuis les .proto — NE PAS MODIFIER
```

## Modèles

- **Paiement** : un versement sur une facture (partiel ou total)
- **SoldeFacture** : PK = facture_id (une ligne par facture), statut IMPAYEE/PARTIELLE/PAYEE
- **SuiviImpaye** : suivi des étapes de relance (4 étapes)
- **AvoirAbonne** + **MouvementAvoir** : crédit d'un abonné et son journal

## Règles métier critiques

- `montant > 0` — toujours
- **Le surpaiement est accepté** : la facture est soldée avec la part imputable
  et l'excédent est porté au crédit (`AvoirAbonne`), reporté sur les prochaines
  factures. La règle « pas de surpaiement » qui figurait ici contredisait le
  code depuis l'introduction de l'avoir.
- **Imputation d'un versement au niveau abonné : du plus ancien au plus récent.**
  `enregistrer_paiement_abonne` éteint d'abord le solde le plus anciennement
  **exigible** (tri sur `date_limite_paiement`, pas sur la création), le
  reliquat débordant sur le suivant. Un versement produit donc potentiellement
  plusieurs écritures ; la référence de transaction ne se pose que sur la
  première, la contrainte d'unicité l'exigeant.
  C'est l'ancienneté qui déclenche relances et suspension : imputer dans le
  mauvais ordre laisserait vieillir la mauvaise dette.
- `enregistrer_paiement` (une facture nommée) reste disponible : un caissier a
  parfois besoin de viser une facture précise.
- `reference_transaction` obligatoire pour MOBILE_MONEY et VIREMENT
- Statut : `montant_paye == 0` → IMPAYEE ; `0 < montant_paye < total` → PARTIELLE ; `>= total` → PAYEE
- Après PAYEE : résoudre SuiviImpaye.resolu_le et notifier (dégradation gracieuse)
- Après PARTIELLE : suspendre relances N jours (défaut: 5)

## Événements émis

- `UpdateStatutFacture` → Facturation Service après chaque paiement
- `EnvoyerRelance` → Notification Service (étapes 1-4)
- `SuspendreAbonne` → Abonné Service (étape 4)

## Cron (8h00)

```
ImpayeCheckerJob :
  Pour chaque SoldeFacture (date_limite < today, statut != PAYEE) :
    Étape 1 (J+0)  : rappel WhatsApp
    Étape 2 (J+3)  : 2ème rappel
    Étape 3 (J+7)  : avertissement
    Étape 4 (J+10) : suspension + notification
  Délais configurables via Config Service.
```

## Démarrage local

```bash
cd services/paiement
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50055
python manage.py test paiements   # tests (SQLite en mémoire)
```

## Génération des stubs

```bash
python -m grpc_tools.protoc -I ../../proto/ \
  --python_out=proto/ --grpc_python_out=proto/ \
  ../../proto/paiement_service.proto \
  ../../proto/facturation_service.proto \
  ../../proto/notification_service.proto \
  ../../proto/abonne_service.proto \
  ../../proto/config_service.proto
```
