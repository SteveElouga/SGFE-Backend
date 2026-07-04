# État du Système — Bilan d'implémentation

## Système de Gestion de Facturation d'Eau

> **Nature de ce document :** base de vérité vivante sur ce qui est **réellement construit et vérifié dans le code**, par opposition à ce qui est *prévu* (`SRS.md`), *conçu* (`ARCHITECTURE.md`) ou *décidé* (`ADR.md`). Ce document décrit l'état constaté à sa date de rédaction et doit être mis à jour à chaque évolution significative d'un service (nouvelle règle métier, changement de contrat gRPC/GraphQL, correction d'une anomalie listée ici).
> **Méthode :** chaque service a été audité indépendamment (lecture du code source, exécution réelle des suites de tests, comparaison avec les `.proto` et avec `CLAUDE.md`/`SRS.md`/`ARCHITECTURE.md`). Toute affirmation ci-dessous est traçable à un fichier et, si possible, une ligne.
> **Date de l'audit initial :** 2026-07-02 · **Branche auditée :** `feature/campagne-demarrer-maintenant` · **Dernier commit :** `2500707`
> **Date de résolution :** 2026-07-03 — les 24 anomalies du §4 (hors ANO-016, Reporting Service, différé) ont chacune fait l'objet d'une branche dédiée, d'une PR vers `develop`, d'une CI verte, puis d'un merge. `develop` reflète désormais l'état corrigé décrit dans ce document.
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
10. [Pipeline CI/CD et Dockerfiles](#10-pipeline-cicd-et-dockerfiles)

---



## 1. Résumé exécutif


| Brique                  | Statut                                             | Tests                   | Points d'attention majeurs                                                         |
| ----------------------- | -------------------------------------------------- | ----------------------- | ---------------------------------------------------------------------------------- |
| Gateway (GraphQL)       | 🟢 Fonctionnel, riche                              | 124 ✅                   | ANO-002 (IDOR PDF), ANO-015 (subscription non protégée), ANO-022 (couverture) corrigées — PR #19, #28, #33 ; ANO-026 (endpoint PDF back-office) — PR #48 |
| Auth Service            | 🟢 Fonctionnel, solide                             | 94 ✅                    | ANO-006 (rotation refresh token) — PR #22 ; ANO-020 (Logout homogène) — PR #32 ; ANO-014/024 (duplication + parsing whatsapp_client) — PR #27 |
| Abonné Service          | 🟢 Fonctionnel, propre                             | 52 ✅                    | ANO-003bis (doc CLAUDE.md) — PR #25 ; ANO-017 (contrainte compteur actif) — PR #29            |
| Campagne Service        | 🟢 Fonctionnel                                     | 71 ✅                    | ANO-003 — PR #20, ANO-004/`demarrer_maintenant` — PR #16, ANO-018 — PR #30, ANO-019 — PR #31 |
| Facturation Service     | 🟢 Fonctionnel                                     | 73 ✅                    | ANO-007 — PR #23, ANO-008 — PR #24 ; refonte PDF WeasyPrint — PR #41 ; ANO-028 (rendu PDF figé chez l'abonné) — PR #52 |
| Paiement Service        | 🟢 Fonctionnel, bien testé                         | 60 ✅                    | Service le plus robuste de l'audit ; ANO-023 (code mort) — PR #34, ANO-025 (délai de pause des relances no-op) — PR #45 |
| Notification Service    | 🟢 Fonctionnel                                     | 46 ✅                    | ANO-013 (confirmation paiement WhatsApp jamais envoyée) — PR #26 ; ANO-014/024 — PR #27       |
| Config Service          | 🟢 Fonctionnel                                     | 29 ✅                    | ANO-001 (casse des clés) corrigée — PR #18                                         |
| Reporting Service       | ⚪ **N'existe pas**                                 | —                       | Seul le `.proto` existe ; dossier `services/reporting/` absent                     |
| whatsapp-service (Node) | 🟢 Fonctionnel                                     | — (pas de tests)        | ANO-005 (auth endpoints) corrigée — PR #21                                         |


**Total : 508 tests exécutés à travers les 8 briques Python** (`develop`, vérifié par exécution réelle après le merge des 18 PR de correctifs puis de la refonte PDF facturation — voir §4, §5.5 et §7). Chiffre initial de l'audit avant tout correctif : 439 tests (dont 1 échec, gateway — voir ANO-004).

**Infrastructure (Dockerfiles + pipeline CI/CD) également auditée et corrigée — voir §10** : build multi-stage, non-root en lecture seule, `HEALTHCHECK`, digests pinnés, cache Docker en CI, scan de vulnérabilités (Trivy) bloquant, SBOM + provenance + signature cosign sur les images publiées, versions de dépendances alignées entre services, Dependabot configuré.

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
services/facturation/ ✅ Django + gRPC :50054 — app "factures" (+ génération PDF HTML→WeasyPrint)
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

**ANO-026 — Gateway : aucun endpoint back-office pour télécharger le PDF d'une facture** — ✅ **RÉSOLU** (PR #48, branche `fix/ano-026-pdf-facture-back-office`)

- **Constat** : le bouton « PDF » de la vue facture (ADMIN/COMPTABLE) appelait `GET /api/factures/<id>/pdf` → **404**. La Gateway ne câblait le RPC `GetFacturePDF` qu'à une seule route, `espace_abonne_pdf` (espace abonné public, token WhatsApp) ; aucune route authentifiée par JWT n'existait pour le back-office, et aucun champ GraphQL ne renvoie les octets du PDF (le type `Facture` n'expose que `pdf_path`, un chemin serveur inutilisable côté navigateur).
- **Correctif appliqué** : nouvelle vue Django `facture_pdf` (`gateway/schema/facturation_views.py`) sur `GET /factures/<facture_id>/pdf/`, gardée par JWT + rôle ADMIN/COMPTABLE (`extract_token` + `auth_client.validate_token`, même contrat que le gating GraphQL des factures), relayant `GetFacturePDF`. Mapping d'erreurs : `NOT_FOUND` → 404, autre → 503. 7 tests ajoutés. **Contrat frontend** : appeler `/factures/<id>/pdf/` (au lieu de `/api/factures/<id>/pdf`).

**ANO-028 — Facturation : les abonnés reçoivent l'ancien rendu du PDF (cache jamais invalidé après un changement de gabarit)** — ✅ **RÉSOLU** (PR #52, branche `fix/facturation-pdf-rendu-abonnes`)

- **Constat** : le PDF est généré une fois puis stocké (`pdf_path`) ; `get_pdf_bytes` ne le régénérait que si le **fichier était absent**. Une facture générée avant la refonte WeasyPrint (PR #41) conservait donc indéfiniment son ancien PDF ReportLab — resservi à chaque téléchargement/renvoi et déjà parti en WhatsApp. Symptôme observé : le design (`facture_pdf.html`) affiche la maquette « AquaBill », mais les abonnés recevaient le vieux tableau ReportLab. Défaut structurel : le cache n'avait aucune notion de « ce PDF a-t-il été produit par le gabarit actuel ? ».
- **Correctif appliqué** : versioning de gabarit — `pdf_generator.PDF_TEMPLATE_VERSION` + champ `Facture.pdf_template_version` (migration `0003`). `get_pdf_bytes` régénère dès que la version stockée diffère de la version courante ; si la régénération échoue (WeasyPrint indisponible), **repli sur le PDF existant** plutôt que rien. Commande one-shot `regenerer_pdfs` (`--all`/`--before`/`--limit`/`--dry-run`, rapport succès/échec) pour rafraîchir en masse et voir les échecs. Corrigé au passage : la phrase « Règlement sous 5 jours » du gabarit était codée en dur → désormais dérivée des dates de la facture. 13 tests ajoutés. **Exploitation** : après déploiement, lancer `python manage.py regenerer_pdfs` puis renvoyer les factures voulues (`ReenvoyerFacture`) pour que les abonnés reçoivent le nouveau rendu.



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

**ANO-025 — Paiement : le délai de « pause des relances après versement partiel » (`impaye_suspension_relances`) était un no-op silencieux** — ✅ **RÉSOLU** (PR #45, branche `fix/ano-025-pause-relances-config`) — le paramètre exposé à l'ADMIN dans l'onglet Relances & Impayés était persisté (`updateConfig` réussissait, `getConfigs` renvoyait la nouvelle valeur) mais **jamais appliqué** : `suspendre_relances_si_partiel` était appelé sans argument depuis le `grpc_server` (repli sur le défaut codé en dur 5 j) et le `suspension_relances` lu par le cron `verifier_et_escalader` était un **paramètre mort** de `_escalader_facture`. Même classe qu'ANO-001 (config sans effet observable), instance distincte non couverte par PR #18. Corrigé : `suspendre_relances_si_partiel` lit `impaye_suspension_relances` depuis Config Service (repli gracieux) quand `jours_suspension` n'est pas fourni ; le paramètre mort est retiré. Test de non-régression ajouté (60 tests).

**ANO-027 — Facturation : `agent_username` et l'heure du relevé absents du PDF** — ⏳ **dette identifiée (non bloquant)** — le gabarit prévoit « Relevé par … à HH:MM », mais Campagne Service ne trace pas encore l'agent ayant saisi l'index ni l'heure exacte du relevé ; ces deux champs restent vides sur le PDF (repli propre, les autres champs du relevé sont présents). À lever côté Campagne Service (traçage auteur/horodatage du relevé) avant de compléter le rendu (`pdf_generator.py`, `DonneesFacture.agent_username`/`heure_releve`).

### ⚪ Faibles

- **ANO-017** — ✅ **RÉSOLU** (PR #29) — Abonné : `StatutCompteur.DESACTIVE` était défini mais jamais utilisé ; utilisé désormais par `resilier_abonne()` (le compteur d'un abonné résilié passe à `DESACTIVE`). Contrainte `UniqueConstraint` partielle ajoutée sur `Compteur` (condition `statut=ACTIF`) pour garantir en base un seul compteur `ACTIF` par abonné (auparavant logique applicative seule).
- **ANO-018** — ✅ **RÉSOLU** (PR #30) — Campagne : `serializers.py::releve_to_proto` utilise désormais le même helper `_to_iso` que `campagne_to_proto` (introduit par le commit `d54133a`). Tests de sérialisation ajoutés (aucun n'existait pour ce module).
- **ANO-019** — ✅ **RÉSOLU** (PR #31) — Campagne : `find_planifiee_pour_date` utilisait `.first()`. Remplacé par `list_planifiees_pour_date` : toutes les campagnes partageant une même `date_planifiee` démarrent désormais au même passage du cron 7h.
- **ANO-020** — ✅ **RÉSOLU** (PR #32) — Auth : `Logout` gérait ses erreurs différemment des 12 autres RPC (catch local au lieu de déléguer à l'intercepteur). Try/except supprimé — un token invalide lève désormais `UNAUTHENTICATED` comme partout ailleurs. Aucun changement nécessaire côté Gateway (`GrpcErrorExtension` déjà générique).
- **ANO-021** — ✅ **RÉSOLU** (PR #25) — `services/config/CLAUDE.md` disait « 8 clés par défaut », corrigé à 10.
- **ANO-022** — ✅ **RÉSOLU** (PR #33) — Gateway : aucun test pour `facturation_queries/mutations`, `paiement_queries/mutations`, `notification_queries/mutations` — trou de couverture notable vu l'exigence CLAUDE.md (« couverture > 80 % »). Ajout de `test_facturation.py`, `test_paiement.py`, `test_notification.py` (19 tests) suivant le même patron que `test_campagne.py`/`test_abonne.py`. `espace_abonne.py` et `subscriptions.py` sont désormais également couverts, via PR #19 (ANO-002) et PR #28 (ANO-015).
- **ANO-023** — ✅ **RÉSOLU** (PR #34) — Paiement : `repositories.py::marquer_resolu` jamais appelé nulle part (code mort, l'admet dans son propre docstring). Supprimée.
- **ANO-024** — ✅ **RÉSOLU** (PR #27) — `whatsapp_client.py::send()`/`send_with_pdf()` (auth et notification) appelaient `response.json()` avant de vérifier le status code HTTP ; corrigé (vérification 503 d'abord, parsing JSON entouré d'un `try/except ValueError`). Tests dédiés ajoutés (le module n'en avait aucun).
- **ANO-029** — ✅ **RÉSOLU** (PR #53) — Code mort supprimé (audit `vulture` + vérification grep, 0 référence hors définition ; même classe qu'ANO-023) : `campagne/grpc_interceptors.py` **entier** (`JWTAuthInterceptor` jamais monté — le grpc_server campagne ne monte aucun interceptor ; contenait les **2 seuls `Any`** du projet), `campagne/repositories.py::list_planifiees` (reste d'ANO-019), `paiement/` + `notification/repositories.py::list_by_facture`/`list_by_abonne` (supplantés par `list_by_facture_and_abonne`), `paiement/schedulers.py::stop_scheduler` (jamais appelé). −81 lignes. Aucune perte de sécurité : l'auth gRPC interne n'existe nulle part (assurée au Gateway).
- **ANO-030** — ✅ **RÉSOLU** (PR #54) — Facturation : `FactureService` instanciait ses 4 clients gRPC en dur dans `__init__` → les tests de service/servicer déclenchaient de vrais appels réseau (§5.5, violation DIP). Clients désormais **injectables** (défaut = client réel, typés via `TYPE_CHECKING`) ; helper de test partagé `tests/helpers.py::service_avec_clients_mockes()`. `test_services` et `test_grpc` n'effectuent plus d'appel réseau incident. Facturation était le seul service concerné (paiement mocke déjà via `@patch`, Gateway via singletons). 73 tests verts.
- **ANO-031** — ✅ **RÉSOLU** (PR #56) — Le lien « Consultez votre historique en ligne » **n'apparaissait jamais** sur le PDF : le gabarit le prévoit (`{% if espace.url %}`) mais Facturation ne peuplait jamais `espace_url` (le token, propriété de Notification, n'existait pas à la génération). Nouveau RPC `NotificationService.GetEspaceUrl(abonne_id, facture_id)` (get-or-create : réutilise le token actif non expiré de l'abonné, en crée un sinon avec l'expiration `token_validite_jours`) ; Facturation l'appelle en générant le PDF (`_generer_et_sauver_pdf`, dégradation gracieuse → bloc masqué si Notification KO). Réutilise le couplage facturation→notification existant, aucun nouveau. Le lien apparaît désormais dans tous les PDF (envoi, back-office, régénérés).

---



## 5. État détaillé par service



### 5.1 Gateway (`gateway/`)

**Rôle** : point d'entrée GraphQL unique (Strawberry), ASGI, aucune base de données propre (`ENGINE: django.db.backends.dummy`). Fédère les 7 services métier (tout sauf reporting) via des clients gRPC synchrones dans `schema/grpc_clients.py`.

**Authentification** : le token JWT n'est **jamais décodé localement** — chaque requête protégée déclenche un appel gRPC `ValidateToken` vers Auth Service (`schema/context.py:49-54`), qui reste l'unique source de vérité sur l'identité et le rôle. Le refresh token vit dans un cookie `HttpOnly + Secure(si prod) + SameSite=Strict` (`schema/context.py:34-42`), jamais renvoyé dans le corps GraphQL.

**Surface GraphQL réelle** (voir aussi §6 de `docs/ARCHITECTURE.md`, corrigé — ANO-012 résolu, PR #25) :

- **25 queries**, **35 mutations**, **1 subscription** (`abonneUpdated`, sur Redis pub/sub, exige ADMIN depuis ANO-015 résolu). Dont `whatsappQr` (ADMIN, PR #46) : relaie le statut de connexion + QR de liaison WhatsApp depuis notification-service, pour l'affichage dans l'UI admin sans exposer la clé interne du whatsapp-service au navigateur.
- Contrôle de rôle systématique via `require_role(info, *roles)` sur toutes les opérations sensibles, y compris désormais `abonneUpdated` (ANO-015). Seule exception volontaire : `infosSociete` (public — sert à alimenter les PDF).
- Filtrage SUPERVISEUR/AGENT par propriété de campagne implémenté et testé (`campagne_queries.py:11-29`).
- Espace abonné public tokenisé : 2 vues Django classiques (pas GraphQL), `GET /espace-abonne/<token>/` (liste des factures) et `GET /espace-abonne/<token>/facture/<id>/pdf/` (téléchargement — IDOR corrigé, ANO-002 résolu, PR #19).
- PDF facture back-office : vue Django `GET /factures/<id>/pdf/` (JWT + rôle ADMIN/COMPTABLE), relaie `GetFacturePDF` — comble un 404 côté back-office (ANO-026 résolu, PR #48).

**Tests** : 124 tests (`gateway/schema/tests/`), tous verts. Couverture complétée par PR #19 (`espace_abonne.py`, ANO-002, +6), PR #28 (`subscriptions.py`, ANO-015, +3), PR #33 (facturation/paiement/notification, ANO-022, +19) et PR #48 (endpoint PDF back-office, ANO-026, +7).

### 5.2 Auth Service (`services/auth/`)

**Rôle** : authentification JWT (access 24h / refresh 7j), gestion des comptes (4 rôles : ADMIN, AGENT, COMPTABLE, SUPERVISEUR), verrouillage anti-bruteforce.

**Modèle** : `User` (UUID, `role`, `failed_attempts`, `locked_until`), `RevokedToken` (blacklist par `jti`), `PasswordSetupToken` (activation ADMIN par e-mail, 48h par défaut), `PhoneOtpToken` (activation/reset par OTP WhatsApp, code à 6 chiffres **haché**, jamais stocké en clair).

**Workflow d'activation à deux canaux** : ADMIN → e-mail Brevo (lien `set-password`) ; autres rôles → OTP WhatsApp (6 chiffres, lien `activer-compte`). Aucun « mot de passe temporaire » n'existe (contrairement à ce que suggère `docs/SRS.md` EF-AUTH-004 — le compte est créé avec `set_unusable_password()` et activé uniquement via lien/OTP).

**RPC** : les 13 RPC du `.proto` sont tous implémentés, correspondance exacte. Aucun contrôle de rôle n'est fait dans ce service lui-même (`CreateUser`/`UpdateUser`/`DeactivateUser` acceptent tout appelant gRPC) — entièrement délégué à la Gateway (`require_role(info, "ADMIN")` avant l'appel), cohérent avec l'architecture mais sans défense en profondeur.

**Tests** : 94 tests, **tous verts** (confirmé par exécution). Couverture large (login, verrouillage, refresh, logout, création/activation par les deux canaux, reset mot de passe anti-énumération, rotation du refresh token — ANO-006, PR #22 ; client WhatsApp — ANO-014/024, PR #27).

### 5.3 Abonné Service (`services/abonne/`)

**Rôle** : gestion des abonnés (numérotation auto `AB-XXXX`) et de leurs compteurs (avec historique de remplacement).

**Modèle** : `Abonne` (statut `ACTIF`/`SUSPENDU`/`RESILIE` — pas de retour possible depuis `RESILIE`), `Compteur` (statut `ACTIF`/`REMPLACE`/`DESACTIVE`, ce dernier désormais posé par `resilier_abonne()` — ANO-017 résolu, PR #29), `HistoriqueCompteur`.

**RPC** : les 12 RPC du `.proto` sont tous implémentés, correspondance exacte. Publication d'événements `ABONNE_CREATED`/`ABONNE_UPDATED` sur Redis pub/sub — **consommée uniquement par la Gateway** (subscriptions GraphQL), jamais par Campagne Service (ANO-003bis).

**Tests** : 52 tests, tous verts (confirmé par exécution).

### 5.4 Campagne Service (`services/campagne/`)

**Rôle** : cycle de vie des campagnes de relevé mensuelles (`PLANIFIEE` → `EN_COURS` → `CLOTUREE`) et des relevés d'index associés.

**Modèle** : `Campagne` (`created_by`, `numero_mobile_money`, toggles `generer_factures_auto`/`envoyer_whatsapp_auto`), `CampagneAgent` (affectation), `Releve` (`unique_together` campagne+abonné, statuts `A_RELEVER`/`RELEVE`/`NON_RELEVE`/`ESTIME`).

**Fonctionnalité** `demarrer_maintenant` (commit `29e91c7`, mergée via PR #16) : la création d'une campagne peut désormais démarrer directement `EN_COURS` au lieu de `PLANIFIEE`. Câblage propre bout-en-bout (proto → service → repository → grpc_server). Le test `gateway` cassé par l'ajout de ce paramètre et l'absence de tests dédiés ont été corrigés directement sur cette même PR (ANO-004 résolu — voir §4).

**Scheduler 7h00** : démarre toutes les campagnes `PLANIFIEE` dont `date_planifiee` est aujourd'hui **ou hier** (rattrapage) — ANO-019 résolu, PR #31.

**Clôture → Facturation** : appel gRPC direct et synchrone (`CampagneServicer.CloturerCampagne` → `FacturationServiceClient.notifier_campagne_cloturee`), dégradation gracieuse si Facturation est indisponible — mais alors **aucune facture n'est jamais générée** pour cette campagne, sans retry ni alerte.

**RPC** : les 11 RPC du `.proto` sont tous implémentés. Filtrage SUPERVISEUR par `created_by` **implémenté et testé** (contrairement à la note obsolète de CLAUDE.md racine — ANO-010).

**Tests** : 71 tests, tous verts. Lacunes restantes : rattrapage J-1 du scheduler, `numero_mobile_money` invalide.

### 5.5 Facturation Service (`services/facturation/`)

**Rôle** : génération des factures à la clôture d'une campagne, calcul du montant, génération PDF (gabarit HTML/CSS → PDF via **WeasyPrint**, PR #41), gestion du tarif actif.

**PDF de facture (PR #41)** : rendu HTML/CSS reproduisant la maquette client (en-tête société, cartes « Facturé à » / « Compteur », tableau de consommation, total, modalités de paiement). L'identité de l'abonné (nom, N° abonné, quartier/camp, WhatsApp, N° compteur) est récupérée via un nouveau client gRPC **Facturation → Abonné** (`AbonneServiceClient.get_abonne`) au lieu d'afficher les UUID techniques. Dégradation gracieuse : si Abonné Service est indisponible, le PDF est tout de même produit (repli sur l'identifiant tronqué). `generer_pdf` importe WeasyPrint paresseusement — l'absence des bibliothèques natives (pango/cairo) en environnement de test n'empêche pas l'import du module. Hors périmètre volontaire (pour ne pas afficher de données non disponibles) : graphique de consommation 6 mois, agent/heure du relevé, lien espace abonné sur le PDF.

**Fraîcheur du PDF (PR #52, ANO-028)** : le PDF est stocké après génération et **versionné** (`Facture.pdf_template_version` vs `pdf_generator.PDF_TEMPLATE_VERSION`). `get_pdf_bytes` régénère automatiquement dès que la version stockée est obsolète — sans ce marqueur, les PDF produits avant un changement de gabarit restaient servis tels quels (les abonnés recevaient l'ancien rendu). En cas d'échec de régénération (WeasyPrint indisponible), repli sur le PDF existant plutôt que rien. Commande `python manage.py regenerer_pdfs` pour rafraîchir en masse (option `--dry-run`).

**Modèle** : `Tarif` (`prix_m3`, `is_active` — un seul actif à la fois, garanti applicativement, pas par contrainte DB), `Facture` (`prix_m3` **copié**, `statut`, `numero_facture` séquentiel `FACT-AAAA-MM-XXXX`, `pdf_path`, `pdf_template_version`, `numero_mobile_money` ajouté au commit `701fe0b`).

**Workflow** : `GenererFactures` récupère les relevés `RELEVE` depuis Campagne (échec bloquant si Campagne est indisponible — volontairement non dégradé, contrairement au reste du flux), calcule montant et date limite, crée la facture + PDF en transaction atomique, puis notifie Paiement (`InitialiserSolde`) et Notification (`EnvoyerFacture`) **hors transaction**, avec dégradation gracieuse.

**RPC** : les 8 RPC du `.proto` sont tous implémentés. `GetFacturesParCampagne` est le seul RPC sans `try/except` (incohérence mineure de pattern).

**Absence de Reporting** : aucun appel, aucune trace — cohérent avec ANO-016.

**Tests** : 73 tests, tous verts (confirmé par exécution ; +12 sur PR #41 pour le gabarit HTML et le client Abonné, +13 sur PR #52 pour le versioning/régénération PDF, la commande `regenerer_pdfs` et le délai de règlement dynamique). Le rendu PDF réel (WeasyPrint) est vérifié séparément par un smoke-test dans l'image Docker (`%PDF-`), car il dépend de bibliothèques natives absentes de l'environnement de CI. Depuis PR #54 (ANO-030), les tests de service/servicer **n'effectuent plus d'appel réseau incident** : `FactureService` accepte ses clients gRPC en injection (défaut = client réel) et `tests/helpers.py::service_avec_clients_mockes()` les mocke dans `test_services` et `test_grpc`. Les seuls appels réels restants sont ceux de `AbonneServiceClientTests`/`CampagneServiceClientTests`, qui exercent volontairement la dégradation gracieuse du client.

### 5.6 Paiement Service (`services/paiement/`)

**Rôle** : enregistrement des versements (partiels/multiples), suivi du solde par facture, escalade automatique des impayés.

**Modèle** : `Paiement` (mode `ESPECES`/`MOBILE_MONEY`/`VIREMENT`, `reference_transaction`), `SoldeFacture` (une ligne par facture, statut dérivé), `SuiviImpaye` (`facture_id` unique, étapes 1 à 4 avec flags/dates).

**Workflow paiement** : validation montant > 0, référence obligatoire selon le mode, anti-surpaiement (`montant > solde_restant` refusé), mise à jour atomique du solde et du statut, synchronisation vers Facturation (`UpdateStatutFacture`, dégradé), et si `PAYEE` : réactivation de l'abonné + résolution du suivi impayé + confirmation WhatsApp étape 0.

**Workflow impayés (cron 8h00)** : pour chaque facture en retard, tente successivement rappel 1 / rappel 2 / avertissement (étapes indépendantes, peuvent se déclencher en cascade le même jour si le cron a été interrompu plusieurs jours), puis suspension automatique (si activée en config) avec notification admin. Un `SuiviImpaye.relances_suspendues_jusqu` (posé sur paiement partiel) met en pause **toutes** les relances, y compris la suspension, jusqu'à une date donnée.

**Délais de relance** : `impaye_delai_rappel_1/2`, `avertissement`, `suspension` sont désormais lus avec succès depuis Config Service (ANO-001 résolu, PR #18) — défauts internes (rappel_1=0, rappel_2=3, avertissement=7, suspension=10 jours) utilisés seulement si Config Service est indisponible.

**RPC** : les 6 RPC du `.proto` sont tous implémentés. C'est le service jugé le plus robuste de l'audit (gestion d'erreur homogène) ; deux anomalies corrigées depuis l'audit initial : le code mort `marquer_resolu` supprimé (ANO-023, PR #34) et le no-op silencieux du délai de pause des relances `impaye_suspension_relances` (ANO-025, PR #45). Le bug de config ANO-001 lui était externe (déjà corrigé, PR #18).

**Tests** : 60 tests, tous verts (confirmé par exécution).

### 5.7 Notification Service (`services/notification/`) + whatsapp-service (Node.js)

**Rôle** : envoi de messages WhatsApp (facture, relances impayés étapes 1-4, notification admin) et gestion des tokens d'accès à l'espace abonné.

**⚠️ Canal réel : whatsapp-web.js, pas Telnyx** (voir ANO-009). Le champ `telnyx_message_id` du modèle `Envoi` et du `.proto` est un vestige mort, jamais renseigné.

**Modèle** : `Envoi` (`type_envoi` : `FACTURE`/`RELANCE_1`/`RELANCE_2`/`AVERTISSEMENT`/`SUSPENSION`/`RETABLISSEMENT` — ce dernier désormais produit à l'étape 0, ANO-013 résolu), `TokenAcces` (UUID, expiration configurable, ANO-001 résolu).

**whatsapp-service (Node.js)** : `whatsapp-web.js` + Puppeteer/Chromium headless, session persistée sur volume Docker (`LocalAuth`), reconnexion avec backoff exponentiel sur déconnexion. Endpoints `GET /health`, `GET /qr` (page HTML du QR), `GET /qr-data` (QR en JSON `{ready, qr}` pour relais UI admin, PR #46), `POST /send`, `POST /send-with-pdf`. Authentification par clé partagée (en-tête `X-Internal-Api-Key`) sur tous sauf `/health` (ANO-005 résolu, PR #21).

**Liaison WhatsApp depuis l'UI admin (PR #46)** : le QR de connexion, autrefois seulement atteignable sur l'endpoint interne `/qr` (401 dans un navigateur faute d'en-tête), est désormais exposé à l'admin via la chaîne gRPC `Gateway (query ADMIN whatsappQr) → notification-service (GetWhatsAppQr) → whatsapp-service (/qr-data)`. La clé interne reste côté serveur ; le QR tournant, l'UI rafraîchit périodiquement tant que `ready` est faux. Dégradation gracieuse si whatsapp-service est indisponible (`ready=False, qr=""`). `ARCHITECTURE.md` §5.7 affirmait à tort un « Retry Handler — 3 tentatives » : **il n'y a en réalité aucune logique de retry**, ni côté Node ni côté Django (échec immédiatement marqué `ECHEC`, dégradation gracieuse sans nouvelle tentative) — documentation corrigée (ANO-009 résolu, PR #25).

**Duplication de code** : `whatsapp_client.py` existe identique dans `auth` et `notification` — assumée et documentée (ANO-014, PR #27).

**RPC** : les 8 RPC du `.proto` sont tous implémentés, y compris les champs `type_envoi`/`abonne_id` ajoutés au dernier commit (`2500707`).

**Tests** : 46 tests, tous verts. `message_builder.py` (templates de messages) reste sans test direct dédié ; `whatsapp_client.py` en a désormais un (ANO-014/ANO-024, PR #27).

### 5.8 Config Service (`services/config/`)

**Rôle** : paramètres système clé/valeur génériques (`ConfigParam`) + informations société typées (`InfosSociete`, singleton, alimente les PDF de facture).

**Voir ANO-001, résolu (PR #18)** — c'était le bug le plus impactant de cet audit initial, bien qu'il se manifestait chez les *consommateurs* de ce service : `CONFIG_DEFAULTS` (originellement en MAJUSCULES) ne couvrait pas les clés attendues par Paiement (`impaye_`*) et la casse ne correspondait à aucun des appels effectués par Facturation/Notification. Corrigé : clés renommées en minuscule, 6 clés `impaye_*` ajoutées.

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

Chiffres vérifiés par exécution réelle sur `develop`, après le merge des 18 PR de correctifs listées au §4.

| Service      | Tests exécutés | Résultat | Méthode de vérification                             |
| ------------ | --------------- | -------- | ----------------------------------------------------- |
| Auth         | 94              | ✅ OK    | Exécution réelle (`manage.py test comptes`)            |
| Abonné       | 52              | ✅ OK    | Exécution réelle (`manage.py test abonnes`)            |
| Campagne     | 71              | ✅ OK    | Exécution réelle (`manage.py test campagnes`)          |
| Facturation  | 45              | ✅ OK    | Exécution réelle (`manage.py test factures`)           |
| Paiement     | 59              | ✅ OK    | Exécution réelle (`manage.py test paiements`)          |
| Notification | 46              | ✅ OK    | Exécution réelle (`manage.py test notifications`)      |
| Config       | 29              | ✅ OK    | Exécution réelle (`manage.py test parametres`)         |
| Gateway      | 112             | ✅ OK    | Exécution réelle (`manage.py test schema`)             |
| **Total**    | **508**         | **508 ✅** |                                                       |

Trous de couverture restants : absence de tests pour `event_publisher.py` (Abonné), absence de tests pour `schedulers.py` en tant que tel (Campagne, Paiement — la logique métier interne est testée, pas le déclenchement APScheduler), `message_builder.py` (Notification) sans test direct dédié.

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
6. ✅ **ANO-009/010/011/012/021 corrigées** (PR #25, documentation uniquement) — `CLAUDE.md`, `docs/ARCHITECTURE.md`, `gateway/CLAUDE.md`, `services/config/CLAUDE.md` mis à jour, dont la contradiction sur le canal WhatsApp (Telnyx vs whatsapp-web.js). Voir §8.
7. ✅ **ANO-022 corrigée** (PR #33) — couverture ajoutée pour facturation/paiement/notification côté Gateway. Reste : tests manquants sur `event_publisher.py` (Abonné), `message_builder.py` (Notification).
8. ✅ **ANO-023 corrigée** (PR #34) — suppression du code mort `marquer_resolu` côté Paiement.

---



## 10. Pipeline CI/CD et Dockerfiles

**Date de l'audit :** 2026-07-03. Portée : les 10 `Dockerfile` du monorepo (gateway, 7 services Python, nginx, whatsapp-service) et `.github/workflows/ci.yml`. Contrairement au reste de ce document (audit du code applicatif), cette section couvre l'infrastructure de build/déploiement — non cataloguée en `ANO-XXX` (registre réservé aux anomalies applicatives) mais suit le même principe : corrigé directement dans `develop`, sans anomalie ouverte en suspens.

### 10.1 Constat initial

- **Dockerfiles** : image finale gonflée par les outils de build (`gcc`/`libpq-dev` jamais retirés, un seul stage), `.dockerignore` absent sur 7 services sur 10, aucun `HEALTHCHECK`, base images non reproductibles (tag flottant sans digest).
- **Pipeline CI** : chaque image buildée deux fois sans jamais être mise en cache (`docker-build-*` en PR, `publish-*` en push), aucun scan de vulnérabilités d'image, aucun SBOM ni attestation de provenance, aucune signature, actions tierces référencées par tag mutable plutôt que par SHA de commit, `whatsapp-service` absent du pipeline (Dockerfile existant mais aucune build/test/scan/publish).
- **Dépendances** : `grpcio`/`grpcio-tools` divergeaient silencieusement entre services censés suivre la même stack (`1.64.1` vs `1.81.1`) ; les 4 paquets `opentelemetry-*` étaient les seules dépendances en `>=` de tout le monorepo (tout le reste en `==`).

### 10.2 Corrections apportées

**Dockerfiles — PR #35** (mergée) :
- Build multi-stage sur les 8 services Python : `gcc`/`libpq-dev` isolés dans un stage `builder`, dépendances installées en `--user` puis copiées dans l'image finale. ~40-45 % de réduction de taille (ex. auth : 603 Mo → 347 Mo).
- Durcissement lecture seule : code et dépendances restent `root:root`, lisibles mais non modifiables par `appuser` à l'exécution (exception : `/app/pdfs` sur facturation, seul répertoire réellement écrit — génération PDF).
- `HEALTHCHECK` sur les 10 images (TCP pour les services gRPC, HTTP pour gateway/whatsapp-service/nginx — `/healthz` dédié sur nginx, indépendant de la disponibilité de la gateway en aval).
- `.dockerignore` ajouté sur les 7 services qui n'en avaient pas.
- Base images pinnées par digest en plus du tag (`python:3.12-slim@sha256:...`, `node:18-slim@sha256:...`, `nginx:1.27-alpine@sha256:...`).

**Pipeline CI — PR #36** (mergée) :
- Cache Docker via le backend GHA (`cache-from`/`cache-to: type=gha`), partagé par scope entre `docker-build-*` (PR) et `publish-*` (push) du même service — la publication réutilise le cache du build.
- Scan Trivy (CRITICAL/HIGH, bloquant) sur chaque image dans `docker-build-*`, scope `scanners: vuln` (le scan de secrets ferait doublon avec gitleaks déjà sur le code source, et générait un faux positif sur une clé de test embarquée dans le paquet tiers `autobahn`).
- SBOM + attestation de provenance + signature keyless (cosign, identité OIDC GitHub Actions) sur chaque image publiée sur GHCR.
- `whatsapp-service` ajouté au filtre de chemins et aux jobs `docker-build-*`/`publish-*`.
- Toutes les actions tierces épinglées par SHA de commit plutôt que par tag mutable.
- **Corrections découvertes en testant le nouveau gate Trivy localement avant de l'activer** (sinon la CI aurait cassé dès le premier merge) : nginx `1.27-alpine` → `1.29-alpine` + `apk upgrade` au build (33 CVE CRITICAL/HIGH → 0) ; whatsapp-service `node:18-slim` (EOL) → `node:22-slim` (LTS actif) + `apt-get upgrade` au build + suppression de npm/npx/corepack après `npm install` (jamais utilisés à l'exécution) — 14 CVE CRITICAL/HIGH → 0.

**Dépendances — PR #37** (mergée) :
- `grpcio`/`grpcio-tools` alignés à `1.81.1` sur les 8 services Python, avec `protobuf==6.33.6` explicite.
- `opentelemetry-api`/`-sdk`/`-instrumentation-django`/`-exporter-otlp` pinnés à la version qui résolvait déjà (`1.43.0` / `0.64b0`) sur les 7 services qui les utilisent.
- 496/496 tests relancés après le bump (aucune régression), `pip-audit` sans vulnérabilité connue.

**Dependabot — PR #38** (mergée) :
- `.github/dependabot.yml` : un job `docker` par service (10), un job `pip` par service Python (8), un job `npm` (whatsapp-service), un job `github-actions` — cadence hebdomadaire, PR ciblées sur `develop`. Complète le pinning par digest/SHA : sans lui, un digest ou un SHA pinné se périme silencieusement (c'est exactement ce que le Trivy gate a débusqué sur nginx et whatsapp-service lors de sa mise en place).

### 10.3 Points d'attention restants

- **Rétention GHCR** : aucune politique de rétention/nettoyage des images publiées n'est configurée — ce n'est pas un réglage qui se fait dans le code du dépôt (paramètre d'organisation GitHub), à traiter séparément si le volume d'images devient un problème.
- **nginx tourne en root** (comportement par défaut de l'image officielle `nginx:alpine`, le process maître doit binder le port 80) — non modifié, cohérent avec l'image amont, pas une régression introduite ici.
- **whatsapp-service** reste en single-stage (Puppeteer/Chromium doit rester dans l'image finale, rien à extraire dans un stage `builder` séparé) — c'est aussi la plus grosse image du monorepo (~1,5 Go), inhérent à Chromium, pas optimisable sans changer d'approche (ex. navigateur distant).
