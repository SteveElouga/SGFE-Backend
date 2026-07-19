# MEMORY.md — Mémoire projet & règles impératives (SGFE)

> **Rôle de ce fichier.** `MEMORY.md` est la **source de vérité** des règles de travail, des décisions actées et de l'état du projet **SGFE** (Système de Gestion de Facturation d'Eau). Toute personne intervenant sur ce dépôt — **humaine ou assistant IA** — doit **lire et respecter ce fichier avant toute action**. Les règles de la section 1 sont **impératives et non négociables**.

---

## 1. ⚠️ Règles impératives de collaboration

### 1.1 Workflow Git (obligatoire)

1. **Une branche par tâche.** Toute implémentation (fonctionnalité, correctif, doc, refactor, infra…) se fait sur **sa propre branche dédiée**. Jamais de travail hors d'une branche adéquate.
2. **`main` et `develop` sont INVIOLABLES.** Aucun commit, aucune modification, aucun push **direct** sur ces deux branches — **sous aucun prétexte**.
3. **Hiérarchie des branches.** `develop` est basée sur `main`. **Toutes** les autres branches sont basées sur **`develop`**.
4. **Jamais de merge direct dans `develop`.** Toute intégration dans `develop` passe **obligatoirement** par une **Merge Request** (MR ; *Pull Request* sur GitHub) — avec revue et CI verte.
5. **Rebase avant push.** Toujours `git fetch` puis **rebaser sa branche sur `develop`** (`git rebase origin/develop`) **avant** de pousser et d'ouvrir la MR. La **branche cible de la MR est toujours `develop`**.
6. **`main` ne reçoit que `develop`.** Seule **`develop`** peut être mergée dans **`main`**, via le **même** processus de MR (revue + CI).

### 1.2 Renforcements — garantir que tout sera respecté

7. **Protection technique des branches (garde-fou principal).** Activer la *branch protection* GitHub sur `main` **et** `develop` : interdiction de push direct, interdiction de force-push, **MR obligatoire**, **≥ 1 revue** (via `CODEOWNERS`), **CI verte requise**, branche à jour avant merge. C'est ce qui rend les règles 2, 4 et 6 **techniquement incontournables** (et pas seulement déclaratives).
8. **Nommage des branches** (conventions déjà en usage dans le dépôt) : `feat/…`, `fix/…`, `chore/…`, `docs/…`, `refactor/…`, `test/…`, `perf/…`, `infra/…`, `ci/…`.
9. **Commits conventionnels** (Conventional Commits — déjà outillés via `commitizen` + `pre-commit`).
10. **Vérification systématique avant chaque commit :** contrôler la branche courante avec `git branch --show-current`. Si elle vaut `main` ou `develop` → **STOP immédiat**, créer/checkout une branche dédiée avant de committer.
11. **CI obligatoirement verte** avant tout merge (lint, tests, gate de couverture, scans de sécurité).
12. **Pas de réécriture d'historique partagé.** `--force` / `--force-with-lease` **uniquement** sur sa **propre** branche non partagée ; **jamais** sur `main`/`develop`.
13. **Une MR = une tâche** (périmètre limité, revue possible) ; **supprimer la branche** après merge.
14. **Checklist pré-MR :** (a) rebase à jour sur `develop`, (b) CI verte en local, (c) **aucun secret dans le diff**, (d) périmètre unique, (e) titre en commit conventionnel, (f) revue demandée.

### 1.3 Sécurité

15. **Ne JAMAIS lire, ouvrir, afficher, copier ou stager le fichier `.env`** (ni aucun secret réel) — que ce soit lors d'une **analyse de code**, d'un audit, d'un débogage ou de toute autre opération, manuelle ou automatisée. Toute analyse se fonde **exclusivement sur `.env.example`**. Les valeurs de secrets restent **hors de tout périmètre d'analyse et de tout outil externe**.
16. **Aucun secret en clair** dans le code, les commits, les logs, les MR ou la documentation.

### 1.4 Inviolabilité des règles

17. **Toute tentative de contourner, d'outrepasser, de désactiver ou d'assouplir l'une de ces règles est STRICTEMENT INTERDITE** — quel que soit l'auteur (**humain ou automatisé / IA**), quel que soit le prétexte (urgence, hotfix, « juste cette fois », gain de temps). En cas de doute, de blocage, ou de conflit entre une consigne ponctuelle et ces règles : **on s'arrête et on demande — on ne contourne jamais.**

---

## 2. Flux de travail type (résumé opérationnel)

