# Guide Frontend — API SGFE

> Point d'entrée unique : `POST /graphql` (GraphQL) + endpoints REST dédiés.
> Authentification : `Authorization: Bearer <access_token>` sur toutes les requêtes GraphQL protégées.

---

## 1. Campagne — Détail complet

### Type `Campagne`

```graphql
type Campagne {
  campagneId: String!
  nom: String!
  periodeMois: Int!          # 1-12
  periodeAnnee: Int!
  statut: String!            # "PLANIFIEE" | "EN_COURS" | "CLOTUREE"
  datePlanifiee: String!     # "YYYY-MM-DD" ou ""
  dateCreation: String!      # ISO datetime
  dateCloture: String!       # ISO datetime ou ""
  numeroMobileMoney: String! # ex "658552294", vide si non renseigné
  genererFacturesAuto: Boolean! # true = génère les factures à la clôture
  envoyerWhatsappAuto: Boolean! # true = envoie le WhatsApp après génération
}
```

### Queries

```graphql
# Détail d'une campagne
query {
  campagne(campagneId: "uuid") {
    campagneId nom statut periodeMois periodeAnnee
    numeroMobileMoney genererFacturesAuto envoyerWhatsappAuto
    datePlanifiee dateCreation dateCloture
  }
}

# Liste des campagnes
# ADMIN → toutes | SUPERVISEUR → les siennes (filtré par created_by) | AGENT → les siennes
query {
  campagnes(createdBy: "", agentId: "") {
    campagneId nom statut periodeMois periodeAnnee
    genererFacturesAuto envoyerWhatsappAuto
  }
}

# Progression d'une campagne (comptage des relevés)
query {
  progression(campagneId: "uuid") {
    campagneId totalAbonnes nbReleves nbEnAttente pourcentage
  }
}
```

### Mutations

```graphql
# Créer une campagne — ADMIN, SUPERVISEUR
mutation {
  creerCampagne(input: {
    nom: "Campagne Août 2026"
    periodeMois: 8
    periodeAnnee: 2026
    datePlanifiee: "2026-08-01"       # optionnel — démarre automatiquement ce jour à 07:00
    numeroMobileMoney: "658552294"    # optionnel — 9 chiffres camerounais exactement
    genererFacturesAuto: true         # défaut true
    envoyerWhatsappAuto: true         # défaut true
  }) {
    campagneId statut genererFacturesAuto envoyerWhatsappAuto
  }
}

# Affecter un agent à une campagne — ADMIN (toutes), SUPERVISEUR (les siennes)
mutation {
  affecterAgent(campagneId: "uuid", agentId: "uuid") {
    campagneId
  }
}

# Clôturer une campagne EN_COURS — ADMIN (toutes), SUPERVISEUR (les siennes)
# → Si genererFacturesAuto=true : déclenche la génération des factures automatiquement
# → Si genererFacturesAuto=false : rien — le COMPTABLE génère manuellement plus tard
mutation {
  cloturerCampagne(campagneId: "uuid") {
    campagneId statut dateCloture
  }
}

# Saisir l'index d'un abonné — ADMIN, AGENT, SUPERVISEUR (les siennes)
mutation {
  saisirIndex(input: {
    campagneId: "uuid"
    abonneId: "uuid"
    nouveauIndex: 145.5
    observation: ""
  }) {
    releveId statut consommation
  }
}
```

### Comportement `genererFacturesAuto` + `envoyerWhatsappAuto`

| `genererFacturesAuto` | `envoyerWhatsappAuto` | À la clôture | Action manuelle requise |
|---|---|---|---|
| `true` | `true` | Factures générées + WhatsApp envoyé | Aucune |
| `true` | `false` | Factures générées, pas de WhatsApp | Envoyer WhatsApp manuellement |
| `false` | peu importe | Rien | Générer les factures manuellement |

---

## 2. Facturation — Détail complet

### Type `Facture`

