# CLAUDE.md — Facturation Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Génération des factures à la clôture d'une campagne, calcul du montant (consommation × prix_m3),
génération des PDF (gabarit Django + WeasyPrint), gestion du tarif actif, et mise à jour du statut
facture (appelée par Paiement Service).

## Structure

```
services/facturation/
├── facturation/     # Projet Django (settings, urls, wsgi)
├── factures/        # App métier : Tarif, Facture
│   ├── management/commands/grpc_server.py
│   ├── pdf_generator.py    # Construit le contexte de rendu, WeasyPrint (import paresseux)
│   ├── templates/facture_pdf.html  # Gabarit HTML/CSS "AquaBill" (rendu via render_to_string)
│   └── grpc_clients.py     # → Abonné, Campagne (ListReleves, GetCampagne), Config Service
├── proto/           # Stubs générés depuis proto/facturation_service.proto — NE PAS MODIFIER
```

## Spécificités

- **Tarif** : un seul tarif actif à la fois. `UpdateTarif` désactive l'ancien avant d'en créer un nouveau.
  Le `prix_m3` est **copié** dans chaque facture — jamais de FK vers le tarif.
- **Numérotation** : `FACT-AAAA-MM-XXXX` (ex. FACT-2025-07-0001). Séquence réinitialisée chaque mois,
  verrouillée par `select_for_update()` pour éviter les collisions en génération concurrente.
- **PDF** : rendu du gabarit `templates/facture_pdf.html` (Django, autoescape) via WeasyPrint,
  généré à la création de la facture, stocké dans `PDF_STORAGE_DIR`. Régénéré à la volée si absent
  (`get_pdf_bytes`). Le contexte inclut l'historique de consommation des 6 derniers mois de
  l'abonné (`FactureRepository.list_historique_consommation`). Le bloc "espace abonné" du PDF est
  masqué tant qu'aucun token d'accès n'existe encore (créé par Notification Service, pas par ce
  service) — l'agent ayant effectué le relevé et l'heure exacte ne sont pas encore tracés par
  Campagne Service, ces deux champs du gabarit restent donc vides pour l'instant.
- **`GenererFactures`** : appelle `CampagneService.ListReleves`, filtre les relevés `RELEVE` (index saisi),
  génère une facture par relevé. Les relevés `NON_RELEVE` et `ESTIME` sont ignorés. Revalide
  `nouveau_index >= ancien_index` avant tout calcul (défense en profondeur, ne fait pas une
  confiance aveugle aux données déjà validées par Campagne Service).
- **`date_limite_paiement`** : `date_releve + delai_paiement_jours` (lu depuis Config Service, défaut 5).
- **`UpdateStatutFacture`** : appelé par Paiement Service pour passer une facture en PARTIELLE ou PAYEE.

## Démarrage local

```bash
cd services/facturation
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50054
python manage.py test factures    # tests (utilisent sqlite en mémoire)
```

## Générer les stubs proto

```bash
python -m grpc_tools.protoc \
  -I ../../proto/ \
  --python_out=proto/ \
  --grpc_python_out=proto/ \
  ../../proto/facturation_service.proto
```
