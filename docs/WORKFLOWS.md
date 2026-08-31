# Workflows — Système de Gestion de Facturation d'Eau

> **Nature de ce document :** description pas-à-pas de ce que fait **réellement** le code pour chaque fonctionnalité, du point d'entrée (mutation/query GraphQL ou cron) jusqu'aux effets de bord inter-services. Complète `docs/ETAT_DU_SYSTEME.md` (qui liste les anomalies) et `docs/ARCHITECTURE.md` (qui décrit la conception cible). Là où le comportement réel s'écarte de la règle métier attendue, une note ⚠️ renvoie vers le registre d'anomalies (`ETAT_DU_SYSTEME.md` §4, codes `ANO-XXX`).
> **Convention** : chaque étape indique le service qui l'exécute entre crochets `[Service]`. Les appels gRPC sortants sont notés `→ Service.RPC`.
> **Dernière mise à jour :** 2026-07-02. À maintenir à chaque changement de comportement d'un workflow (nouvelle étape, nouvelle validation, nouveau service impliqué).

---

## Table des matières

1. [Authentification et comptes](#1-authentification-et-comptes)
2. [Gestion des abonnés](#2-gestion-des-abonnés)
3. [Campagne de relevé](#3-campagne-de-relevé)
4. [Facturation](#4-facturation)
5. [Paiement](#5-paiement)
6. [Gestion des impayés](#6-gestion-des-impayés)
7. [Notification WhatsApp](#7-notification-whatsapp)
8. [Configuration système](#8-configuration-système)

---

## 1. Authentification et comptes

### 1.1 Connexion (`login`)

1. `[Gateway]` Mutation `login(identifier, password)` — pas d'authentification requise.
2. `[Gateway]` → `Auth.Login(identifier, password)`.
3. `[Auth]` Résout l'utilisateur par `username` **ou** `phone_number` (`Q(username=) | Q(phone_number=)`).
4. `[Auth]` Si `locked_until` est dans le futur → refus immédiat (`AuthenticationError`).
5. `[Auth]` Si `is_active=False` (compte pas encore activé) → refus.
6. `[Auth]` Vérifie le mot de passe :
   - Échec → incrémente `failed_attempts` ; si `≥ MAX_LOGIN_ATTEMPTS` (défaut 5) → pose `locked_until = now + LOCKOUT_DURATION_MINUTES` (défaut 15 min).
   - Succès → réinitialise `failed_attempts`/`locked_until`, génère un access token (24h, claim `role`) et un refresh token (7j, claim `role`) via SimpleJWT.
7. `[Gateway]` Pose le refresh token dans un cookie `HttpOnly + Secure(prod) + SameSite=Strict`, jamais renvoyé dans le corps de la réponse GraphQL. Retourne l'access token au client.

### 1.2 Rafraîchissement (`refreshToken`)

1. `[Gateway]` Lit le cookie `refresh_token`.
2. `[Gateway]` → `Auth.RefreshToken(refresh_token)`.
3. `[Auth]` Vérifie que le token n'est pas dans `RevokedToken` (par `jti`), retrouve l'utilisateur, régénère un **nouveau couple complet** access + refresh.
4. ✅ `ANO-006` résolu (PR #22) — l'ancien refresh token est révoqué immédiatement après l'émission du nouveau couple (rotation).
5. `[Gateway]` Repose le nouveau cookie refresh, retourne le nouvel access token.

### 1.3 Déconnexion (`logout`)

1. `[Gateway]` → `Auth.Logout(access_token)`.
2. `[Auth]` Ajoute le `jti` de l'access token courant à `RevokedToken`. Le refresh token du cookie n'est pas explicitement révoqué par cette mutation.
3. Si le token fourni est déjà invalide/expiré : lève désormais `UNAUTHENTICATED`, comme les 12 autres RPC (✅ `ANO-020` résolu, PR #32 — auparavant seul `Logout` gérait ce cas localement et renvoyait un `StatusResponse(success=False)` au lieu de laisser `ErrorHandlingInterceptor` centraliser la conversion en code gRPC).

### 1.4 Création de compte (`createUser`, ADMIN uniquement)

1. `[Gateway]` `require_role(info, "ADMIN")`, puis → `Auth.CreateUser(...)`.
2. `[Auth]` Crée l'utilisateur avec `is_active=False` et `set_unusable_password()` (**aucun mot de passe temporaire** — voir écart avec `docs/SRS.md` EF-AUTH-004 noté dans `ETAT_DU_SYSTEME.md` §8).
3. Branche selon le rôle :
   - **ADMIN** → email obligatoire ; crée un `PasswordSetupToken` (validité 48h par défaut) et envoie un lien `FRONTEND_URL/set-password?token=...` via **Brevo** (e-mail).
   - **AGENT / COMPTABLE / SUPERVISEUR** → crée un `PhoneOtpToken` (code 6 chiffres, **haché**, jamais stocké en clair) et l'envoie via **whatsapp-web.js** avec un lien `FRONTEND_URL/activer-compte?phone=...`.

### 1.5 Activation du compte

**Canal e-mail (ADMIN)** — mutation `activateAccount(token, password)` :
1. `[Auth]` Vérifie que le `PasswordSetupToken` est valide (non expiré, non utilisé).
2. Définit le mot de passe, passe `is_active=True`, marque le token utilisé — le tout dans une transaction atomique.

**Canal OTP (autres rôles)** — mutation `verifyOtpAndSetPassword` :
1. `[Auth]` Vérifie le code OTP (comparaison sur le hash), non expiré, non utilisé.
2. Définit le mot de passe, active le compte, marque l'OTP utilisé.

### 1.6 Réinitialisation du mot de passe

Deux canaux distincts, tous deux **anti-énumération** (réponse « succès » silencieuse même si l'identifiant est inconnu) :
- `requestPasswordReset(email)` — **ADMIN uniquement** (par construction, seul un ADMIN a un e-mail enregistré) → nouveau `PasswordSetupToken` + e-mail Brevo.
- `requestPhoneOtp(phoneNumber)` — tous rôles, y compris ADMIN en option → nouveau `PhoneOtpToken`, ancien OTP non utilisé invalidé, envoi WhatsApp.

---

## 2. Gestion des abonnés

*Rôle requis pour toutes les mutations : ADMIN uniquement (voir tableau de permissions CLAUDE.md racine).*

### 2.1 Création d'un abonné (`createAbonne`)

1. `[Gateway]` `require_role(info, "ADMIN")` → `Abonne.CreateAbonne(...)`.
2. `[Abonné]` Valide le téléphone (format E.164).
3. `[Abonné]` Génère `numero_abonne` (format `AB-XXXX`) via `select_for_update()` pour éviter toute collision en création concurrente.
4. `[Abonné]` Crée `Abonne` (statut `ACTIF`) **et** son `Compteur` initial dans une seule transaction atomique — le compteur est **obligatoire** dès la création, pas de création d'abonné sans compteur.
5. `[Abonné]` Publie `ABONNE_CREATED` sur Redis (canal `abonne:events`) — consommé uniquement par la subscription GraphQL de la Gateway (⚠️ **pas** par Campagne Service, malgré ce que documente CLAUDE.md racine — voir `ANO-003bis`).

### 2.2 Suspension / réactivation / résiliation

Machine à états simple, appliquée par `suspendreAbonne` / `reactiverAbonne` / `resilierAbonne` :

```
ACTIF ──suspendre──► SUSPENDU ──réactiver──► ACTIF
  │                     │
  └────────resilier─────┴────────resilier────► RESILIE  (état terminal, aucun retour possible)
```

- `suspendre` exige le statut `ACTIF` de départ.
- `reactiver` exige le statut `SUSPENDU` de départ.
- `resilier` refuse uniquement si déjà `RESILIE` — accessible depuis `ACTIF` ou `SUSPENDU`.
- ✅ Depuis la résolution d'`ANO-003` (PR #20), le statut ACTIF est vérifié au moment où un abonné est *ajouté* à une campagne (création du `Releve`). Limite résiduelle non couverte (hors périmètre d'ANO-003, non cataloguée séparément) : si un abonné est suspendu *après* que son `Releve` a déjà été créé dans une campagne en cours, la saisie ultérieure de son index (`SaisirIndex` sur un relevé existant) ne revérifie pas son statut à ce moment précis.

### 2.3 Remplacement de compteur (`remplacerCompteur`)

1. `[Abonné]` Valide `index_fermeture ≥ index_initial` de l'ancien compteur.
2. Dans une transaction atomique : archive l'ancien compteur (statut `REMPLACE`), crée le nouveau (statut `ACTIF`), trace l'opération dans `HistoriqueCompteur`.

### 2.4 Consultation de l'historique (`historiqueCompteur`)

Retourne uniquement l'historique des **remplacements de compteur** (pas les relevés, factures ou paiements — ceux-ci vivent dans d'autres services et ne sont pas agrégés ici ; voir écart avec le libellé de `docs/SRS.md` EF-ABO-004 noté dans `ETAT_DU_SYSTEME.md`).

---

## 3. Campagne de relevé

### 3.1 Création (`creerCampagne`)

*Rôle requis : ADMIN ou SUPERVISEUR.*

1. `[Gateway]` → `Campagne.CreateCampagne(nom, periode_mois, periode_annee, date_planifiee, created_by, numero_mobile_money, generer_factures_auto, envoyer_whatsapp_auto, demarrer_maintenant)`.
2. `[Campagne]` Valide : nom non vide, mois 1-12, année ≥ 2000, `created_by` obligatoire, `numero_mobile_money` (9 chiffres exact si fourni).
3. `[Campagne]` Statut initial déterminé par `demarrer_maintenant` :
   - `True` → la campagne démarre directement en `EN_COURS`.
   - `False` (défaut) → statut `PLANIFIEE`, en attente du scheduler ou d'un démarrage manuel.

### 3.2 Démarrage planifié (scheduler quotidien 7h00)

1. `[Campagne]` (APScheduler, cron 7h00) `campagne_planifiee_job` cherche **toutes** les campagnes `PLANIFIEE` dont `date_planifiee` = aujourd'hui **ou hier** (rattrapage si le job a été manqué), et les passe **toutes** à `EN_COURS` (✅ `ANO-019` résolu, PR #31 — auparavant seule la première trouvée démarrait).
2. `[Campagne]` → `Notification.NotifierAdmins(evenement="CAMPAGNE_DEMARREE", ...)` pour chacune (dégradation gracieuse si Notification est indisponible).

### 3.3 Affectation d'agents (`affecterAgent`)

*Rôle requis : ADMIN ou SUPERVISEUR propriétaire de la campagne (vérifié côté Gateway via `_verifier_acces_campagne`).*

Associe un `CampagneAgent` (agent ↔ campagne), idempotent (pas de doublon).

### 3.4 Saisie d'un index (`saisirIndex`)

*Rôle requis : ADMIN, AGENT (affecté à la campagne), ou SUPERVISEUR propriétaire.*

1. `[Gateway]` Vérifie l'accès (rôle + propriété/affectation) avant l'appel.
2. `[Campagne]` → `SaisirIndex(campagne_id, abonne_id, nouveau_index)`.
3. `[Campagne]` Si le `Releve` n'existe pas encore pour ce couple (campagne, abonné), il est **créé à la volée** via `ajouter_abonne_campagne` : `ancien_index` = dernier index connu de cet abonné toutes campagnes confondues (ou `0.0` si premier relevé). ✅ `ANO-003` résolu (PR #20) — cette création vérifie désormais `Abonne.GetAbonne(abonne_id).statut == "ACTIF"` avant de créer le relevé, de façon bloquante (refus si non ACTIF ou si Abonné Service est inaccessible).
4. `[Campagne]` Valide : la campagne doit être `EN_COURS`, le relevé ne doit pas déjà être `RELEVE`, et **`nouveau_index ≥ ancien_index`** (règle métier obligatoire, respectée ici).
5. `[Campagne]` Calcule `consommation`, passe le relevé en statut `RELEVE`.

### 3.5 Suivi de progression (`progression`)

Agrège les compteurs de relevés par statut (`A_RELEVER`/`RELEVE`/`NON_RELEVE`/`ESTIME`) et calcule le pourcentage d'avancement `(relevés + non_relevés + estimés) / total × 100`.

### 3.6 Clôture (`cloturerCampagne`) → déclenchement de la facturation

*Rôle requis : ADMIN ou SUPERVISEUR propriétaire.*

1. `[Campagne]` Transition `EN_COURS → CLOTUREE`, fixe `date_cloture`.
2. Si `generer_factures_auto=True` (toggle posé à la création) : `[Campagne]` → `Facturation.GenererFactures(campagne_id)`, **appel gRPC direct et synchrone** (pas de file d'attente).
3. ⚠️ Si Facturation est indisponible à cet instant précis, l'appel échoue silencieusement (log warning) : la campagne reste `CLOTUREE` mais **aucune facture n'est jamais générée**, sans retry ni alerte visible pour l'ADMIN.

---

## 4. Facturation

### 4.1 Génération automatique (déclenchée par 3.6, ou appelable manuellement via `genererFactures`)

1. `[Facturation]` → `Campagne.ListReleves(campagne_id)`, filtre côté client les relevés `statut == "RELEVE"` (les `NON_RELEVE`/`ESTIME` sont exclus — aucune facture générée pour eux). Échec bloquant si Campagne est indisponible (pas de dégradation ici, volontairement).
2. `[Facturation]` → `Config.GetConfig("delai_paiement_jours")` — ✅ `ANO-001` résolu (PR #18) : la valeur configurée par l'ADMIN est désormais bien lue (défaut 5 jours si Config Service indisponible). → `Config.GetInfosSociete()` pour les informations à afficher sur le PDF.
3. Pour chaque relevé : `montant = consommation × prix_m3` (tarif actif au moment de la génération, **copié**, jamais en FK), `date_limite = date_releve + delai_paiement_jours`, numéro séquentiel `FACT-AAAA-MM-XXXX`.
4. Transaction atomique : création de la `Facture` (statut `IMPAYEE`) puis génération et sauvegarde du PDF (ReportLab). Si la génération PDF échoue, la facture reste créée avec `pdf_path=""` (régénérable à la demande via `GetFacturePDF`).
5. Hors transaction, avec dégradation gracieuse : `[Facturation]` → `Paiement.InitialiserSolde(facture_id, montant_total, date_limite)`, puis si `envoyer_whatsapp_auto=True` → `Notification.EnvoyerFacture(facture_id)`.
6. Aucun appel vers Reporting (service inexistant, `ANO-016`).

### 4.2 Mise à jour du statut suite à un paiement

`Paiement.EnregistrerPaiement` appelle en aval `Facturation.UpdateStatutFacture(facture_id, nouveau_statut)` (dégradation gracieuse côté Paiement si Facturation est down). Facturation ne notifie personne d'autre après cette mise à jour (pas de propagation vers Reporting, inexistant).

### 4.3 Gestion du tarif (`updateTarif`, ADMIN uniquement)

Désactive tous les tarifs existants (`is_active=False`) puis crée le nouveau tarif actif avec `prix_m3` et `date_effet`. Les factures déjà émises conservent leur `prix_m3` copié — non affectées rétroactivement.

---

## 5. Paiement

> ⚠️ **Réécrit le 31 août 2026.** Cette section décrivait un « anti-surpaiement »
> qui refusait tout versement dépassant le solde. Ce refus n'existe plus depuis
> #141 : le surpaiement est accepté et cascade sur les impayés. Elle ne
> documentait par ailleurs **que** `enregistrerPaiement`, alors que
> `enregistrerPaiementAbonne` est le geste que l'interface emploie — et c'est
> précisément ce silence qui a laissé ce second chemin sans aucun effet aval.

**Deux chemins d'encaissement**, et un seul jeu de conséquences.

### 5.1 Encaissement au comptoir (`enregistrerPaiementAbonne`) — le geste courant

*Rôle requis : ADMIN ou COMPTABLE.* C'est celui que l'interface emploie : le caissier saisit un montant, le système répartit.

1. `[Paiement]` Valide `montant > 0`, et `reference_transaction` non vide si `mode_paiement ∈ {MOBILE_MONEY, VIREMENT}`.
2. Idempotence : une `reference_transaction` déjà vue ne re-crédite rien (rejeu réseau, double-clic).
3. Impute **du plus anciennement exigible au plus récent** (tri sur `date_limite_paiement`) : le plus vieux solde s'éteint d'abord, le reliquat déborde sur le suivant. C'est l'ancienneté qui déclenche relances et suspension — imputer dans l'autre sens laisserait vieillir la mauvaise dette.
4. Une **écriture `Paiement` par facture touchée**, toutes marquées du même `versement_id`. `Paiement.montant` est la part imputée à *sa* facture, pas la somme reçue.
5. Ce qui reste après extinction de **toutes** les dettes part en avoir (`TROP_PERCU`), et seulement là.
6. Puis les conséquences communes — voir §5.3.

### 5.2 Encaissement sur une facture nommée (`enregistrerPaiement`)

*Rôle requis : ADMIN ou COMPTABLE.* Un caissier a parfois besoin de viser une facture précise.

Même déroulé, à un détail près : **la facture visée est servie d'abord**, puis l'excédent cascade sur les impayés, puis l'avoir. Un abonné règle la facture dont il a reçu le message ; c'est celle-là qui s'éteint en premier.

### 5.3 Les conséquences d'un encaissement (les deux chemins)

Portées par un seul chemin de code (`_propager_versement`). **Par facture touchée**, cascade comprise :

1. `[Paiement]` → `Facturation.UpdateStatutFacture(...)` (dégradé).
2. Si `PAYEE` → résout `SuiviImpaye.resolu_le`.
3. Si `PARTIELLE` → pose `SuiviImpaye.relances_suspendues_jusqu = aujourd'hui + N jours` (`impaye_suspension_relances`, défaut 5) : met en pause toute relance ultérieure, suspension automatique comprise.
4. Publie `PAIEMENT_STATS` sur le flux Reporting, avec **la part imputée à cette facture et la campagne de cette facture**.
5. Publie l'événement de la souscription GraphQL `paiementCree`.

**Une fois par versement** :

6. Si — et seulement si — `total_du_abonne(...) == 0` : `[Paiement]` → `Abonne.ReactiverAbonne(...)` et `Notification.EnvoyerRelance(etape=0)`. RS-005 dit « paiement **intégral** après suspension → ACTIF » : *intégral* qualifie la dette, pas une ligne de la dette. Un abonné qui règle une facture sur trois ne retrouve pas l'eau.
7. `[Paiement]` → `Notification.EnvoyerRecu(...)` : **un** reçu pour le geste. `montant` = ce que l'abonné a tendu (excédent compris) ; `solde_restant` = ce qu'il doit **encore en tout**, et non le reste d'une facture parmi plusieurs.

Tout est en dégradation gracieuse : un service aval injoignable n'annule jamais un versement déjà écrit.

### 5.4 Paiements partiels multiples

Chaque appel relit le solde courant en base et recalcule à partir de là — aucune limite au nombre de versements. Un versement qui dépasse ne provoque pas de refus : il déborde sur les impayés, puis sur l'avoir. Exemple vérifié par les tests : deux versements de 150 sur une facture de 300 → statut `PAYEE` après le second.

### 5.5 Annulation d'un versement (`annulerPaiement`)

On annule un **versement**, pas une de ses lignes : les écritures partageant le `versement_id` sont annulées d'un bloc. Un excédent déjà porté à l'avoir est reprisé (refus si le crédit a déjà été dépensé), les `SuiviImpaye` sont rouverts (`resolu_le = None`), les recettes du read model sont décrémentées (`PAIEMENT_ANNULE`), et l'abonné est prévenu (étape 5).


### 5.6 Exports comptables (CSV)

*Rôle requis : ADMIN ou COMPTABLE. Routes HTTP, pas GraphQL — un flux CSV s'y prête mal.*

```
GET /rapports/factures.csv    ?campagne_id=  et/ou  ?date_debut=&date_fin=
GET /rapports/paiements.csv   ?campagne_id=  et/ou  ?date_debut=&date_fin=
```

Bornes ISO `AAAA-MM-JJ`, **incluses**. Aucun critère = tout l'historique, ce qu'une clôture d'exercice demande. Une date mal formée est **refusée** (400) plutôt qu'ignorée : un export silencieusement non borné rendrait tout l'historique là où le comptable a demandé un mois, et rien ne le lui dirait avant qu'il somme la colonne.

| Export | La période porte sur | Pourquoi cette date |
|---|---|---|
| factures | `date_generation` | une régularisation n'a pas de relevé — c'est la seule date que portent les deux natures |
| paiements | `date_paiement` | la date de caisse, celle qu'un journal demande |

> ⚠️ **`campagne_id` était OBLIGATOIRE** (400 sinon). Deux verrous, tous deux bloquants pour une clôture :
>
> 1. **Aucun journal par période.** Il fallait exporter campagne par campagne et recoller les fichiers à la main.
> 2. **Les régularisations étaient exportables par aucun chemin.** `creer_regularisation` crée la facture ET son solde avec `campagne_id=""` ; le filtre par campagne ne les trouvait donc jamais, ni la facture ni ses paiements. La seule dette qu'on saisit à la main — l'arriéré antérieur à la mise en service — était structurellement invisible de la comptabilité exportée.

**Colonnes ajoutées, et pourquoi elles changent les totaux.**

*Factures* : `nature`, `motif`, `campagne_id`, `date_generation`. Sans `nature`, rien ne distingue dans le fichier une régularisation — consommation à 0, index vides — d'une facture de consommation. Le comptable lisait des lignes à 0 m³ sans savoir pourquoi, et sans pouvoir rapprocher le montant d'un motif. `date_generation` est la colonne qui a servi à borner l'export : sans elle, on ne peut pas vérifier son propre extrait.

*Paiements* : `annule`, `annule_le`, `annule_par`, `motif_annulation`. Les paiements annulés étaient **déjà** dans l'export — ni le repo ni la vue ne les excluaient — mais rien ne les signalait. Un comptable qui sommait la colonne `montant` comptait donc comme recette des versements annulés. Faux, et faux en silence.

---

## 6. Gestion des impayés

### 6.1 Déclenchement (cron quotidien 8h00)

`[Paiement]` (APScheduler) `impaye_checker_job` → `ImpayeService.verifier_et_escalader()` :

1. Récupère les délais de relance depuis Config Service (✅ `ANO-001` résolu, PR #18 — les valeurs configurées par l'ADMIN sont désormais bien lues ; défauts si Config Service indisponible : rappel_1=0j, rappel_2=3j, avertissement=7j, suspension=10j, suspension_auto=True).
2. Récupère toutes les factures dont `date_limite_paiement < aujourd'hui` et `statut ≠ PAYEE` (inclut `IMPAYEE` et `PARTIELLE`).
3. Pour chaque facture :
   - `jours_depasses = aujourd'hui - date_limite_paiement`.
   - Récupère ou crée le `SuiviImpaye` (étape initiale 1).
   - **Si `relances_suspendues_jusqu ≥ aujourd'hui`** (posé lors d'un paiement partiel récent) → **aucune relance n'est tentée**, y compris la suspension. Passe à la facture suivante.
   - Sinon, envoie **UNE seule étape** : la plus avancée que le retard justifie, et elle seule.

     | Retard | Étape envoyée |
     |---|---|
     | `≥ délai_suspension`, pas déjà suspendu, `suspension_auto` | **Suspension** (étape 4) |
     | `≥ délai_avertissement` | **Avertissement** (étape 3), avec `jours_avant_suspension = délai_suspension − jours_depasses` |
     | `≥ délai_rappel_2` | **Rappel 2** (étape 2) |
     | `≥ délai_rappel_1` | **Rappel 1** (étape 1) |

     La suspension appelle en outre `Abonne.SuspendreAbonne(abonne_id)` et `Notification.NotifierAdmins(evenement="SUSPENSION", …)`.

   - **L'étape n'est marquée envoyée que si le message est réellement parti.** `EnvoyerRelance` rend un `EnvoiResponse` dont le `statut` vaut `ECHEC` quand WhatsApp a échoué ; le client le lit désormais. Un échec laisse l'étape à retenter au passage suivant.
   - **La suspension a lieu même si le message échoue** — la coupure est la décision, le message n'en est que l'annonce — mais l'échec est journalisé en ERREUR et la notification admin porte la mention « ⚠️ L'ABONNÉ N'A PAS PU ÊTRE PRÉVENU ».

4. Une étape par facture et par passage. Un cron resté longtemps à l'arrêt ne produit donc plus de cascade : chaque facture reçoit le message qui correspond à son retard du jour.

> ⚠️ **Réécrit le 31 août 2026.** Ce paragraphe décrivait trois rappels « tentés successivement, chaque étape indépendante », et une note qualifiait la cascade de « comportement probablement voulu mais non documenté comme tel ». Il ne l'était pas.
>
> Pour une facture déjà très en retard — le cas dès qu'on saisit un arriéré avec sa vraie échéance — le premier passage envoyait **quatre messages en quelques secondes**, qui se contredisaient : « arrivée à échéance aujourd'hui », « impayée depuis 3 jours », « impayé depuis 7 jours … suspendue dans 3 jours », « votre ligne d'eau a été suspendue ». Trois étaient faux, et le quatrième rendait les trois autres absurdes.
>
> Les drapeaux des étapes sautées restent à `False` : ces messages n'ont jamais été envoyés, et la piste d'audit doit pouvoir répondre à « m'a-t-on prévenu ? ». Seul `etape_actuelle` porte le niveau atteint.

### 6.1bis Ce que les messages de relance annoncent

**Le reste dû, et non le montant de la facture.** `facture.montant` est la consommation du mois × le prix : un abonné qui avait versé 8 000 sur 10 000 lisait « votre facture de 10 000 FCFA est impayée », son versement ni déduit ni mentionné. Or les factures `PARTIELLE` sont bien relancées — la pause après acompte ne dure que quelques jours.

`FactureResponse` n'expose ni `montant_paye` ni `solde_restant` : la seule source est `Paiement.GetSolde`, et le client existait déjà (il ne servait qu'à l'étape 5). Illisible, le montant **n'est pas imprimé** — jamais imprimé faux.

**Le retard réel, calculé depuis l'échéance.** Les gabarits écrivaient « depuis 3 jours » et « depuis 7 jours » en dur, en supposant que le cron passe le jour exact. Le retard se calcule maintenant depuis `date_limite_paiement` : il n'a besoin d'aucune configuration et ne peut pas se désynchroniser.

**Le délai avant coupure, transmis par le cron.** `EnvoyerRelanceRequest.jours_avant_suspension` (nouveau champ) porte `délai_suspension − jours_depasses`. Un admin qui règle la suspension à 20 jours ne fait plus annoncer 3. À `0`, aucun délai n'est annoncé plutôt qu'un délai faux.

**La suspension dit quoi payer pour être rétabli** : la dette **totale** de l'abonné (`GetDetteAbonne`), pas le montant d'une facture. Depuis RS-005, régler une seule facture ne rétablit pas la ligne — le message envoyait donc payer la mauvaise somme.

### 6.2 Rétablissement après paiement

Ne fait **pas** partie du cron — se produit **immédiatement** au moment du paiement complet (voir §5.1 étape 7 : réactivation de l'abonné + résolution du suivi impayé), pas au prochain passage du cron 8h.

---

## 7. Notification WhatsApp

### 7.1 Envoi de facture (`envoyerFactureWhatsapp`, ou automatique à la génération si `envoyer_whatsapp_auto=True`)

1. `[Notification]` → `Facturation.GetFacture` + `Abonne.GetAbonne` + `Config.GetConfig("token_validite_jours")` (✅ `ANO-001` résolu, PR #18 — défaut 20 jours utilisé seulement si Config Service est indisponible).
2. `[Notification]` Crée un `TokenAcces` (UUID, `date_expiration = aujourd'hui + token_validite_jours`).
3. `[Notification]` Construit le message (texte + lien `{FRONTEND_URL}/espace/{token}`) via `message_builder.build_message_facture`.
4. `[Notification]` Crée l'`Envoi` (statut `EN_ATTENTE`), récupère le PDF via `Facturation.GetFacturePDF` (dégradé : `(b"", "")` si erreur — l'envoi se fait alors sans pièce jointe).
5. `[Notification]` → `whatsapp-service` : `POST /send-with-pdf` (si PDF disponible) ou `POST /send` (sinon).
6. Résultat : statut `ENVOYE` ou `ECHEC` — **jamais d'exception propagée** au niveau gRPC (dégradation gracieuse totale). En cas d'`ECHEC`, `Notification.notifier_admins(evenement="ECHEC_WHATSAPP", ...)` est déclenché automatiquement (email admin via Brevo, si activé en config).

### 7.2 Révocation et renvoi (`renvoyerFactureWhatsapp`, ADMIN uniquement)

1. `[Notification]` Révoque (`is_active=False`) tous les tokens actifs de la facture concernée.
2. `[Notification]` Relance le workflow 7.1 en entier — un nouveau `TokenAcces` est créé, l'ancien lien devient invalide.

### 7.3 Relance impayés (déclenché par le cron 8h — voir §6.1)

`Notification.EnvoyerRelance(facture_id, etape)` pour `etape ∈ {1, 2, 3, 4}` :
- Étape 1 : réutilise un token actif existant s'il y en a un, sinon en crée un nouveau — message avec lien vers l'espace abonné.
- Étapes 2 et 3 : message sans lien (rappel/avertissement textuel).
- Étape 4 (suspension) : récupère le numéro de téléphone de la société via `Config.GetInfosSociete` (dégradation gracieuse par `except Exception` générique) et l'inclut dans le message.

- Étape 0 (confirmation de paiement / rétablissement) : envoyée par `Paiement.EnregistrerPaiement` dès qu'une facture passe au statut `PAYEE` (voir §5.1 étape 7). ✅ `ANO-013` résolu (PR #26) — envoie désormais réellement un message `TypeEnvoi.RETABLISSEMENT` (« Votre paiement de [X] FCFA a été reçu. Votre ligne d'eau est maintenant rétablie. »). Avant ce correctif, cet appel échouait silencieusement à chaque paiement complet (dégradation gracieuse côté Paiement masquant une `ValidationError` côté Notification) : aucune confirmation n'a jamais été envoyée.

### 7.4 Espace abonné public (accès tokenisé, sans authentification JWT)

**Liste des factures** — `GET /espace-abonne/<token>/` :
1. `[Gateway]` → `Notification.ValiderToken(token)` — vérifie existence, `is_active`, non-expiration. Retourne `abonne_id`.
2. `[Gateway]` → `Facturation.ListFactures(abonne_id)` — **toutes** les factures de cet `abonne_id` (dérivé du token, jamais fourni par le client — empêche la fuite d'autres abonnés à ce niveau).
3. `[Gateway]` Enrichit chaque facture avec son solde via `Paiement.GetSolde` (dégradé : continue sans le solde si indisponible).

**Téléchargement PDF** — `GET /espace-abonne/<token>/facture/<facture_id>/pdf/` :
1. `[Gateway]` → `Notification.ValiderToken(token)` (même validation).
2. `[Gateway]` → `Facturation.GetFacture(facture_id)` puis vérifie `facture.abonne_id == token.abonne_id` (✅ `ANO-002` résolu, PR #19 — empêche qu'un abonné télécharge la facture d'un autre en devinant un `facture_id`). Mismatch ou facture introuvable → `404`.
3. `[Gateway]` → `Facturation.GetFacturePDF(facture_id)` — sert le PDF.

### 7.5 Notification admin (`notifierAdmins` — automatique ou manuel)

Déclenché automatiquement sur `ECHEC_WHATSAPP`, `SUSPENSION`, `CAMPAGNE_DEMARREE`. Respecte le toggle Config `notifications_admin_activees` (défaut `True` si la lecture échoue). Envoie un e-mail via Brevo. Dégradation gracieuse totale (aucune exception ne remonte si Brevo est indisponible ou non configuré).

---

## 8. Configuration système

### 8.1 Lecture d'un paramètre par un autre service

1. Le service consommateur (Facturation, Paiement ou Notification) instancie un `ConfigServiceClient` local et appelle `GetConfig(cle)`.
2. `[Config]` Vérifie que `cle` fait partie de `CONFIG_DEFAULTS` (dictionnaire figé en code) ; si oui, `get_or_create` en base avec la valeur par défaut si absente ; si la clé est **inconnue du dictionnaire**, lève `NOT_FOUND`, quelle que soit la casse utilisée par l'appelant.
3. Le client consommateur catch cette erreur (dégradation gracieuse, à des degrés d'homogénéité variables selon le service — voir `ETAT_DU_SYSTEME.md` §5.8) et retombe sur une valeur par défaut codée en dur localement dans son propre `settings.py`.
4. ✅ **`ANO-001` résolu (PR #18)** : `CONFIG_DEFAULTS` utilise désormais exactement les mêmes noms de clés (minuscule) que ceux appelés par Facturation/Paiement/Notification — `delai_paiement_jours`, `token_validite_jours`, tous les `impaye_delai_*`/`impaye_suspension_*`, `email_admin_notifications` et `notifications_admin_activees` sont toutes lues avec succès.

### 8.2 Modification par un ADMIN (`updateConfig`, `updateInfosSociete`)

1. `[Gateway]` `require_role(info, "ADMIN")` → `Config.UpdateConfig(cle, valeur)`.
2. `[Config]` Écrit la nouvelle valeur en base **si la clé existe dans `CONFIG_DEFAULTS`** (sinon `NOT_FOUND`).
3. Depuis la résolution d'`ANO-001` (PR #18), la valeur modifiée est effectivement relue par le service consommateur concerné au prochain appel `GetConfig` — le paramétrage ADMIN a désormais un effet réel.

### 8.3 Informations société (`infosSociete`)

Query **publique**, sans contrôle de rôle (volontaire — alimente l'en-tête des PDF de facture, y compris ceux téléchargés depuis l'espace abonné public non authentifié).