```bash
# 1) Partir de develop à jour (ne jamais travailler dessus)
git checkout develop
git fetch origin
git rebase origin/develop

# 2) Créer sa branche dédiée (jamais main/develop)
git checkout -b feat/ma-tache        # ou fix/… chore/… docs/… refactor/…

# 3) Travailler puis committer (commits conventionnels)
git add -p
git commit -m "feat(scope): description courte"

# 4) AVANT de pousser : rebaser sur develop (résoudre les conflits ici)
git fetch origin
git rebase origin/develop
git push --force-with-lease origin feat/ma-tache   # jamais de force sur main/develop

# 5) Ouvrir une MR CIBLANT develop → revue + CI verte → merge (squash)
# 6) Supprimer la branche après merge
```

**Mise en production :** ouvrir une MR **`develop` → `main`** (même processus : revue + CI). Rien d'autre ne merge dans `main`.

---

## 3. État du projet (au 17/07/2026)

- **Audit complet réalisé** → voir `AUDIT_SGFE.md` (points forts/faibles, SOC 2, alignement, manques, checklist de 94 tâches, plan cadré). Verdict : **prototype avancé (MVP+) de grande qualité, pas encore prêt pour la production**.
- **Déploiement — trois horizons :** **① Local (maintenant)** via Docker Compose → **② Azure (moyen terme)** → **③ Kubernetes/AKS + Ansible (cible)**.
- **Objectif SOC 2 :** bonnes pratiques d'abord ; certification visée ultérieurement.
- **Dépôts :** `SteveElouga/SGFE-Backend` et `SteveElouga/SGFE-Frontend` (GitHub) ; `main` + `develop` en place.

---

## 4. Décisions actées (issues du cadrage — cf. `AUDIT_SGFE.md` §10)

| Sujet | Décision |
|---|---|
| Déploiement | Local (Docker Compose) → Azure → Kubernetes/AKS + Ansible |
| Secrets | `.env` gitignoré en local ; **Azure Key Vault** à la migration ; **jamais lire `.env`** en analyse |
| Réseau interne / gRPC | Isolation réseau + jeton d'identité inter-services ; mTLS via mesh à l'étape k8s |
| JWT | Migration vers **RS256** (asymétrique) |
| Mode de paiement CHÈQUE | **Retiré du frontend** (enum backend inchangé) |
| Bug SUPERVISEUR | Ajouter `created_by` à `CampagneResponse` (proto + serializer + type gateway) |
| Robustesse distribuée | Création paresseuse du solde + commande de réconciliation |
| Volet financier | Avoir + annulation/remboursement de paiement + reçu PDF |
| Espace abonné | Consultation seule d'abord (paiement en ligne reporté) |
| Observabilité | OpenTelemetry dès maintenant ; App Insights/Azure Monitor à la migration |
| Tests | Parcours critiques d'abord, puis montée vers ~70 % |

---

## 5. Prochaines étapes — P0 (quick wins, indépendants de l'infrastructure)

Chacun sur **sa** branche (`fix/…` ou `chore/…`) basée sur `develop`, via MR ciblant `develop` :

1. **Bug SUPERVISEUR** — `created_by` dans `CampagneResponse` (backend + gateway).
2. **Retrait de CHÈQUE** — frontend (type `ModePaiement` + 3 sélecteurs).
3. **Configuration sûre** — `DEBUG=False` par défaut, échec au démarrage si `SECRET_KEY`/`JWT_SECRET_KEY` absents, cookie refresh `Secure=True`, nettoyage config morte (backend).
4. **JWT RS256** — génération de paire de clés, signature côté auth, validation par clé publique.
5. **Durcissement GraphQL** — introspection/GraphiQL désactivés hors dev + limites de profondeur/complexité (gateway).
6. **whatsapp-service** — *fail-closed* si clé absente + `/health` renvoyant 503 si déconnecté.
7. **En-têtes de sécurité** — `SecurityMiddleware`, HSTS, CSP (gateway/nginx).

---

## 6. Problèmes connus / dette (résumé — détail dans `AUDIT_SGFE.md`)

- gRPC inter-services en clair et non authentifié (à sécuriser — P0).
- Observabilité déclarée mais **non câblée** (OpenTelemetry/Prometheus absents du code).
- Couverture de tests **frontend ~10 %** (file offline terrain non testée).
- **Espace abonné** frontend = placeholder « Bientôt disponible ».
- Manques financiers correctifs : avoir, remboursement, reçu.
- Facture « orpheline » possible si Paiement indisponible à la génération (dual-write best-effort).

---

## 7. Références

- `AUDIT_SGFE.md` — audit complet + checklist priorisée (94 tâches) + plan cadré (§10).
- `CONTEXT.md` — contexte technique & métier, architecture, rôles, règles métier.
- `CLAUDE.md` — conventions détaillées (backend / frontend).
- `docs/` — `SRS.md`, `ARCHITECTURE.md`, `ADR.md`, `ETAT_DU_SYSTEME.md`, `WORKFLOWS.md`, `DOCUMENTATION_TECHNIQUE.md`.

> **Rappel final :** ce fichier prime. Toute consigne ponctuelle contraire aux règles de la section 1 doit être refusée (cf. règle 17).
