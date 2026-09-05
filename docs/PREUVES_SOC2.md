# Déclaration de collecte de preuves SOC 2 — SGFE-backend

**Date de la déclaration :** 5 septembre 2026
**Périmètre :** `SGFE-backend` uniquement (gateway + 8 microservices). Le frontend n'est pas couvert par ce document.
**Objet :** déclarer formellement, contrôle par contrôle, **depuis quelle date** chaque preuve technique est collectée **en continu**, à l'usage d'un futur auditeur SOC 2 Type II qui doit établir une période d'observation. Ce document ne remplace ni `AUDIT_SGFE.md` (audit technique complet, checklist §8) ni `docs/CONFORMITE_SOC2_OWASP.md` (diagnostic de préparation référentiel par référentiel) — il en est le sous-produit opérationnel : la liste datée, vérifiable, des preuves réellement accumulées à date, base pour chiffrer une période d'observation.

---

## 0. Avertissement de portée — à lire avant toute autre section

**Ce document n'est pas une certification et ne prétend pas qu'une période d'observation suffisante s'est déjà écoulée.** Un audit SOC 2 Type II exige une période d'observation réelle (généralement 3 à 12 mois) durant laquelle l'efficacité *opérationnelle* d'un contrôle est testée sur des preuves accumulées dans le temps — pas seulement son existence dans le code à un instant T. Les dates ci-dessous sont des **points de départ**, pas des durées déjà écoulées suffisantes : à la date de rédaction, **aucun** contrôle listé ici n'a encore accumulé plusieurs mois de preuves.

Ce document est rédigé comme un journal des contrôles techniques réellement opérants, avec preuve `fichier:ligne`/PR/migration vérifiable, pas comme un rapport de complaisance. Un contrôle qui n'est PAS effectif malgré une apparence de l'être (ex. une migration `REVOKE` sans effet réel contre un rôle Postgres superutilisateur) est déclaré comme tel en §3, pas compté comme preuve.

---

## 1. Vue d'ensemble

