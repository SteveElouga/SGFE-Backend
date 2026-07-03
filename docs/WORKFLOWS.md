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
4. ⚠️ L'ancien refresh token n'est **pas** révoqué à ce stade — il reste valide jusqu'à expiration naturelle (voir `ANO-006`).
5. `[Gateway]` Repose le nouveau cookie refresh, retourne le nouvel access token.

### 1.3 Déconnexion (`logout`)

1. `[Gateway]` → `Auth.Logout(access_token)`.
2. `[Auth]` Ajoute le `jti` de l'access token courant à `RevokedToken`. Le refresh token du cookie n'est pas explicitement révoqué par cette mutation.

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
- ⚠️ Aucune de ces transitions ne vérifie ni ne bloque une campagne en cours impliquant cet abonné (voir `ANO-003`).

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

1. `[Campagne]` (APScheduler, cron 7h00) `campagne_planifiee_job` cherche une campagne `PLANIFIEE` dont `date_planifiee` = aujourd'hui **ou hier** (rattrapage si le job a été manqué), passe la première trouvée (`.first()`) à `EN_COURS`.
2. `[Campagne]` → `Notification.NotifierAdmins(evenement="CAMPAGNE_DEMARREE", ...)` (dégradation gracieuse si Notification est indisponible).
3. ⚠️ Si deux campagnes partagent la même `date_planifiee`, une seule démarre par exécution (`ANO-019`).

### 3.3 Affectation d'agents (`affecterAgent`)

*Rôle requis : ADMIN ou SUPERVISEUR propriétaire de la campagne (vérifié côté Gateway via `_verifier_acces_campagne`).*

Associe un `CampagneAgent` (agent ↔ campagne), idempotent (pas de doublon).

### 3.4 Saisie d'un index (`saisirIndex`)

*Rôle requis : ADMIN, AGENT (affecté à la campagne), ou SUPERVISEUR propriétaire.*

1. `[Gateway]` Vérifie l'accès (rôle + propriété/affectation) avant l'appel.
2. `[Campagne]` → `SaisirIndex(campagne_id, abonne_id, nouveau_index)`.
3. `[Campagne]` Si le `Releve` n'existe pas encore pour ce couple (campagne, abonné), il est **créé à la volée** : `ancien_index` = dernier index connu de cet abonné toutes campagnes confondues (ou `0.0` si premier relevé).
4. `[Campagne]` Valide : la campagne doit être `EN_COURS`, le relevé ne doit pas déjà être `RELEVE`, et **`nouveau_index ≥ ancien_index`** (règle métier obligatoire, respectée ici).
5. `[Campagne]` Calcule `consommation`, passe le relevé en statut `RELEVE`.
6. ⚠️ **Aucune vérification du statut ACTIF de l'abonné** n'est faite à cette étape ni avant (`ANO-003`) — un abonné suspendu peut être relevé normalement.

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

### 5.1 Enregistrement d'un paiement (`enregistrerPaiement`)

*Rôle requis : ADMIN ou COMPTABLE.*

1. `[Paiement]` Valide `montant > 0`.
2. Si `mode_paiement ∈ {MOBILE_MONEY, VIREMENT}` → `reference_transaction` obligatoire et non vide (sinon refus). Aucune contrainte pour `ESPECES`.
3. Récupère le `SoldeFacture` de la facture (doit avoir été initialisé par `InitialiserSolde`, sinon erreur « facture introuvable »).
4. Anti-surpaiement : `montant > solde_restant` → refus (empêche tout dépassement cumulé sur plusieurs versements).
5. Crée le `Paiement`, met à jour `SoldeFacture` : `montant_paye += montant`, `solde_restant = montant_total - montant_paye`, statut dérivé (`PAYEE` si `montant_paye ≥ montant_total`, sinon `PARTIELLE`).
6. `[Paiement]` → `Facturation.UpdateStatutFacture(...)` (dégradé).
7. Si le nouveau statut est `PAYEE` :
   - `[Paiement]` Résout `SuiviImpaye.resolu_le` s'il existait.
   - `[Paiement]` → `Abonne.ReactiverAbonne(abonne_id)` si l'abonné avait été suspendu.
   - `[Paiement]` → `Notification.EnvoyerRelance(etape=0)` — confirmation de paiement par WhatsApp.
