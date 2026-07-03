# État du Système — Bilan d'implémentation

## Système de Gestion de Facturation d'Eau

> **Nature de ce document :** base de vérité vivante sur ce qui est **réellement construit et vérifié dans le code**, par opposition à ce qui est *prévu* (`SRS.md`), *conçu* (`ARCHITECTURE.md`) ou *décidé* (`ADR.md`). Ce document décrit l'état constaté à sa date de rédaction et doit être mis à jour à chaque évolution significative d'un service (nouvelle règle métier, changement de contrat gRPC/GraphQL, correction d'une anomalie listée ici).
> **Méthode :** chaque service a été audité indépendamment (lecture du code source, exécution réelle des suites de tests, comparaison avec les `.proto` et avec `CLAUDE.md`/`SRS.md`/`ARCHITECTURE.md`). Toute affirmation ci-dessous est traçable à un fichier et, si possible, une ligne.
> **Date de l'audit :** 2026-07-02 · **Branche auditée :** `feature/campagne-demarrer-maintenant` · **Dernier commit :** `2500707`
> **À maintenir par :** quiconque modifie le comportement d'un service — mettre à jour la section du service concerné et le registre d'anomalies (§4) en même temps que le code, dans la même PR si possible.

---



## Table des matières

1. [Résumé exécutif](#1-résumé-exécutif)
2. [Réponse à « est-ce cohérent et ça fonctionne ? »](#2-réponse-à--est-ce-cohérent-et-ça-fonctionne-)
3. [Vue d'ensemble : ce qui est réellement construit](#3-vue-densemble--ce-qui-est-réellement-construit)
4. [Registre des anomalies](#4-registre-des-anomalies)
5. [État détaillé par service](#5-état-détaillé-par-service)
6. [Flux d'événements inter-services — documenté vs réel](#6-flux-dévénements-inter-services--documenté-vs-réel)
7. [Couverture de tests](#7-couverture-de-tests)
8. [Documentation existante à corriger](#8-documentation-existante-à-corriger)
9. [Recommandations priorisées](#9-recommandations-priorisées)

---



## 1. Résumé exécutif


| Brique                  | Statut                                             | Tests                   | Points d'attention majeurs                                                         |
| ----------------------- | -------------------------------------------------- | ----------------------- | ---------------------------------------------------------------------------------- |
| Gateway (GraphQL)       | 🟢 Fonctionnel, riche                              | 84 — **1 échec actuel** | ANO-002 (IDOR PDF) et ANO-015 (subscription non protégée) corrigées — PR #19, #28 |
| Auth Service            | 🟢 Fonctionnel, solide                             | 89 ✅                    | RAS majeur — ANO-006 (rotation refresh token) corrigée, PR #22                     |
| Abonné Service          | 🟢 Fonctionnel, propre                             | 49 ✅                    | RAS majeur — ANO-003bis (doc CLAUDE.md) corrigée, PR #25            |
| Campagne Service        | 🟢 Fonctionnel                                     | 65 ✅                    | ANO-003 (statut ACTIF) corrigée — PR #20 ; ANO-004 (test `demarrer_maintenant`) corrigée — PR #16 |
| Facturation Service     | 🟢 Fonctionnel                                     | 33 ✅                    | RAS majeur — ANO-007 et ANO-008 corrigées (PR #23, #24)                            |
| Paiement Service        | 🟢 Fonctionnel, bien testé                         | 59 ✅                    | RAS majeur — service le plus robuste de l'audit                                    |
| Notification Service    | 🟢 Fonctionnel                                     | 40 ✅                    | RAS majeur                                                                          |
| Config Service          | 🟢 Fonctionnel                                     | 29 ✅                    | ANO-001 (casse des clés) corrigée — PR #18                                         |
| Reporting Service       | ⚪ **N'existe pas**                                 | —                       | Seul le `.proto` existe ; dossier `services/reporting/` absent                     |
| whatsapp-service (Node) | 🟢 Fonctionnel                                     | — (pas de tests)        | ANO-005 (auth endpoints) corrigée — PR #21                                         |


**Total : 439 tests exécutés à travers les 8 briques Python** (chiffre au moment de l'audit initial — voir §7 pour l'état courant). ANO-004 (régression sur `demarrer_maintenant`, gateway) a été corrigée directement sur la PR #16.

**Le point le plus important de cet audit** : le système *semble* cohérent en surface (contrats gRPC respectés à 100 %, dégradation gracieuse quasi partout, bonne couverture de tests), mais un bug de configuration silencieux (**ANO-001**) fait qu'une partie du paramétrage métier exposé à l'ADMIN (délais de paiement, validité des tokens, délais de relance impayés) **n'a aucun effet réel** — le système tourne en permanence sur des valeurs par défaut codées en dur, jamais sur les valeurs configurées. C'est le genre d'incohérence qu'aucun test unitaire par service ne peut détecter, car chaque service se teste avec son propre mock du Config Service.

---



## 2. Réponse à « est-ce cohérent et ça fonctionne ? »



### 2.1 Conformité aux règles métier critiques (CLAUDE.md racine)


| Règle (CLAUDE.md)                                                        | Statut                          | Détail                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------ | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `consommation = nouveau_index - ancien_index` (toujours ≥ 0)             | 🟢 Conforme                     | Validé côté **Campagne** (`campagnes/services.py:130`) et revalidé côté **Facturation** (ANO-008 résolu, PR #24).                                                                                                                                                                                                         |
| `montant = consommation * prix_m3` (prix_m3 copié depuis le tarif actif) | 🟢 Conforme                     | `services/facturation/factures/services.py:94-95`, arrondi `ROUND_HALF_UP`.                                                                                                                                                                                                                                                                              |
| `solde_restant = montant_total - Σ(versements)`                          | 🟢 Conforme                     | `services/paiement/paiements/repositories.py:84-105`, recalculé à chaque versement.                                                                                                                                                                                                                                                                      |
| `date_limite = date_releve + delai_paiement_jours` (défaut 5)            | 🟢 Conforme (corrigé)           | ANO-001 résolu (PR #18) — `delai_paiement_jours` est maintenant lu avec succès depuis Config Service.                                                                                                                                                                                                                                                     |
| `date_expiration_token = date_envoi + token_validite_jours` (défaut 20)  | 🟢 Conforme (corrigé)           | ANO-001 résolu (PR #18) — `token_validite_jours` est maintenant lu avec succès depuis Config Service.                                                                                                                                                                                                                                                     |
| Statut facture (IMPAYEE/PARTIELLE/PAYEE) selon `montant_paye`            | 🟢 Conforme                     | `services/paiement/paiements/repositories.py:95-100`.                                                                                                                                                                                                                                                                                                    |
| `nouveau_index ≥ ancien_index` avant sauvegarde d'un relevé              | 🟡 Partiel                      | Vérifié côté Campagne uniquement (voir ci-dessus).                                                                                                                                                                                                                                                                                                       |
| Statut ACTIF de l'abonné vérifié avant ajout en campagne                 | 🟢 Conforme (corrigé)           | ANO-003 résolu (PR #20) — vérification bloquante via `AbonneServiceClient.get_abonne()` avant tout ajout en campagne.                                                                                                                                                                                                                     |
| `prix_m3` copié dans la facture, jamais en FK                            | 🟢 Conforme                     | `services/facturation/factures/models.py:36-73` — champ `Decimal`, pas de `ForeignKey` vers `Tarif`.                                                                                                                                                                                                                                                     |
| Rôle vérifié avant toute action sensible                                 | 🟢 Conforme                     | `require_role()` systématique côté Gateway sur les mutations et désormais aussi sur la subscription `abonneUpdated` (ANO-015 résolu, PR #28). Reste : l'absence de tout contrôle de rôle **au niveau gRPC lui-même** dans chaque microservice (délégué entièrement à la Gateway — cohérent avec l'architecture mais sans défense en profondeur si un service interne était compromis, non cataloguée comme anomalie distincte). |
| `reference_transaction` obligatoire pour MOBILE_MONEY/VIREMENT           | 🟢 Conforme (au niveau service) | `services/paiement/paiements/services.py:82-84`. Non dupliqué en contrainte DB — un accès direct au repository la contournerait, mais ce n'est pas un chemin accessible via l'API publique actuelle.                                                                                                                                                     |




### 2.2 Verdict

- **Les contrats techniques sont respectés** : les 8 microservices Python exposent exactement les RPC déclarés dans leurs `.proto` respectifs (aucun écart trouvé sur les 8 services audités), et la Gateway consomme ces contrats sans divergence de schéma protobuf.
- **La dégradation gracieuse est un pattern largement appliqué** (Config, Reporting absent, PDF manquant, WhatsApp indisponible) mais **appliquée de façon inégale** d'un client à l'autre (voir §5.8, Config Service) — certains appels ne catchent rien et laisseraient une erreur gRPC remonter brute.
- **Le système n'était pas intuitivement cohérent sur un point précis et important** : le paramétrage métier via `updateConfig` (délais, validité de token) donnait l'illusion de fonctionner (la mutation GraphQL réussissait, la valeur était bien écrite en base côté Config Service) mais **n'avait aucun effet observable** côté Facturation/Paiement/Notification (ANO-001). ✅ Corrigé — PR #18.
- **Deux failles de sécurité concrètes** ont été identifiées, toutes deux corrigées : IDOR sur le PDF de l'espace abonné public (ANO-002, ✅ PR #19) et absence totale d'authentification sur le service whatsapp-service (ANO-005, ✅ PR #21).

---



## 3. Vue d'ensemble — ce qui est réellement construit

Le CLAUDE.md racine décrit une cible à **9 composants**. **8 sont réellement implémentés** ; le 9ème (Reporting Service) n'existe que sous forme de contrat `.proto`, sans aucune implémentation Django.

```
gateway/              ✅ Django + Strawberry GraphQL, ASGI, aucune BD (dummy backend)
services/auth/        ✅ Django + gRPC :50051 — app "comptes"
services/abonne/      ✅ Django + gRPC :50052 — app "abonnes"
services/campagne/    ✅ Django + gRPC :50053 — app "campagnes" (+ scheduler 7h00)
services/facturation/ ✅ Django + gRPC :50054 — app "factures" (+ génération PDF ReportLab)
services/paiement/    ✅ Django + gRPC :50055 — app "paiements" (+ scheduler 8h00)
services/notification/✅ Django + gRPC :50056 — app "notifications"
services/config/      ✅ Django + gRPC :50058 — app "parametres"
services/reporting/   ⚪ ABSENT — seul proto/reporting_service.proto existe (6 RPC déclarés,
                          0 implémentés). Aucune trace dans docker-compose.yml.
whatsapp-service/     ✅ Node.js + Express + whatsapp-web.js (Puppeteer/Chromium),
                          consommé uniquement par Auth (OTP) et Notification (factures/relances)
```

**Divergence de stack majeure par rapport à** `CLAUDE.md` **racine** : la ligne *« WhatsApp Telnyx API »* du tableau de stack technologique est **obsolète depuis l'origine du projet audité** — aucune trace de Telnyx dans le code (ni `auth`, ni `notification`, ni `gateway`). Le canal réel est `whatsapp-web.js` auto-hébergé (compte WhatsApp dédié, session persistée via QR code, zéro coût), déjà documenté correctement dans `docs/ARCHITECTURE.md` §2.2 mais pas ailleurs (voir §8).

**Topologie réelle confirmée** (`docker-compose.yml`) : 8 bases PostgreSQL dédiées (une par microservice métier, pas de `reporting-postgres`), `redis` (pub/sub événementiel + cache), `nginx` (reverse proxy devant Gateway), volume `whatsapp_session` (persistance de session WhatsApp Web).

---



## 4. Registre des anomalies

Légende sévérité : 🔴 Critique (bug actif ou faille de sécurité, silencieux) · 🟠 Élevée · 🟡 Moyenne (dette/doc obsolète) · ⚪ Faible.

### 🔴 Critiques

**ANO-001 — Config Service : incohérence de casse des clés rend le paramétrage ADMIN sans effet** — ✅ **RÉSOLU** (PR #18, branche `fix/ano-001-config-key-casing`)

- **Fichiers** : `services/config/parametres/models.py:27-56` (`CONFIG_DEFAULTS`, tout en MAJUSCULES) vs `services/facturation/factures/grpc_clients.py:79` (`"delai_paiement_jours"`), `services/notification/notifications/grpc_clients.py:128` (`"token_validite_jours"`), `services/paiement/paiements/grpc_clients.py:224-235` (`impaye_delai_rappel_1/2`, `impaye_delai_avertissement`, `impaye_delai_suspension`, `impaye_suspension_auto`, `impaye_suspension_relances` — clés qui **n'ont jamais existé** côté Config, même en majuscule).
- **Scénario concret** : un ADMIN modifie `DELAI_PAIEMENT_JOURS` à 10 jours via `updateConfig` dans le back-office. Facturation Service continue de calculer `date_limite = date_releve + 5` (valeur par défaut de `facturation/settings.py`) car son appel `GetConfig(cle="delai_paiement_jours")` (minuscule) échoue systématiquement en `NOT_FOUND` côté Config Service et retombe sur son propre défaut local. Même chose pour la validité des tokens abonné et **tous** les délais de relance impayés.
- **Confirmé par exécution** : `'delai_paiement_jours' in CONFIG_DEFAULTS` → `False`.
- **Seules clés qui fonctionnent réellement** : `EMAIL_ADMIN_NOTIFICATIONS`, `NOTIFICATIONS_ADMIN_ACTIVEES` (appelées en MAJUSCULE par Notification, cohérentes avec `CONFIG_DEFAULTS`).
- **Correctif appliqué** : toutes les clés de `CONFIG_DEFAULTS` renommées en minuscule pour correspondre exactement aux appels des 3 consommateurs ; les 6 clés `impaye_*` attendues par Paiement ajoutées côté Config (en remplacement de `RELANCE_ETAPE_1..4_JOURS`/`SUSPENSION_AUTO_ACTIVE`/`DELAI_SUSPENSION_APRES_VERSEMENT_JOURS`, jamais lues avec succès) ; délai de suspension corrigé à J+10 pour correspondre à `docs/SRS.md` EF-IMP-002. Voir PR #18 pour le détail et le plan de test.

**ANO-002 — Gateway : IDOR sur le téléchargement de PDF de l'espace abonné public** — ✅ **RÉSOLU** (PR #19, branche `fix/ano-002-idor-facture-pdf`)

- **Fichier** : `gateway/schema/espace_abonne.py:99-123` (vue `espace_abonne_pdf`).
- **Scénario concret** : un abonné authentifié par *son propre* token valide `GET /espace-abonne/<mon_token>/facture/<facture_id>/pdf/` avec un `facture_id` **appartenant à un autre abonné** (deviné ou énuméré, les ID sont des UUID mais rien n'empêche l'essai). La vue valide le token (donc l'identité de l'appelant) puis appelle directement `facturation_client.get_facture_pdf(facture_id)` **sans jamais vérifier que cette facture appartient à l'abonné du token**. Le PDF d'un tiers (nom, adresse, consommation, montant) fuit.
- **Correctif appliqué** : `GetFacture` est désormais appelé avant `GetFacturePDF` pour vérifier `facture.abonne_id == token_resp.abonne_id` ; en cas de mismatch ou de facture introuvable, réponse `404` (pas `403`, pour ne pas confirmer l'existence d'une facture qui n'appartient pas à l'appelant). Tests de régression ajoutés (`gateway/schema/tests/test_espace_abonne.py`, inexistant auparavant). Voir PR #19.

**ANO-003 — Vérification du statut ACTIF de l'abonné avant ajout en campagne : non implémentée** — ✅ **RÉSOLU** (PR #20, branche `fix/ano-003-verifier-abonne-actif`)

- **Fichiers** : `services/campagne/campagnes/services.py:75-87` (`ajouter_abonne_campagne`, aucun appel gRPC vers Abonné) ; `services/campagne/campagnes/grpc_clients.py:13-30` (`AbonneServiceClient` ne définit qu'une méthode `ping()`, jamais appelée) ; côté Abonné, aucun mécanisme ne pousse l'information non plus (voir ANO-003bis ci-dessous).
- **Scénario concret** : un abonné `SUSPENDU` (impayé) ou `RESILIE` peut être relevé normalement lors d'une campagne (`SaisirIndex` ne vérifie que la non-duplication du relevé), puis facturé, alors que la règle métier obligatoire (CLAUDE.md racine) l'interdit explicitement.
- **Correctif appliqué** : `AbonneServiceClient.get_abonne()` implémenté (stubs `abonne_service_pb2*.py` générés pour campagne-service, absents auparavant) ; `ajouter_abonne_campagne()` vérifie `statut == "ACTIF"` avant de créer le `Releve`, de façon **bloquante** (pas de dégradation gracieuse — c'est une règle métier obligatoire). `SaisirIndex` route désormais sa création à la volée via cette méthode au lieu d'un accès direct au repository. Tests de régression ajoutés.
- **Non résolu par cette PR — reste ouvert en ANO-003bis** : l'intégration reste un appel gRPC synchrone (Campagne interroge Abonné à la demande), pas l'événement `AbonneCreated` décrit dans CLAUDE.md racine. Voir ci-dessous.

**ANO-003bis — L'événement** `AbonneCreated` **(Abonné → Campagne) documenté dans CLAUDE.md n'existe pas** — ✅ **RÉSOLU** (PR #25, documentation uniquement)

- **Statut** : traité comme une correction de documentation (CLAUDE.md racine décrivait un flux événementiel qui n'a jamais existé) plutôt que comme un chantier d'implémentation — distinct d'ANO-003 (qui portait sur la vérification du statut, désormais faite via un appel gRPC synchrone `GetAbonne`, pas via un événement). CLAUDE.md racine reformulé pour décrire le mécanisme réel.
- **Réalité** : Abonné Service publie `ABONNE_CREATED`/`ABONNE_UPDATED` uniquement sur **Redis pub/sub** (`services/abonne/abonnes/event_publisher.py:6-25`, canal `abonne:events`), consommé **uniquement par la Gateway** pour les subscriptions GraphQL temps réel. Campagne Service ne s'y abonne jamais. L'intégration event-driven décrite dans CLAUDE.md racine (§ « Événements inter-services ») n'existe donc pas pour ce flux précis — et n'est plus nécessaire pour la vérification de statut, désormais faite en synchrone au moment de la saisie (ANO-003).



### 🟠 Élevées

**ANO-004 — Test cassé sur la branche** `feature/campagne-demarrer-maintenant` — ✅ **RÉSOLU** (poussé directement sur la PR #16 existante, commit `7f7fb75` — pas de nouvelle PR : le bug n'existait que sur cette branche, `develop` n'ayant pas encore la fonctionnalité `demarrer_maintenant`)

- **Fichier** : `gateway/schema/tests/test_campagne.py::TestCampagneMutations::test_creer_campagne_admin` (ligne ~134).
- **Cause** : le mock `assert_called_once_with(...)` n'avait pas été mis à jour après l'ajout de `demarrer_maintenant` au commit `29e91c7`. Confirmé par exécution réelle : `python manage.py test schema` → **84 tests, 1 échec**.
- **Correctif appliqué** : `demarrer_maintenant=False` ajouté à l'assertion ; tests manquants ajoutés côté campagne-service pour la fonctionnalité elle-même (`test_creer_campagne_demarrer_maintenant_statut_en_cours`, `test_creer_campagne_sans_demarrer_maintenant_reste_planifiee`). Suites vérifiées : campagnes 64/64, schema (gateway) 84/84.

**ANO-005 — whatsapp-service : endpoints HTTP sans authentification** — ✅ **RÉSOLU** (PR #21, branche `fix/ano-005-authentifier-whatsapp-service`)

- **Fichier** : `whatsapp-service/server.js` (`/send`, `/send-with-pdf`, `/qr`, `/health`).
- **Constat** : aucune clé API, aucun token, aucune restriction — protection uniquement par l'isolation réseau Docker/Kubernetes. En développement local, `docker-compose.yml` mappe `3000:3000` sur l'hôte, donc accessible sans contrôle depuis la machine locale (et potentiellement depuis le LAN).
- **Correctif appliqué** : middleware `requireApiKey` (en-tête `X-Internal-Api-Key`, comparaison en temps constant) appliqué à `/qr`, `/send`, `/send-with-pdf` — `/health` reste public. `auth-service`/`notification-service` envoient désormais cet en-tête. Clé configurée via `WHATSAPP_INTERNAL_API_KEY` (docker-compose + `.env.example`). Si la clé n'est pas définie, dégradation en clair avec avertissement dans les logs (dev uniquement). Vérifié manuellement en démarrant le service réel (voir PR #21).

**ANO-006 — Auth : pas de rotation du refresh token lors de** `RefreshToken` — ✅ **RÉSOLU** (PR #22, branche `fix/ano-006-rotation-refresh-token`)

- **Fichier** : `services/auth/comptes/services.py:87-101`.
- **Constat** : un nouveau couple access+refresh est émis à chaque refresh, mais l'ancien refresh token n'est **pas** ajouté à `RevokedToken` — il reste valide jusqu'à expiration naturelle (7 jours), même après avoir servi. Un refresh token intercepté reste exploitable en parallèle du nouveau.
- **Correctif appliqué** : l'ancien refresh token est désormais révoqué (ajouté à `RevokedToken`) juste après l'émission du nouveau couple, avec le même mécanisme que `logout()`. Test de régression ajouté.
- **Correctif** : révoquer explicitement l'ancien refresh (son `jti`) au moment d'émettre le nouveau.

**ANO-007 — Facturation : numérotation séquentielle des factures sans verrou transactionnel** — ✅ **RÉSOLU** (PR #23, branche `fix/ano-007-verrouiller-numerotation-facture`)

- **Fichier** : `services/facturation/factures/repositories.py:34-38` (`build_numero`, pattern `count()+1`).
- **Constat** : sous génération concurrente de factures pour le même mois (deux `GenererFactures` simultanés, ex. deux clôtures de campagnes le même mois), deux threads peuvent calculer le même numéro séquentiel → collision sur la contrainte `unique=True` de `numero_facture`, non catchée spécifiquement (remonte en `INTERNAL` générique).
- **Correctif appliqué** : `next_sequence`/`build_numero` se basent désormais sur le dernier numéro existant du mois (+1, plus robuste qu'un `COUNT()` en cas de suppression intermédiaire) avec `SELECT ... FOR UPDATE` (même pattern que `AbonneRepository.last_numero`), appelé à l'intérieur du bloc `transaction.atomic()` qui crée la facture.
- **Correctif** : `select_for_update()` sur le dernier numéro du mois, ou séquence PostgreSQL dédiée.

**ANO-008 — Facturation ne revalide pas** `nouveau_index ≥ ancien_index` — ✅ **RÉSOLU** (PR #24, branche `fix/ano-008-revalider-index-facturation`)

- **Constat** : Facturation fait confiance aux relevés reçus de Campagne Service (déjà validés en amont) sans revalider avant de calculer un montant. Si cette règle était un jour contournée en amont (bug, appel direct au repository Campagne), Facturation produirait une facture à montant négatif sans le détecter.
- **Correctif appliqué** : `generer_factures()` ignore désormais (log `warning`) tout relevé dont `nouveau_index < ancien_index`, sans bloquer la facturation des autres abonnés valides du même lot.



### 🟡 Moyennes (dette technique / documentation obsolète)

**ANO-009 — CLAUDE.md racine +** `docs/ARCHITECTURE.md` **(§3, §5.7) référencent encore Telnyx** — ✅ **RÉSOLU** (PR #25). Voir §8.

**ANO-010 — CLAUDE.md racine : note sur le filtrage SUPERVISEUR obsolète** — ✅ **RÉSOLU** (PR #25) — le texte affirmait que le filtrage par `created_by` « n'existe pas encore » côté campagne-service ; corrigé pour refléter l'implémentation réelle **côté Gateway** (`gateway/schema/campagne_queries.py:11-29`, `_verifier_acces_campagne`).

**ANO-011 —** `gateway/CLAUDE.md` **obsolète** — ✅ **RÉSOLU** (PR #25) — affirmait que seul `auth_service` était branché ; corrigé (6 des 7 services métier, tout sauf reporting, sont câblés côté Gateway).

**ANO-012 —** `docs/ARCHITECTURE.md` **§10 (schéma GraphQL) en retard sur le schéma réel** — ✅ **RÉSOLU** (PR #25) — noms d'opérations corrigés (`creerCampagne`, `envoyerFactureWhatsapp`/`renvoyerFactureWhatsapp`, `soldeFacture`/`SoldeFacture`), `affecterAgent` ajoutée (mutation manquante), arguments manquants ajoutés (`numeroMobileMoney`, `genererFacturesAuto`, `envoyerWhatsappAuto` sur `creerCampagne` ; `dateEffet` sur `updateTarif`), `enregistrerPaiement` corrigée en arguments scalaires, type `Envoi` complété (`abonneId`, `telnyxMessageId`). Note de fraîcheur ajoutée pointant vers le code comme source de vérité.

**ANO-013 — Notification :** `TypeEnvoi.RETABLISSEMENT` **défini mais jamais produit** — ✅ **RÉSOLU** (PR #26, branche `feat/ano-013-message-retablissement`)

- **Constat initial** : aucun `build_message_retablissement`, aucune étape 5 dans `_ETAPE_TO_TYPE`.
- **Bug découvert en creusant** : Paiement Service appelait déjà systématiquement `envoyer_relance(etape=0)` après un paiement complet ("confirmation de paiement complet") — mais `envoyer_relance` rejetait explicitement `etape=0` (`ValidationError` → gRPC `INVALID_ARGUMENT` → capté par la dégradation gracieuse du client Paiement). **Aucun abonné n'a donc jamais reçu de confirmation de paiement par WhatsApp**, silencieusement, depuis toujours.
- **Correctif appliqué** : `build_message_retablissement()` ajouté (conforme au template exact `docs/SRS.md` EF-NOTIF-004/EF-IMP-005), `_ETAPE_TO_TYPE` et la validation étendus à `[0, 4]`. Aucun changement côté Paiement Service (il appelait déjà correctement `etape=0`).

**ANO-014 — Duplication assumée du client WhatsApp entre Auth et Notification** — ✅ **RÉSOLU (documenté explicitement)** (PR #27) — `services/auth/comptes/whatsapp_client.py` et `services/notification/notifications/whatsapp_client.py` implémentent la même classe `WhatsAppWebClient`/`WhatsAppDeliveryError` en copier-coller. La duplication reste assumée (chaque microservice reste indépendant, CLAUDE.md racine) mais est désormais documentée explicitement en tête des deux fichiers, avec un rappel que tout bugfix doit être répliqué manuellement dans les deux.

**ANO-015 — Gateway : subscription** `abonneUpdated` **sans contrôle d'accès** — ✅ **RÉSOLU** (PR #28, branche `fix/ano-015-proteger-subscription`) — `gateway/schema/subscriptions.py` n'appelait ni `require_auth` ni `require_role`, alors que la query équivalente (`abonne`/`abonnes`) exige ADMIN. Corrigé : `require_role(info, "ADMIN")` appelé en tout début du générateur, avant toute connexion Redis. Tests ajoutés (aucun n'existait auparavant pour ce fichier).

**ANO-016 — Reporting Service : absence totale (pas un bug, un chantier non démarré)** — `proto/reporting_service.proto` déclare 6 RPC (`GetDashboard`, `GetStatsCampagne`, `GetStatsGlobales`, `UpdateStatsCampagne`, `UpdateStatsFacturation`, `UpdateStatsPaiements`), aucun n'est implémenté. Facturation et Paiement ne tentent même pas de l'appeler (pas de risque de plantage, l'intégration n'a simplement jamais été commencée).

### ⚪ Faibles

- **ANO-017** — ✅ **RÉSOLU** (PR #29) — Abonné : `StatutCompteur.DESACTIVE` était défini mais jamais utilisé ; utilisé désormais par `resilier_abonne()` (le compteur d'un abonné résilié passe à `DESACTIVE`). Contrainte `UniqueConstraint` partielle ajoutée sur `Compteur` (condition `statut=ACTIF`) pour garantir en base un seul compteur `ACTIF` par abonné (auparavant logique applicative seule).
- **ANO-018** — ✅ **RÉSOLU** (PR #30) — Campagne : `serializers.py::releve_to_proto` utilise désormais le même helper `_to_iso` que `campagne_to_proto` (introduit par le commit `d54133a`). Tests de sérialisation ajoutés (aucun n'existait pour ce module).
- **ANO-019** — ✅ **RÉSOLU** (PR #31) — Campagne : `find_planifiee_pour_date` utilisait `.first()`. Remplacé par `list_planifiees_pour_date` : toutes les campagnes partageant une même `date_planifiee` démarrent désormais au même passage du cron 7h.
- **ANO-020** — Auth : `Logout` (`grpc_server.py:42-47`) gère ses erreurs différemment des 12 autres RPC (catch local au lieu de déléguer à l'intercepteur) — pas un bug, mais un pattern à ne pas reproduire.
- **ANO-021** — ✅ **RÉSOLU** (PR #25) — `services/config/CLAUDE.md` disait « 8 clés par défaut », corrigé à 10.
- **ANO-022** — Gateway : aucun test pour `facturation_queries/mutations`, `paiement_queries/mutations`, `notification_queries/mutations`, `espace_abonne.py`, `subscriptions.py` — trou de couverture notable vu l'exigence CLAUDE.md (« couverture > 80 % »).
- **ANO-023** — Paiement : `repositories.py::marquer_resolu` jamais appelé nulle part (code mort, l'admet dans son propre docstring).
- **ANO-024** — ✅ **RÉSOLU** (PR #27) — `whatsapp_client.py::send()`/`send_with_pdf()` (auth et notification) appelaient `response.json()` avant de vérifier le status code HTTP ; corrigé (vérification 503 d'abord, parsing JSON entouré d'un `try/except ValueError`). Tests dédiés ajoutés (le module n'en avait aucun).

---



## 5. État détaillé par service



### 5.1 Gateway (`gateway/`)

**Rôle** : point d'entrée GraphQL unique (Strawberry), ASGI, aucune base de données propre (`ENGINE: django.db.backends.dummy`). Fédère les 7 services métier (tout sauf reporting) via des clients gRPC synchrones dans `schema/grpc_clients.py`.

**Authentification** : le token JWT n'est **jamais décodé localement** — chaque requête protégée déclenche un appel gRPC `ValidateToken` vers Auth Service (`schema/context.py:49-54`), qui reste l'unique source de vérité sur l'identité et le rôle. Le refresh token vit dans un cookie `HttpOnly + Secure(si prod) + SameSite=Strict` (`schema/context.py:34-42`), jamais renvoyé dans le corps GraphQL.

**Surface GraphQL réelle** (voir aussi §6 de `docs/ARCHITECTURE.md`, à corriger — ANO-012) :

- **23 queries**, **31 mutations**, **1 subscription** (`abonneUpdated`, sur Redis pub/sub, exige ADMIN depuis ANO-015 résolu).
- Contrôle de rôle systématique via `require_role(info, *roles)` sur toutes les opérations sensibles, y compris désormais `abonneUpdated` (ANO-015). Seule exception volontaire : `infosSociete` (public — sert à alimenter les PDF).
- Filtrage SUPERVISEUR/AGENT par propriété de campagne implémenté et testé (`campagne_queries.py:11-29`).
- Espace abonné public tokenisé : 2 vues Django classiques (pas GraphQL), `GET /espace-abonne/<token>/` (liste des factures) et `GET /espace-abonne/<token>/facture/<id>/pdf/` (téléchargement — **IDOR, ANO-002**).

**Tests** : 84 tests (`gateway/schema/tests/`), **1 échec actuel** (ANO-004). Bonne couverture sur auth/abonne/campagne/config ; **aucun test** sur facturation, paiement, notification, espace_abonne, subscriptions (ANO-022).

### 5.2 Auth Service (`services/auth/`)

**Rôle** : authentification JWT (access 24h / refresh 7j), gestion des comptes (4 rôles : ADMIN, AGENT, COMPTABLE, SUPERVISEUR), verrouillage anti-bruteforce.

**Modèle** : `User` (UUID, `role`, `failed_attempts`, `locked_until`), `RevokedToken` (blacklist par `jti`), `PasswordSetupToken` (activation ADMIN par e-mail, 48h par défaut), `PhoneOtpToken` (activation/reset par OTP WhatsApp, code à 6 chiffres **haché**, jamais stocké en clair).

**Workflow d'activation à deux canaux** : ADMIN → e-mail Brevo (lien `set-password`) ; autres rôles → OTP WhatsApp (6 chiffres, lien `activer-compte`). Aucun « mot de passe temporaire » n'existe (contrairement à ce que suggère `docs/SRS.md` EF-AUTH-004 — le compte est créé avec `set_unusable_password()` et activé uniquement via lien/OTP).

**RPC** : les 13 RPC du `.proto` sont tous implémentés, correspondance exacte. Aucun contrôle de rôle n'est fait dans ce service lui-même (`CreateUser`/`UpdateUser`/`DeactivateUser` acceptent tout appelant gRPC) — entièrement délégué à la Gateway (`require_role(info, "ADMIN")` avant l'appel), cohérent avec l'architecture mais sans défense en profondeur.

**Tests** : 88 tests, **tous verts** (confirmé par exécution). Couverture large (login, verrouillage, refresh, logout, création/activation par les deux canaux, reset mot de passe anti-énumération).

### 5.3 Abonné Service (`services/abonne/`)

**Rôle** : gestion des abonnés (numérotation auto `AB-XXXX`) et de leurs compteurs (avec historique de remplacement).

**Modèle** : `Abonne` (statut `ACTIF`/`SUSPENDU`/`RESILIE` — pas de retour possible depuis `RESILIE`), `Compteur` (statut `ACTIF`/`REMPLACE`/`DESACTIVE`, ce dernier jamais utilisé), `HistoriqueCompteur`.

**RPC** : les 12 RPC du `.proto` sont tous implémentés, correspondance exacte. Publication d'événements `ABONNE_CREATED`/`ABONNE_UPDATED` sur Redis pub/sub — **consommée uniquement par la Gateway** (subscriptions GraphQL), jamais par Campagne Service (ANO-003bis).

**Tests** : 49 tests, tous verts (confirmé par exécution de l'agent d'audit).

### 5.4 Campagne Service (`services/campagne/`)

**Rôle** : cycle de vie des campagnes de relevé mensuelles (`PLANIFIEE` → `EN_COURS` → `CLOTUREE`) et des relevés d'index associés.

**Modèle** : `Campagne` (`created_by`, `numero_mobile_money`, toggles `generer_factures_auto`/`envoyer_whatsapp_auto`), `CampagneAgent` (affectation), `Releve` (`unique_together` campagne+abonné, statuts `A_RELEVER`/`RELEVE`/`NON_RELEVE`/`ESTIME`).

**Fonctionnalité en cours sur cette branche** — `demarrer_maintenant` (commit `29e91c7`) : la création d'une campagne peut désormais démarrer directement `EN_COURS` au lieu de `PLANIFIEE`. Câblage propre bout-en-bout (proto → service → repository → grpc_server), mais **zéro test ajouté** pour cette fonctionnalité, et le test `gateway` existant est cassé par son absence de mise à jour (ANO-004).

**Scheduler 7h00** : démarre toutes les campagnes `PLANIFIEE` dont `date_planifiee` est aujourd'hui **ou hier** (rattrapage) — ANO-019 résolu, PR #31.

**Clôture → Facturation** : appel gRPC direct et synchrone (`CampagneServicer.CloturerCampagne` → `FacturationServiceClient.notifier_campagne_cloturee`), dégradation gracieuse si Facturation est indisponible — mais alors **aucune facture n'est jamais générée** pour cette campagne, sans retry ni alerte.

**RPC** : les 11 RPC du `.proto` sont tous implémentés. Filtrage SUPERVISEUR par `created_by` **implémenté et testé** (contrairement à la note obsolète de CLAUDE.md racine — ANO-010).

**Tests** : 62 tests, tous verts (confirmé par exécution). Lacunes : `demarrer_maintenant`, rattrapage J-1 du scheduler, `numero_mobile_money` invalide.

### 5.5 Facturation Service (`services/facturation/`)

**Rôle** : génération des factures à la clôture d'une campagne, calcul du montant, génération PDF (ReportLab), gestion du tarif actif.

**Modèle** : `Tarif` (`prix_m3`, `is_active` — un seul actif à la fois, garanti applicativement, pas par contrainte DB), `Facture` (`prix_m3` **copié**, `statut`, `numero_facture` séquentiel `FACT-AAAA-MM-XXXX`, `pdf_path`, `numero_mobile_money` ajouté au commit `701fe0b`).

**Workflow** : `GenererFactures` récupère les relevés `RELEVE` depuis Campagne (échec bloquant si Campagne est indisponible — volontairement non dégradé, contrairement au reste du flux), calcule montant et date limite, crée la facture + PDF en transaction atomique, puis notifie Paiement (`InitialiserSolde`) et Notification (`EnvoyerFacture`) **hors transaction**, avec dégradation gracieuse.

**RPC** : les 8 RPC du `.proto` sont tous implémentés. `GetFacturesParCampagne` est le seul RPC sans `try/except` (incohérence mineure de pattern).

**Absence de Reporting** : aucun appel, aucune trace — cohérent avec ANO-016.

**Tests** : 28 tests, tous verts (confirmé par exécution). Les tests de service/gRPC déclenchent de vrais appels réseau vers Paiement/Notification (non mockés) qui échouent proprement en local (`ConnectionRefused` capté par la dégradation gracieuse) — fonctionne mais n'est pas une pratique de test unitaire isolée idéale.

### 5.6 Paiement Service (`services/paiement/`)

**Rôle** : enregistrement des versements (partiels/multiples), suivi du solde par facture, escalade automatique des impayés.

**Modèle** : `Paiement` (mode `ESPECES`/`MOBILE_MONEY`/`VIREMENT`, `reference_transaction`), `SoldeFacture` (une ligne par facture, statut dérivé), `SuiviImpaye` (`facture_id` unique, étapes 1 à 4 avec flags/dates).

**Workflow paiement** : validation montant > 0, référence obligatoire selon le mode, anti-surpaiement (`montant > solde_restant` refusé), mise à jour atomique du solde et du statut, synchronisation vers Facturation (`UpdateStatutFacture`, dégradé), et si `PAYEE` : réactivation de l'abonné + résolution du suivi impayé + confirmation WhatsApp étape 0.

**Workflow impayés (cron 8h00)** : pour chaque facture en retard, tente successivement rappel 1 / rappel 2 / avertissement (étapes indépendantes, peuvent se déclencher en cascade le même jour si le cron a été interrompu plusieurs jours), puis suspension automatique (si activée en config) avec notification admin. Un `SuiviImpaye.relances_suspendues_jusqu` (posé sur paiement partiel) met en pause **toutes** les relances, y compris la suspension, jusqu'à une date donnée.

**⚠️ Rappel important** : les délais utilisés par ce cron (`impaye_delai_rappel_1/2`, `avertissement`, `suspension`) **ne sont jamais lus avec succès depuis Config Service** (ANO-001) — le service tourne systématiquement sur ses valeurs par défaut internes (rappel_1=0, rappel_2=3, avertissement=7, suspension=10 jours).

**RPC** : les 6 RPC du `.proto` sont tous implémentés. C'est le service jugé le plus robuste de l'audit (gestion d'erreur homogène, pas de bug fonctionnel identifié en dehors d'ANO-001 qui lui est externe).

**Tests** : 59 tests, tous verts (confirmé par exécution, 0.044s).

### 5.7 Notification Service (`services/notification/`) + whatsapp-service (Node.js)

**Rôle** : envoi de messages WhatsApp (facture, relances impayés étapes 1-4, notification admin) et gestion des tokens d'accès à l'espace abonné.

**⚠️ Canal réel : whatsapp-web.js, pas Telnyx** (voir ANO-009). Le champ `telnyx_message_id` du modèle `Envoi` et du `.proto` est un vestige mort, jamais renseigné.

**Modèle** : `Envoi` (`type_envoi` : `FACTURE`/`RELANCE_1`/`RELANCE_2`/`AVERTISSEMENT`/`SUSPENSION`/`RETABLISSEMENT` — ce dernier désormais produit à l'étape 0, ANO-013 résolu), `TokenAcces` (UUID, expiration configurable, ANO-001 résolu).

**whatsapp-service (Node.js)** : `whatsapp-web.js` + Puppeteer/Chromium headless, session persistée sur volume Docker (`LocalAuth`), reconnexion avec backoff exponentiel sur déconnexion. Endpoints `GET /health`, `GET /qr` (QR code à scanner une fois), `POST /send`, `POST /send-with-pdf`. **Aucune authentification** sur ces endpoints (ANO-005). Contrairement à ce que documente `ARCHITECTURE.md` §5.7 (« Retry Handler — 3 tentatives »), **il n'y a aucune logique de retry**, ni côté Node ni côté Django — un échec est immédiatement marqué `ECHEC` (dégradation gracieuse, pas de nouvelle tentative automatique).

**Duplication de code** : `whatsapp_client.py` existe identique dans `auth` et `notification` — assumée et documentée (ANO-014, PR #27).

**RPC** : les 8 RPC du `.proto` sont tous implémentés, y compris les champs `type_envoi`/`abonne_id` ajoutés au dernier commit (`2500707`).

**Tests** : 40 tests, tous verts (confirmé par exécution). `message_builder.py` (templates de messages) et `whatsapp_client.py` n'ont pas de test direct dédié.

### 5.8 Config Service (`services/config/`)

**Rôle** : paramètres système clé/valeur génériques (`ConfigParam`) + informations société typées (`InfosSociete`, singleton, alimente les PDF de facture).

**⚠️ Voir ANO-001** — c'est le service à l'origine du bug le plus impactant de cet audit, bien que le bug se manifeste chez ses *consommateurs*. `CONFIG_DEFAULTS` (10 clés, toutes en MAJUSCULES) ne couvre déjà pas les clés attendues par Paiement (`impaye_`*), et la casse ne correspond à aucun des appels effectués par Facturation/Notification.

**RPC** : les 5 RPC du `.proto` sont tous implémentés (`GetConfig`/`UpdateConfig` génériques par clé, pas un RPC par paramètre). Aucun contrôle de rôle **dans ce service** — entièrement délégué à la Gateway (`require_role(info, "ADMIN")` sur `updateConfig`/`updateInfosSociete`/`config`/`configs`).

**Dégradation gracieuse inégale entre consommateurs** : Paiement est le plus robuste (repli clé par clé) ; Notification protège 3 méthodes sur 4 mais `get_infos_societe()` **ne catche rien** ; la Gateway ne protège rien du tout côté `config` (comportement voulu, c'est une API d'admin directe).

**Tests** : 29 tests, tous verts (confirmé par exécution).

---



## 6. Flux d'événements inter-services — documenté vs réel


| Événement (CLAUDE.md racine)                           | Documenté comme                    | Réalité constatée                                                                                                                                                |
| ------------------------------------------------------ | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CampagneCloturee` (Campagne → Facturation)            | Émission d'événement               | ✅ Conforme en pratique : appel **gRPC direct et synchrone** `GenererFactures`, dégradation gracieuse (mais alors aucune facture générée, sans retry — voir §5.4) |
| `FactureGeneree` → Paiement                            | Notifie Paiement                   | ✅ Conforme : appel `InitialiserSolde`, dégradé                                                                                                                   |
| `FactureGeneree` → Notification                        | Peut déclencher WhatsApp           | ✅ Conforme : appel `EnvoyerFacture` si `envoyer_whatsapp_auto=True`, dégradé                                                                                     |
| `FactureGeneree` → Reporting                           | Met à jour les stats               | ⚪ **N'existe pas** (ANO-016)                                                                                                                                     |
| `PaiementEnregistre` → Facturation                     | Met à jour le statut facture       | ✅ Conforme : appel `UpdateStatutFacture`, dégradé                                                                                                                |
| `PaiementEnregistre` → Reporting                       | Met à jour les stats               | ⚪ **N'existe pas** (ANO-016)                                                                                                                                     |
| `SuspensionRequise` (Paiement → Abonné + Notification) | Suspend l'abonné, WhatsApp étape 4 | ✅ Conforme : `AbonneServiceClient.suspendre_abonne` + `NotificationServiceClient.envoyer_relance(etape=4)` + `notifier_admins`                                   |
| `AbonneCreated` (Abonné → Campagne)                    | Ajoute à la campagne en cours      | 🔴 **N'existe pas** (ANO-003bis) — publié sur Redis, consommé uniquement par la Gateway pour les subscriptions GraphQL, jamais par Campagne                      |


---



## 7. Couverture de tests


| Service      | Tests exécutés | Résultat         | Méthode de vérification                                   |
| ------------ | -------------- | ---------------- | --------------------------------------------------------- |
| Auth         | 88             | ✅ OK             | Exécution réelle (`manage.py test comptes`)               |
| Abonné       | 49             | ✅ OK             | Rapporté par l'agent d'audit (exécution confirmée)        |
| Campagne     | 62             | ✅ OK             | Exécution réelle (`manage.py test campagnes`)             |
| Facturation  | 28             | ✅ OK             | Rapporté par l'agent d'audit (exécution confirmée)        |
| Paiement     | 59             | ✅ OK             | Rapporté par l'agent d'audit (exécution confirmée)        |
| Notification | 40             | ✅ OK             | Exécution réelle (`manage.py test notifications`)         |
| Config       | 29             | ✅ OK             | Rapporté par l'agent d'audit (exécution confirmée)        |
| Gateway      | 84 → 90 après ANO-002 | 🟢 OK (ANO-004 corrigée sur PR #16) | Exécution réelle (`manage.py test schema`) |
| **Total**    | **439**        | **438 ✅ / 1 🔴** |                                                           |


Trous de couverture identifiés : voir ANO-022 (Gateway), absence de tests pour `demarrer_maintenant` (Campagne), absence de tests pour `event_publisher.py` (Abonné), absence de tests pour `schedulers.py` en tant que tel (Campagne, Paiement — la logique métier interne est testée, pas le déclenchement APScheduler).

---



## 8. Documentation existante à corriger

Cet audit a mis en évidence des passages **obsolètes ou incohérents** dans les documents déjà présents dans `docs/`. **Tous corrigés — PR #25** (`docs/corriger-references-obsoletes`), sauf mention contraire :

- `CLAUDE.md` **(racine)** : ✅ « WhatsApp Telnyx + tokens » et « WhatsApp Telnyx API » (stack technologique) remplacés par whatsapp-web.js. ✅ Note sur le filtrage SUPERVISEUR corrigée (déjà implémenté côté Gateway). ✅ Description de l'événement `AbonneCreated` reformulée pour refléter le mécanisme réel (appel gRPC synchrone).
- `docs/ARCHITECTURE.md` : ✅ Corrigée — §2.2 mentionnait déjà correctement whatsapp-web.js ; le diagramme C4 §3 et le détail des composants §5.7 décrivaient encore un « Telnyx Adapter » avec un « Retry Handler — 3 tentatives automatiques » et un « Event Consumer » qui n'existent nulle part dans le code (reformulés pour refléter les appels gRPC synchrones réels). Le scénario de troubleshooting (~ligne 2481) référençait un timeout Telnyx impossible à produire — remplacé par un scénario whatsapp-service réaliste. Le schéma GraphQL §10 corrigé (ANO-012) avec une note de fraîcheur pointant vers le code.
- `gateway/CLAUDE.md` : ✅ Corrigée — n'affirme plus que seul `auth_service` est branché (ANO-011).
- `services/config/CLAUDE.md` : ✅ « 8 clés par défaut » → 10.
- `docs/SRS.md` EF-AUTH-004 : ✅ « mot de passe temporaire » (qui n'existe pas dans l'implémentation réelle) remplacé par une description du flux d'activation réel (lien e-mail ADMIN / OTP WhatsApp autres rôles).

---



## 9. Recommandations priorisées

1. ✅ **ANO-001 corrigée** (PR #18) — casse des clés Config uniformisée sur les 4 services consommateurs.
2. ✅ **ANO-002 corrigée** (PR #19) — IDOR sur le PDF de l'espace abonné.
3. ✅ **ANO-004 corrigée** — poussé directement sur la PR #16 (`feature/campagne-demarrer-maintenant`).
4. ✅ **ANO-003 corrigée** (PR #20) — vérification du statut ACTIF avant ajout en campagne, bloquante.
5. ✅ **ANO-005 corrigée** (PR #21) — authentification par clé partagée sur whatsapp-service.
6. Planifier la mise à jour des documents obsolètes (§8) — en particulier `ARCHITECTURE.md` qui se contredit elle-même sur un point technique central (canal WhatsApp).
7. Combler les trous de couverture de tests identifiés (ANO-022 et tests manquants sur `demarrer_maintenant`) avant que ces zones grossissent.
