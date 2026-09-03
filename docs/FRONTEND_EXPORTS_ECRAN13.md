# Frontend — Rapports & exports (écran 13)

Documentation d'intégration des **4 exports back-office par campagne**. Livrés
en PR #69 (bilan) et #79 (les 3 autres). Toutes ces routes sont des **flux
binaires HTTP** exposés par la Gateway **en dehors de GraphQL**.

---

## 1. Règle d'or : le JWT passe par l'en-tête `Authorization`

Ces routes sont protégées **exactement comme GraphQL** : elles lisent le token
d'accès dans l'en-tête `Authorization: Bearer <access_token>` et le valident
auprès d'`auth-service` (rôle **ADMIN** ou **COMPTABLE** requis).

> ⚠️ **Un `<a href download>`, un `window.open()` ou un `<iframe>` ne
> marcheront PAS** : le navigateur n'y ajoute pas l'en-tête `Authorization`, la
> réponse sera **401**.
>
> ✅ Il faut **télécharger le fichier via `HttpClient` en `responseType: 'blob'`**
> (l'intercepteur d'auth ajoute le header), puis déclencher l'enregistrement
> côté client avec un `Object URL`. Voir §6.

Le token ne doit **jamais** être mis en query-string (il finirait dans les logs).

---

## 2. Les 4 endpoints

Base URL = **chemin relatif** (`/rapports/...`), même origine que `/graphql`
(proxy en dev, nginx en prod — voir §5). Ne jamais coder en dur `https://localhost:8443`.

| # | Export | Méthode + route | Format | Query |
|---|---|---|---|---|
| 1 | Factures d'une campagne | `GET /rapports/factures.csv` | CSV | `campagne_id` (requis) |
| 2 | Paiements d'une campagne | `GET /rapports/paiements.csv` | CSV | `campagne_id` (requis) |
| 3 | Synthèse chiffrée d'une campagne | `GET /rapports/synthese/pdf/` | PDF | `campagne_id` (requis) |
| 4 | Bilan des impayés (global) | `GET /bilan-impayes/pdf/` | PDF | *(aucun)* |

- **1 & 2 (CSV)** : `Content-Disposition: attachment` → destinés au téléchargement.
- **3 & 4 (PDF)** : servis *inline* (`as_attachment=false`) → peuvent être
  ouverts dans un nouvel onglet **ou** téléchargés (au choix du front).
- Le **nom de fichier** est fourni par l'en-tête `Content-Disposition`
  (ex. `factures-<campagne_id>.csv`, `synthese-<campagne_id>-<date>.pdf`).

---

## 3. Contenu des fichiers

### CSV factures (`/rapports/factures.csv`)
Séparateur **`;`**, encodage **UTF-8 avec BOM** (ouverture correcte des accents
dans Excel FR). Colonnes, dans l'ordre :

```
numero_facture ; abonne_id ; ancien_index ; nouveau_index ; consommation ;
prix_m3 ; montant ; statut ; date_releve ; date_limite_paiement
```

### CSV paiements (`/rapports/paiements.csv`)
Mêmes conventions. Colonnes :

```
paiement_id ; facture_id ; abonne_id ; montant ; date_paiement ;
mode_paiement ; reference_transaction ; enregistre_par
```