8. Si le nouveau statut est `PARTIELLE` :
   - `[Paiement]` Pose `SuiviImpaye.relances_suspendues_jusqu = aujourd'hui + 5 jours` (délai par défaut) — met en pause toute relance ultérieure pendant ce délai, y compris une éventuelle suspension automatique.

### 5.2 Paiements partiels multiples

Chaque appel `enregistrerPaiement` relit le solde courant en base et recalcule à partir de là — aucune limite au nombre de versements tant que la somme ne dépasse pas `montant_total`. Exemple vérifié par les tests : deux versements de 150 sur une facture de 300 → statut `PAYEE` après le second.

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
   - Sinon, tente successivement (chaque étape est indépendante, pas de séquentialité stricte imposée) :
     - **Rappel 1** si `jours_depasses ≥ délai_rappel_1` et pas déjà envoyé → `Notification.EnvoyerRelance(etape=1)`.
     - **Rappel 2** si `jours_depasses ≥ délai_rappel_2` et pas déjà envoyé → `Notification.EnvoyerRelance(etape=2)`.
     - **Avertissement** si `jours_depasses ≥ délai_avertissement` et pas déjà envoyé → `Notification.EnvoyerRelance(etape=3)`.
   - **Suspension automatique** si `jours_depasses ≥ délai_suspension` et pas déjà effectuée et `suspension_auto=True` :
     - `[Paiement]` → `Abonne.SuspendreAbonne(abonne_id)`.
     - `[Paiement]` → `Notification.EnvoyerRelance(etape=4)` — message de suspension à l'abonné.
     - `[Paiement]` → `Notification.NotifierAdmins(evenement="SUSPENSION", ...)`.
4. ⚠️ Note de comportement : si le cron n'a pas tourné pendant plusieurs jours (panne), au passage suivant, plusieurs étapes de rappel peuvent se déclencher **le même jour** en cascade pour une même facture (rattrapage non séquentiel, comportement probablement voulu mais non documenté comme tel).

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

⚠️ Il n'existe **pas** d'étape 5 « rétablissement » (`TypeEnvoi.RETABLISSEMENT` défini en base mais jamais produit — `ANO-013`). La confirmation de paiement complet passe par `EnvoyerRelance(etape=0)` (voir §5.1 étape 7), pas par un message dédié au rétablissement après suspension.

### 7.4 Espace abonné public (accès tokenisé, sans authentification JWT)

**Liste des factures** — `GET /espace-abonne/<token>/` :
1. `[Gateway]` → `Notification.ValiderToken(token)` — vérifie existence, `is_active`, non-expiration. Retourne `abonne_id`.
2. `[Gateway]` → `Facturation.ListFactures(abonne_id)` — **toutes** les factures de cet `abonne_id` (dérivé du token, jamais fourni par le client — empêche la fuite d'autres abonnés à ce niveau).
3. `[Gateway]` Enrichit chaque facture avec son solde via `Paiement.GetSolde` (dégradé : continue sans le solde si indisponible).

**Téléchargement PDF** — `GET /espace-abonne/<token>/facture/<facture_id>/pdf/` :
1. `[Gateway]` → `Notification.ValiderToken(token)` (même validation).
2. `[Gateway]` → `Facturation.GetFacturePDF(facture_id)` — ⚠️ **sans vérifier que `facture_id` appartient bien à l'`abonne_id` du token validé** (`ANO-002`, faille IDOR). Ce point doit être corrigé avant toute mise en production de l'espace abonné public.

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
