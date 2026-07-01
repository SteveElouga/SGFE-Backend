# Résumé des changements backend — Frontend Update Guide

## PR #12 — Gaps EF-IMP-005, EF-NOTIF-003, EF-NOTIF-005

### Nouveaux endpoints REST (Gateway)

#### Espace abonné (accès via token WhatsApp)

```
GET /espace-abonne/{token}/
```
**Réponse JSON :**
```json
{
  "abonne_id": "uuid",
  "token_expiration": "JJ/MM/AAAA",
  "factures": [
    {
      "facture_id": "uuid",
      "numero": "FACT-2025-07-0001",
      "date_releve": "YYYY-MM-DD",
      "montant": 15000.0,
      "statut": "IMPAYEE | PARTIELLE | PAYEE",
      "date_limite_paiement": "YYYY-MM-DD",
      "solde_restant": 15000.0,
      "montant_paye": 0.0
    }
  ]
}
```

```
GET /espace-abonne/{token}/facture/{facture_id}/pdf/
```
**Réponse :** PDF binaire (`application/pdf`), headers `Content-Disposition: attachment`.

---

## PR #13 — Numéro Mobile Money + Toggles campagne + Notifications admin

### 1. Mutation GraphQL `creerCampagne` — nouveaux champs

```graphql
mutation {
  creerCampagne(input: {
    nom: "Campagne Août 2026"
    periodeMois: 8
    periodeAnnee: 2026
    datePlanifiee: ""            # optionnel
    numeroMobileMoney: "658552294"  # 9 chiffres camerounais, optionnel
    genererFacturesAuto: true    # défaut true — génère les factures à la clôture
    envoyerWhatsappAuto: true    # défaut true — envoie les factures par WhatsApp
  }) {
    campagneId
    nom
    statut
    numeroMobileMoney
    genererFacturesAuto
    envoyerWhatsappAuto
  }
}
```

**Validation `numeroMobileMoney` :**
- Exactement **9 chiffres** (format camerounais, ex: `658552294`)
- Champ vide = pas de numéro Mobile Money (aucun affichage dans le message WhatsApp)
- En cas d'erreur : code GraphQL `INVALID_ARGUMENT`, message : *"Le numéro Mobile Money doit contenir exactement 9 chiffres (ex: 658552294)."*

### 2. Type GraphQL `Campagne` — nouveaux champs retournés

```graphql
type Campagne {
  campagneId: String!
  nom: String!
  periodeMois: Int!
  periodeAnnee: Int!
  statut: String!
  datePlanifiee: String!
  dateCreation: String!
  dateCloture: String!
  numeroMobileMoney: String!      # NEW — vide si non renseigné
  genererFacturesAuto: Boolean!   # NEW — défaut true
  envoyerWhatsappAuto: Boolean!   # NEW — défaut true
}
```

### 3. Comportements à la clôture (`cloturerCampagne`)

| `genererFacturesAuto` | `envoyerWhatsappAuto` | Comportement |
|---|---|---|
| `true` | `true` | Factures générées + WhatsApp envoyé à chaque abonné |
| `true` | `false` | Factures générées, **aucun** WhatsApp |
| `false` | `true` | Rien (pas de factures, pas de WhatsApp) |
| `false` | `false` | Rien |

### 4. Message WhatsApp de facture

Quand `numeroMobileMoney` est renseigné sur la campagne, le message inclut automatiquement :

```
Bonjour Jean DUPONT,

Votre facture d'eau - Août 2026

Consommation : 12 m³
Montant dû    : 7200 FCFA
Date limite   : 20/08/2026

💳 Paiement Mobile Money : 658552294

📄 Votre facture est en pièce jointe.

🔗 Consultez votre historique :
https://app.sgfe.cm/espace/abc123...

(Lien valable jusqu'au 09/09/2026)
```

### 5. Notifications admin (Config Service)

Deux clés configurables via le backoffice admin :

| Clé | Valeur par défaut | Description |
|---|---|---|
| `EMAIL_ADMIN_NOTIFICATIONS` | `""` (vide = désactivé) | Email destinataire des alertes (Brevo) |
| `NOTIFICATIONS_ADMIN_ACTIVEES` | `"true"` | Bascule globale — `"false"` coupe tous les emails |

**Événements déclencheurs d'email admin :**
- Campagne démarrée automatiquement (cron 7h00)
- Abonné suspendu pour impayé
- Échec envoi WhatsApp

---

## Variables d'environnement ajoutées

### Facturation Service (`.env`)
```env
NOTIFICATION_GRPC_HOST=notification-service
NOTIFICATION_GRPC_PORT=50056
```

### Notification Service (déjà présent, vérifier)
```env
BREVO_API_KEY=your_brevo_api_key
EMAIL_ADMIN_NOTIFICATIONS=admin@example.com  # dans Config Service DB, pas .env
```

---

## Migrations à appliquer

```bash
# Campagne Service
cd services/campagne && python manage.py migrate

# Facturation Service
cd services/facturation && python manage.py migrate
```

**Détail :**
- `campagnes 0003` : ajout `numero_mobile_money` (CharField)
- `campagnes 0004` : ajout `generer_factures_auto` (bool) + `envoyer_whatsapp_auto` (bool)
- `factures 0002` : ajout `numero_mobile_money` (CharField)