> `enregistre_par` est l'**ID** de l'utilisateur (pas encore résolu en nom dans
> l'export CSV — le nom affichable reste disponible via GraphQL si besoin).

### PDF synthèse (`/rapports/synthese/pdf/`)
Document A4 « Synthèse Campagne » avec 3 blocs de cartes chiffrées :
- **Relevés** : abonnés à relever, relevés effectués, en attente, progression %, consommation totale.
- **Facturation** : factures générées, envoyées (WhatsApp), payées, impayées, montant total facturé.
- **Paiements** : montant encaissé, reste à recouvrer, factures impayées, taux de recouvrement.

Les chiffres proviennent du **Reporting Service** (agrégat pré-calculé).

### PDF bilan des impayés (`/bilan-impayes/pdf/`)
Document A4 global (toutes campagnes) : liste des créances impayées classées par
ancienneté + répartition par étape de relance.

---

## 4. Codes de retour à gérer

| Code | Signification | Action UI suggérée |
|---|---|---|
| **200** | OK, corps = fichier | déclencher le téléchargement / l'ouverture |
| **400** | `campagne_id` manquant (routes 1-3) | bug applicatif : ne pas appeler sans id |
| **401** | Token absent / invalide / expiré | rafraîchir le token ou rediriger login |
| **403** | Rôle insuffisant (ni ADMIN ni COMPTABLE) | masquer le bouton pour ces rôles |
| **404** | Synthèse : la campagne n'a **aucune statistique** agrégée | toast « Aucune donnée pour cette campagne » |
| **503** | Service amont indisponible | toast « Export momentanément indisponible » |

> Sur erreur, le corps est un **JSON** `{ "erreur": "..." }` (et non un fichier).
> En `responseType: 'blob'`, pensez à re-parser ce blob en JSON pour afficher le
> message (voir §6, `readErrorBlob`).

---

## 5. Configuration du proxy (dev)

Ces routes REST doivent être proxifiées comme `/graphql`. Ajoutez-les dans
`frontend/proxy.conf.json` :

```jsonc
{
  "/graphql":        { "target": "https://localhost:8443", "secure": false, "changeOrigin": true },
  "/rapports":       { "target": "https://localhost:8443", "secure": false, "changeOrigin": true },
  "/bilan-impayes":  { "target": "https://localhost:8443", "secure": false, "changeOrigin": true },
  "/factures":       { "target": "https://localhost:8443", "secure": false, "changeOrigin": true }
}
```

En production, nginx (déjà devant la Gateway) sert le build Angular et proxifie
ces chemins sous le même domaine — aucune config supplémentaire.

---

## 6. Implémentation Angular

### Service d'export

```typescript
// exports.service.ts
import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({ providedIn: 'root' })
export class ExportsService {
  constructor(private http: HttpClient) {}

  facturesCsv(campagneId: string): Observable<void> {
    return this.telecharger('/rapports/factures.csv', { campagne_id: campagneId });
  }

  paiementsCsv(campagneId: string): Observable<void> {
    return this.telecharger('/rapports/paiements.csv', { campagne_id: campagneId });
  }

  synthesePdf(campagneId: string): Observable<void> {
    return this.telecharger('/rapports/synthese/pdf/', { campagne_id: campagneId });
  }

  bilanImpayesPdf(): Observable<void> {
    return this.telecharger('/bilan-impayes/pdf/', {});
  }

  /**
   * GET binaire + enregistrement du fichier. L'intercepteur d'auth (celui qui
   * ajoute déjà `Authorization: Bearer` sur /graphql) DOIT s'appliquer ici :
   * assurez-vous que sa condition de match couvre aussi ces chemins REST.
   */
  private telecharger(url: string, params: Record<string, string>): Observable<void> {
    return this.http
      .get(url, { params, responseType: 'blob', observe: 'response' })
      .pipe(map((resp) => this.enregistrer(resp)));
  }

  private enregistrer(resp: HttpResponse<Blob>): void {
    const blob = resp.body!;
    const nom = this.nomDepuisEntete(resp) ?? 'export';
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = nom; // pour le PDF inline, on force le téléchargement ; retirez `download` pour ouvrir dans l'onglet
    a.click();
    URL.revokeObjectURL(objectUrl);
  }

  private nomDepuisEntete(resp: HttpResponse<Blob>): string | null {
    const cd = resp.headers.get('Content-Disposition');
    if (!cd) return null;
    const m = /filename="?([^"]+)"?/.exec(cd);
    return m ? m[1] : null;
  }
}
```

### Lecture du message d'erreur (le corps 4xx/5xx est un JSON, pas un fichier)

```typescript
// À placer dans le bloc error du subscribe, err étant un HttpErrorResponse.
async function readErrorBlob(err: HttpErrorResponse): Promise<string> {
  if (err.error instanceof Blob) {
    try {
      const txt = await err.error.text();
      return JSON.parse(txt).erreur ?? 'Erreur inconnue';
    } catch {
      return 'Erreur inconnue';
    }
  }
  return err.error?.erreur ?? 'Erreur inconnue';
}
```

### Usage dans un composant (écran 13)

```typescript
export class RapportsComponent {
  chargement = false;

  constructor(private exports: ExportsService, private toast: ToastService) {}

  exporterFactures(campagneId: string): void {
    this.chargement = true;
    this.exports.facturesCsv(campagneId).subscribe({
      next: () => (this.chargement = false),
      error: async (err) => {
        this.chargement = false;
        this.toast.error(await readErrorBlob(err));
      },
    });
  }
  // idem pour paiementsCsv / synthesePdf / bilanImpayesPdf
}
```

```html
<!-- Boutons réservés ADMIN/COMPTABLE (le backend renvoie 403 sinon, mais cachez-les) -->
<button (click)="exporterFactures(campagne.id)"  [disabled]="chargement">Exporter les factures (CSV)</button>
<button (click)="exporterPaiements(campagne.id)" [disabled]="chargement">Exporter les paiements (CSV)</button>
<button (click)="exporterSynthese(campagne.id)"  [disabled]="chargement">Télécharger la synthèse (PDF)</button>
<button (click)="exporterBilan()"                [disabled]="chargement">Bilan des impayés (PDF)</button>
```

---

## 7. Points de vigilance

1. **Intercepteur d'auth** : vérifiez que son `if (req.url.includes('/graphql'))`
   (ou équivalent) couvre **aussi** `/rapports` et `/bilan-impayes`, sinon 401.
2. **Rôles** : n'affichez ces boutons que pour ADMIN et COMPTABLE (défense en
   profondeur — le backend refuse déjà avec un 403).
3. **`campagne_id`** : toujours fourni pour les routes 1-3 ; c'est l'UUID de la
   campagne (le même que celui manipulé côté GraphQL `campagnes`).
4. **Ouvrir vs télécharger un PDF** : gardez `a.download = nom` pour forcer
   l'enregistrement, ou faites `window.open(objectUrl)` pour un aperçu inline.