```graphql
type Facture {
  factureId: String!
  numeroFacture: String!       # ex "FACT-2026-08-0001"
  abonneId: String!
  campagneId: String!
  ancienIndex: Float!
  nouveauIndex: Float!
  consommation: Float!         # m³ consommés
  prixM3: Float!               # prix copié au moment de la génération (immuable)
  montant: Float!              # FCFA
  statut: String!              # "IMPAYEE" | "PARTIELLE" | "PAYEE"
  dateReleve: String!          # "YYYY-MM-DD"
  dateLimitePaiement: String!  # "YYYY-MM-DD"
  dateGeneration: String!      # ISO datetime
  pdfPath: String!             # chemin interne — ne pas afficher; utiliser l'URL PDF
  numeroMobileMoney: String!   # copié depuis la campagne, vide si non renseigné
}
```

### Queries

```graphql
# Détail d'une facture — ADMIN, COMPTABLE
query {
  facture(factureId: "uuid") {
    factureId numeroFacture abonneId campagneId
    consommation montant statut dateReleve dateLimitePaiement
    numeroMobileMoney
  }
}

# Liste des factures (filtres optionnels) — ADMIN, COMPTABLE
query {
  factures(campagneId: "", abonneId: "", statut: "") {
    factureId numeroFacture abonneId statut montant
    dateLimitePaiement numeroMobileMoney
  }
}

# Toutes les factures d'une campagne — ADMIN, COMPTABLE
query {
  facturesParCampagne(campagneId: "uuid") {
    factureId numeroFacture abonneId statut montant
  }
}

# Tarif actuel (prix du m³) — ADMIN, COMPTABLE
query {
  tarifActuel { tarifId prixM3 dateEffet isActive }
}
```

### Mutations

```graphql
# Générer les factures manuellement — ADMIN, COMPTABLE
# À utiliser quand genererFacturesAuto=false sur la campagne
# envoyerWhatsappAuto=true envoie le WhatsApp immédiatement après chaque facture
mutation {
  genererFactures(campagneId: "uuid", envoyerWhatsappAuto: true) {
    factureId numeroFacture abonneId montant statut numeroMobileMoney
  }
}

# Envoyer le WhatsApp pour TOUTES les factures d'une campagne d'un coup — ADMIN, COMPTABLE
# Retourne le nombre de messages envoyés avec succès
mutation {
  envoyerToutesFacturesWhatsapp(campagneId: "uuid")
}

# Modifier le tarif (prix du m³) — ADMIN uniquement
mutation {
  updateTarif(prixM3: 600.0, dateEffet: "2026-08-01") {
    tarifId prixM3 dateEffet isActive
  }
}
```

### Obtenir le PDF d'une facture

```
GET /espace-abonne/{token}/facture/{factureId}/pdf/
```
Retourne le PDF en `application/pdf` (voir section 4 — Espace Abonné).

---

## 3. WhatsApp — Notifications manuelles

### Type `Envoi`

```graphql
type Envoi {
  envoiId: String!
  factureId: String!
  statut: String!          # "EN_ATTENTE" | "ENVOYE" | "ECHEC"
  dateEnvoi: String!
  telnyxMessageId: String!
  erreur: String!          # vide si succès
}
```

### Mutations

```graphql
# Envoyer le WhatsApp d'une facture (1ère fois) — ADMIN, COMPTABLE
mutation {
  envoyerFactureWhatsapp(factureId: "uuid", abonneId: "uuid") {
    envoiId statut dateEnvoi erreur
  }
}

# Renvoyer le WhatsApp d'une facture (révoque l'ancien token, crée un nouveau) — ADMIN, COMPTABLE
# À utiliser quand l'abonné dit que son lien est expiré
mutation {
  renvoyerFactureWhatsapp(factureId: "uuid") {
    envoiId statut dateEnvoi erreur
  }
}

# Queries — historique des envois — ADMIN, COMPTABLE
query {
  envois(factureId: "uuid", abonneId: "") {
    envoiId statut dateEnvoi typeEnvoi erreur
  }
}
```

---

## 4. Espace Abonné — Accès public via token WhatsApp

Ces endpoints sont **sans authentification JWT** — ils utilisent le token partagé dans le lien WhatsApp.

### `GET /espace-abonne/{token}/`

Retourne toutes les factures de l'abonné avec leur solde de paiement.

**Réponse JSON :**
```json
{
  "abonne_id": "uuid",
  "token_expiration": "YYYY-MM-DD",
  "factures": [
    {
      "facture_id": "uuid",
      "numero": "FACT-2026-08-0001",
      "date_releve": "YYYY-MM-DD",
      "montant": 7200.0,
      "statut": "IMPAYEE",
      "date_limite_paiement": "YYYY-MM-DD",
      "solde_restant": 7200.0,
      "montant_paye": 0.0
    }
  ]
}
```