| Contrôle | Composants couverts | Collecté en continu depuis | Mécanisme (preuve) |
|---|---|---|---|
| AuditLog métier — Paiement | `paiement` | **04/09/2026** | `services/paiement/paiements/models.py::AuditLog`, PR #193 |
| AuditLog métier — Facturation | `facturation` | **04/09/2026** | `services/facturation/factures/models.py::AuditLog`, PR #193 |
| AuditLog métier — Config | `config` | **05/09/2026** | migration `0003_auditlog.py`, PR #205 |
| AuditLog métier — Campagne | `campagne` | **05/09/2026** | migration `0009_auditlog.py`, PR #208 |
| AuditLog métier — Auth | `auth` | **05/09/2026** | migration `0007_auditlog.py`, PR #210 |
| AuditLog métier — Abonné | `abonne` | **05/09/2026** | migration `0009_auditlog.py`, PR #212 |
| Immuabilité DB *réelle* (rôle Postgres non superutilisateur) | `paiement`, `facturation` uniquement | **05/09/2026** | `libs/sgfe_common/sgfe_common/db_hardening.py`, PR #215 |
| Immuabilité DB *symbolique, non effective* | `campagne`, `config`, `auth`, `abonne` | — (jamais effective — voir §3) | migrations `..._audit_log_immutable` historiques |
| Rétention des logs + horodatage ISO 8601 UTC fiable | 9 composants (gateway + 8 services) | **04/09/2026** | bloc `LOGGING`, `TimedRotatingFileHandler`, PR #193 |
| Événements de sécurité gateway centralisés dans l'`AuditLog` Auth | `gateway` → `auth` | **05/09/2026 (aujourd'hui)** | RPC `EnregistrerEvenementSecurite`, cette PR |
| Logs locaux tamper-evident (chaînage de hash) | `auth`, `gateway` (2 composants sur 9) | **05/09/2026 (aujourd'hui)** | `sgfe_common.log_integrity.ChainedHashFormatter`, cette PR |
| Observabilité (traces, métriques, logs structurés `trace_id`) | **aucun** | jamais entamé | voir AUDIT_SGFE.md §8·I |

---

## 2. Détail par contrôle

### 2.1 AuditLog métier (piste d'audit applicative) — 6 services couverts

Conforme à la conception §10.7 d'`AUDIT_SGFE.md` : une entrée `AuditLog` par mutation métier ciblée, écrite **dans la même transaction Django** que le changement qu'elle documente (voir `<app>/audit.py::enregistrer_audit` de chaque service), acteur résolu via l'identité propagée par la gateway (`IdentityInterceptor`/`get_caller()`).

| Service | Depuis | PR | Mutations couvertes |
|---|---|---|---|
| Paiement | 04/09/2026 | #193 | Encaissement, annulation de paiement, avoir, paiement en ligne (mock) |
| Facturation | 04/09/2026 | #193 | Génération/annulation/régénération de facture, régularisation, mise à jour de tarif |
| Config | 05/09/2026 | #205 | `UpdateConfig`, `UpdateInfosSociete` |
| Campagne | 05/09/2026 | #208 | Création/clôture de campagne, saisie/correction d'index, affectation d'agent/zones |
| Auth | 05/09/2026 | #210 | Création/modification/désactivation/réactivation d'utilisateur, renvoi d'identifiants — **login, OTP et self-service volontairement exclus** (pas des mutations administratives) |
| Abonné | 05/09/2026 | #212 | Création/modification/suspension/réactivation/résiliation d'abonné, remplacement de compteur — **anonymisation RGPD volontairement exclue** (même principe que « jamais les factures/paiements » côté conservation légale) |

**Delta de période d'observation entre les deux groupes** : Paiement/Facturation ont un jour d'avance (04/09) sur les 4 autres services (05/09) — négligeable à l'échelle d'une période d'observation Type II, mais réel et à dater précisément si un auditeur segmente par service.

### 2.2 Immuabilité base de données de l'`AuditLog`

Deux états radicalement différents coexistent aujourd'hui — à ne jamais confondre :

- **Paiement et Facturation (réelle, depuis le 05/09/2026, PR #215)** : chaque service crée, via migration, un second rôle Postgres `NOLOGIN` non superutilisateur (`<rôle>_runtime`), avec `SELECT, INSERT` uniquement sur `audit_log`. La connexion applicative bascule dessus via `SET ROLE` à l'ouverture de chaque connexion (`db_hardening.py`, signal `connection_created`). **Vérifié empiriquement** contre un Postgres jetable : `UPDATE`/`DELETE` échouent avec `permission denied`, la session perd réellement `rolsuper`.
- **Campagne, Config, Auth, Abonné (symbolique, jamais effective)** : ces quatre services utilisent encore l'ancienne migration `..._audit_log_immutable`, qui fait un simple `REVOKE UPDATE, DELETE` pour le rôle applicatif courant. Ce rôle s'est révélé être un **superutilisateur Postgres** (héritage du bootstrap `initdb` de l'image officielle, `POSTGRES_USER`) — un superutilisateur contourne **toute** ACL, `REVOKE` compris. Cette révocation n'a donc **aucun effet réel** pour ces 4 services aujourd'hui. **Ne pas la compter comme preuve d'immuabilité niveau base** : seule l'immuabilité applicative tient pour eux (aucun code de ces services ne fait d'`UPDATE`/`DELETE` sur `audit_log` — mais rien n'empêcherait un accès direct à la base de le faire).
- Le mécanisme générique existe désormais dans `libs/sgfe_common/sgfe_common/db_hardening.py` pour que ces 4 services l'adoptent sans le redécouvrir (voir "Comment un futur service adopte ce mécanisme" dans ce fichier) — non fait à ce jour.

### 2.3 Rétention des logs et horodatage fiable — 9 composants

Depuis le **04/09/2026** (PR #193) : bloc `LOGGING` sur les `settings.py` des 9 composants (gateway + 8 services) — horodatage explicite en UTC (`logging.Formatter.converter = time.gmtime`, cohérent avec `TIME_ZONE = "UTC"`), rétention configurable via `LOG_RETENTION_DAYS` (défaut 30 jours, `TimedRotatingFileHandler`, un fichier par jour). Désactivé en mode `TESTING`.

### 2.4 Événements de sécurité de la gateway centralisés dans l'`AuditLog` Auth — nouveau, depuis aujourd'hui

Depuis le **05/09/2026 (date de cette déclaration)** : la gateway relaie désormais, en plus de son logger local `security` (inchangé, conservé comme filet), chaque refus de rôle (`require_role`, action `ROLE_REFUSE`) et chaque échec de validation de jeton (`require_auth`, action `TOKEN_INVALIDE`) vers un nouveau RPC `EnregistrerEvenementSecurite` (`proto/auth_service.proto`), qui écrit une entrée `AuditLog` côté Auth (`objet_type="EvenementSecuriteGateway"`, `action=type_evenement`), dans sa propre transaction dédiée. Appel **best-effort et non bloquant** : un échec (Auth indisponible, etc.) ne fait jamais échouer la requête GraphQL en cours — capturé et journalisé en avertissement, exactement le patron déjà en place pour `publish_user_event`/`event_publisher.py`. Complète, pour la gateway, la branche de la conception §10.7 qui restait non livrée (« logger via `AuditLog` si un service concerné est impliqué ») — Auth est ce service pour tous les événements de sécurité de la gateway.

Auth reste le seul service à recevoir ces événements (propriétaire naturel de l'identité) — aucun autre service n'émet aujourd'hui d'événement de sécurité comparable.

### 2.5 Logs locaux tamper-evident (chaînage de hash) — nouveau, depuis aujourd'hui, 2 composants sur 9

Depuis le **05/09/2026 (date de cette déclaration)**, sur **Auth** et la **Gateway** uniquement (les deux points d'entrée les plus sensibles pour la sécurité) : `sgfe_common.log_integrity.ChainedHashFormatter` calcule, pour chaque ligne écrite dans le fichier de log (`sha256(hash_précédent + ligne)`), un `log_hash` en suffixe — câblé sur le handler `TimedRotatingFileHandler` uniquement (jamais sur la sortie console). Un outil compagnon, `sgfe_common/verifier_chaine_logs.py`, permet à un futur auditeur de relire un fichier et de confirmer que la chaîne n'a pas été rompue.

**Ce que ce mécanisme garantit VRAIMENT** : détecte une modification ou une suppression de ligne **après coup**, en relisant le fichier avec l'outil dédié — le coût de falsifier une ligne sans se faire détecter n'est plus nul (il faut recalculer cette ligne ET toutes celles qui suivent).

**Ce qu'il NE garantit PAS** (à ne jamais survendre) :
- **Pas un WORM** : un accès root au disque peut toujours réécrire le fichier ET recalculer une chaîne de hash cohérente de bout en bout.
- **Pas de persistance de l'état de chaînage entre deux redémarrages du processus** (limite v1 assumée) : un redémarrage repart d'un hash "genèse" documenté — la chaîne à l'intérieur d'une même exécution reste vérifiable de bout en bout, mais ne relie pas deux exécutions séparées par un redémarrage.
- **Pas une signature cryptographique externe** : aucun ancrage tiers de confiance.
- **Étendu à 2 composants sur 9 seulement** — extension aux 7 autres = répétition à l'identique du câblage documenté, non faite à ce jour.

---

## 3. Hors périmètre — à ne PAS compter comme preuve collectée

- **Observabilité (§8·I d'AUDIT_SGFE.md) : toujours à zéro, jamais entamée.** Aucun `TracerProvider`, aucune route `/metrics`, aucun log JSON structuré avec `trace_id` cross-service. **C'est, seul, le point qui ferait échouer un audit SOC 2 Type II aujourd'hui (CC7 — surveillance/détection d'incident)**, indépendamment de la maturité de toutes les preuves listées en §1/§2.
- **Reporting Service** : agrégateur strictement read-only — n'a jamais reçu d'`AuditLog` par construction (conception §10.7), rien à collecter ici, ce n'est pas un oubli.
- **Notification Service** : hors périmètre de la conception §10.7 — pas d'`AuditLog`, rien à collecter.
- **Immuabilité DB réelle sur Campagne/Config/Auth/Abonné** : non effective (voir §2.2) — seule l'immuabilité applicative tient pour ces 4 services.
- **Chaînage de hash tamper-evident sur les 7 autres composants** (abonné, campagne, config, facturation, notification, paiement, reporting) : ces composants écrivent toujours des fichiers de log locaux sans aucune garantie d'intégrité — état identique à celui décrit dans `AUDIT_SGFE.md` §J avant cette PR, non changé pour eux.
- **Persistance de la chaîne de hash entre redémarrages** : non implémentée (v1, limite documentée en §2.5).
- **Test de pénétration** avant mise en production : jamais fait (item P0 de la checklist §8).
- **mTLS inter-services, gestion des secrets, autres contrôles OWASP/ASVS/SOC 2** : couverts par `docs/CONFORMITE_SOC2_OWASP.md`, pas répétés ici — ce document est scopé à la piste d'audit et à la journalisation de sécurité uniquement.

---

## 4. Ce que cette déclaration signifie concrètement pour une future période d'observation

En datant précisément le début de collecte de chaque contrôle, ce document permet à un futur auditeur de calculer, à toute date ultérieure, la durée de période d'observation effectivement disponible pour chacun :

- **Depuis le 04/09/2026** (le plus ancien de ce périmètre) : `AuditLog` Paiement/Facturation, rétention/horodatage des 9 composants.
- **Depuis le 05/09/2026** : `AuditLog` Config/Campagne/Auth/Abonné, immuabilité DB réelle Paiement/Facturation, centralisation des événements de sécurité gateway→Auth, chaînage de hash Auth+Gateway.

**À la date de rédaction, aucun de ces contrôles n'a accumulé une période d'observation proche des 3 mois généralement attendus pour un Type II** — cette déclaration marque le point de départ de l'horloge, pas son terme. Le verrou réel et immédiat reste, sans changement, l'absence totale d'observabilité (§3) : un Type II échouerait aujourd'hui sur ce seul point, quelle que soit la maturité future des contrôles de piste d'audit listés ici.

---

## 5. Méthode

Chaque date de ce document est vérifiée directement dans le dépôt (pas dans la documentation narrative) : en-tête de migration Django (`# Generated by Django ... on <date>`) pour les modèles `AuditLog` et leurs migrations d'immuabilité, code source pour les mécanismes (`db_hardening.py`, `log_integrity.py`, `context.py`), et recoupement avec les numéros de PR déjà cités dans `AUDIT_SGFE.md` §J. Rédigé le 5 septembre 2026, dans le cadre de la PR qui livre les deux derniers contrôles listés en §2.4 et §2.5.