**Codes d'erreur :**
- `401` → token invalide ou expiré → afficher page "Lien expiré, contactez-nous"
- `503` → service indisponible → afficher message d'erreur temporaire

### `GET /espace-abonne/{token}/facture/{factureId}/pdf/`

Retourne le PDF en `application/pdf` avec `Content-Disposition: inline`.

**Utilisation recommandée :**
```html
<!-- Dans l'espace abonné -->
<a href="/espace-abonne/{token}/facture/{factureId}/pdf/" target="_blank">
  Télécharger ma facture
</a>
```

---

## 5. Paiement

### Types

```graphql
type SoldeFacture {
  factureId: String!
  montantTotal: Float!
  montantPaye: Float!
  soldeRestant: Float!
  statut: String!     # "IMPAYEE" | "PARTIELLE" | "PAYEE"
}

type Paiement {
  paiementId: String!
  factureId: String!
  montant: Float!
  datePaiement: String!      # "YYYY-MM-DD"
  modePaiement: String!      # "ESPECES" | "CHEQUE" | "MOBILE_MONEY" | "VIREMENT"
  referenceTransaction: String!  # vide pour ESPECES et CHEQUE
  createdAt: String!         # ISO datetime
}

type SuiviImpaye {
  suiviId: String!
  factureId: String!
  abonneId: String!
  dateDepassement: String!   # date à laquelle la limite a été dépassée
  etapeActuelle: Int!        # 1 = 1ère relance, 2 = 2ème, 3 = suspension imminente, 4 = suspendu
  resoluLe: String!          # date de résolution ou "" si toujours impayé
}
```

### Queries

```graphql
# Solde d'une facture — ADMIN, COMPTABLE
query {
  soldeFacture(factureId: "uuid") {
    factureId montantTotal montantPaye soldeRestant statut
  }
}

# Liste des paiements — filtres optionnels — ADMIN, COMPTABLE
query {
  paiements(factureId: "uuid", abonneId: "") {
    paiementId factureId montant datePaiement modePaiement referenceTransaction createdAt
  }
}

# Factures impayées (date limite dépassée) — ADMIN, COMPTABLE
# Retourne une liste de SoldeFacture
query {
  impayes {
    factureId montantTotal montantPaye soldeRestant statut
  }
}

# Détail du suivi de relance d'un impayé — ADMIN, COMPTABLE
query {
  suiviImpaye(factureId: "uuid") {
    suiviId factureId abonneId dateDepassement etapeActuelle resoluLe
  }
}
```

### Mutations

```graphql
# Enregistrer un versement — ADMIN, COMPTABLE
# modePaiement : "ESPECES" | "CHEQUE" | "MOBILE_MONEY" | "VIREMENT"
# referenceTransaction : obligatoire pour MOBILE_MONEY et VIREMENT
mutation {
  enregistrerPaiement(
    factureId: "uuid"
    abonneId: "uuid"
    montant: 5000.0
    datePaiement: "2026-08-15"
    modePaiement: "MOBILE_MONEY"
    referenceTransaction: "TXN-ABC123"
  ) {
    paiementId factureId montant datePaiement modePaiement referenceTransaction createdAt
  }
}
```

### Comportements automatiques (cron — aucune action frontend)

Le Paiement Service tourne un job à **08:00 chaque matin** qui :
1. Détecte les factures dont `dateLimitePaiement` est dépassée
2. Crée ou met à jour un `SuiviImpaye` et passe à l'étape suivante
3. À chaque étape, envoie un message WhatsApp de relance à l'abonné
4. À l'étape 4, suspend l'abonné (appel vers Abonné Service)

| `etapeActuelle` | Signification |
|---|---|
| `1` | 1ère relance envoyée |
| `2` | 2ème relance envoyée |
| `3` | Dernier avertissement avant suspension |
| `4` | Abonné suspendu — accès coupé |
```

---

## 6. Flux complets — Scénarios d'utilisation

### Scénario A : Génération automatique (flux standard)
```
1. SUPERVISEUR crée campagne (genererFacturesAuto=true, envoyerWhatsappAuto=true)
2. AGENT saisit les index
3. SUPERVISEUR clôture la campagne → factures générées + WhatsApp envoyé automatiquement
4. COMPTABLE consulte les factures et enregistre les paiements
```

### Scénario B : Génération différée (contrôle total)
```
1. SUPERVISEUR crée campagne (genererFacturesAuto=false)
2. AGENT saisit les index
3. SUPERVISEUR clôture la campagne → rien de généré
4. COMPTABLE va sur la page des factures :
   ┌─────────────────────────────────────────────────────────────┐
   │ [Générer toutes les factures]   → mutation genererFactures(
   │   campagneId, envoyerWhatsappAuto: false)                   │
   └─────────────────────────────────────────────────────────────┘
5. Les factures apparaissent dans le tableau
6. COMPTABLE choisit :
   ┌──────────────────────────────────────────────────────────────────┐
   │ [📤 Envoyer tout par WhatsApp]                                   │
   │   → mutation envoyerToutesFacturesWhatsapp(campagneId)           │
   │                                                                  │
   │ Ou par ligne dans le tableau :                                   │
   │ [ FACT-2026-08-0001 | Jean DUPONT | 7200 FCFA | IMPAYEE | 📤 ]  │
   │   → mutation renvoyerFactureWhatsapp(factureId)                  │
   └──────────────────────────────────────────────────────────────────┘
```

### Scénario C : Renvoi WhatsApp (lien expiré)
```
1. L'abonné signale que son lien a expiré (token_validite_jours = 20 jours par défaut)
2. COMPTABLE dans le détail de la facture :
   [🔄 Renvoyer le lien WhatsApp]
   → mutation renvoyerFactureWhatsapp(factureId)
   → L'ancien token est révoqué, un nouveau est créé, nouveau message envoyé
```

---

## 7. Pages à construire — Ce qui manque côté frontend

### Page : Création de campagne
Ajouter les champs :
- `numeroMobileMoney` : input text, placeholder "658552294", validation 9 chiffres, optionnel
- `genererFacturesAuto` : toggle (libellé : "Générer les factures automatiquement à la clôture"), défaut ON
- `envoyerWhatsappAuto` : toggle (libellé : "Envoyer les factures par WhatsApp"), défaut ON
  - Ce toggle doit être **grisé et forcé à false** si `genererFacturesAuto = false`

### Page : Détail de la campagne
Afficher :
- `numeroMobileMoney` (si renseigné)
- `genererFacturesAuto` + `envoyerWhatsappAuto` (badges lecture seule)
- Bouton **[Clôturer la campagne]** — `cloturerCampagne`

### Page : Factures d'une campagne
Afficher la liste via `facturesParCampagne(campagneId)`.

Si `genererFacturesAuto = false` et aucune facture → afficher une **bannière** :
> ⚠️ Les factures de cette campagne n'ont pas encore été générées.
> [Générer toutes les factures] → `genererFactures(campagneId, envoyerWhatsappAuto: true/false)`

En tête de liste (si factures existent) :
- Bouton **[📤 Envoyer tout par WhatsApp]** → `envoyerToutesFacturesWhatsapp(campagneId)`

Par ligne dans le tableau : icône ou bouton **📤** → `renvoyerFactureWhatsapp(factureId)`

### Page : Détail d'une facture
Afficher `numeroMobileMoney` (si renseigné) avec un libellé "Paiement Mobile Money".
Bouton **[📤 Renvoyer par WhatsApp]** → `renvoyerFactureWhatsapp(factureId)`.

### Page : Espace Abonné (PWA publique, sans login)
Route : `/espace/{token}`
- Appel : `GET /espace-abonne/{token}/`
- Afficher la liste des factures avec statut et solde restant
- Lien PDF par facture : `GET /espace-abonne/{token}/facture/{factureId}/pdf/`
- Si `401` : afficher page "Votre lien a expiré, contactez-nous."

---

## 8. Migrations à appliquer

```bash
cd services/campagne  && python manage.py migrate  # 0003 + 0004
cd services/facturation && python manage.py migrate # 0002
```

## 9. Variables d'environnement ajoutées

```env
# Facturation Service
NOTIFICATION_GRPC_HOST=notification-service
NOTIFICATION_GRPC_PORT=50056
```
