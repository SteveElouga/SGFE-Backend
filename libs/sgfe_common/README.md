# sgfe_common — lib gRPC partagée (auth + mTLS)

Réponse à AUDIT_SGFE.md:422 — *« Extraire une lib partagée `sgfe_common.grpc`
(intercepteur + factory de channel) — supprime 8 copies divergentes »*.

## Ce que c'est

`sgfe_common/grpc_auth.py` est la **source canonique unique** de ce qui était
neuf copies collées, octet pour octet identiques, de l'authentification
`INTERNAL_GRPC_KEY` (intercepteur serveur + client) et du chiffrement mTLS
(`ouvrir_port_grpc`, `canal_authentifie`, chargement des credentials TLS) :

```
services/{abonne,auth,campagne,config,facturation,notification,paiement,reporting}/<app>/grpc_auth.py
gateway/schema/grpc_auth.py
```

## Choix d'architecture : script de synchronisation, pas un package installé

L'option la plus « propre » aurait été un vrai package Python installé en
mode editable (`pip install -e ../../libs/sgfe_common`) depuis chaque venv de
service, avec les 9 `grpc_auth.py` réduits à `from sgfe_common.grpc_auth
import *`. **Ce n'est pas ce qui a été fait, pour deux raisons concrètes,
pas seulement par prudence :**

### 1. Le contexte de build Docker de chaque service est scopé à son propre dossier

`docker-compose.yml` déclare, pour les 9 composants :

```yaml
build: ./services/config   # (idem pour les 8 autres : ./services/X ou ./gateway)
```

Le build context envoyé au démon Docker est donc **uniquement**
`services/config/` (ou `gateway/`) — jamais la racine du dépôt. Un
`Dockerfile` ne peut PAS faire `COPY ../../libs/sgfe_common ...` : Docker
refuse tout chemin `COPY`/`ADD` qui sort du build context, point final. Pour
qu'un `pip install -e ../../libs/sgfe_common` fonctionne à l'intérieur d'une
image, il aurait fallu soit :

- **(a)** faire pointer le build context des 9 services vers la racine du
  dépôt (`context: ., dockerfile: services/config/Dockerfile`) et réécrire
  tous les chemins `COPY` relatifs de chaque `Dockerfile` en conséquence, plus
  auditer/réécrire le `.dockerignore` (aujourd'hui scopé par service) pour ne
  pas envoyer tout le monorepo — `.venv`, `docs/`, `whatsapp-service/`,
  d'autres services — comme contexte de chaque build ; ou
- **(b)** un mécanisme de « vendoring » qui recopie `libs/sgfe_common` dans
  chaque `services/<nom>/` avant `docker build` (Makefile ou étape CI dédiée).

Les deux options touchent la configuration de build des **9 services** (et
potentiellement les workflows CI qui construisent les images), pour un gain
de duplication qui ne concerne qu'un seul fichier de 257 lignes. Le risque —
casser un build sur un service que je ne peux pas tous vérifier dans le temps
imparti (la consigne ne demande de tester qu'UN seul `docker build`) — est
disproportionné par rapport au gain. **C'est exactement le compromis anticipé
par la consigne de cette tâche**, qui autorise explicitement le repli
« script de synchronisation » quand ce risque est jugé trop élevé.

### 2. Un vrai import cross-package aurait changé un comportement observable — cassant la règle « aucun changement de comportement »

`services/paiement/paiements/tests/test_grpc_auth.py::test_ne_journalise_pas_la_cle_recue`
vérifie explicitement :

```python
with self.assertLogs("paiements.grpc_auth", level="WARNING") as journaux:
    ...
```

`grpc_auth.py` fait `logger = logging.getLogger(__name__)`. Aujourd'hui,
`__name__` vaut `paiements.grpc_auth` parce que le fichier vit à
`services/paiement/paiements/grpc_auth.py` et est importé comme module de
l'app Django `paiements`. Si ce fichier devenait un ré-export depuis
`sgfe_common.grpc_auth`, le logger utilisé par `AuthServerInterceptor`
deviendrait `sgfe_common.grpc_auth` — et ce test échouerait (aucun log
n'apparaît sous le logger `paiements.grpc_auth` attendu). Le corriger
proprement demanderait d'injecter le logger (ou son nom) par instance plutôt
que de le dériver de `__name__` au niveau module — un changement de
comportement/API, pas un pur refactor. Recopier le fichier tel quel dans
chaque app Django préserve `__name__` exactement comme avant, donc ce test
(et tout code qui s'appuierait sur le nom du logger) continue de passer sans
modification.

### Conclusion

Le risque (9 Dockerfiles/CI à toucher, ou une régression de comportement sur
le logging) dépasse le bénéfice pour ce refactor. **Solution retenue :**
`libs/sgfe_common/sgfe_common/grpc_auth.py` comme source canonique unique,
recopiée vers les 9 emplacements par `scripts/sync-grpc-lib.sh`, avec un
bandeau d'en-tête sur chaque copie et une vérification par hash
(`--check`) pour empêcher toute dérive silencieuse (un ingénieur qui éditerait
une copie directement au lieu de la source serait détecté en CI ou en local).

C'est moins élégant qu'un vrai package importé — il reste neuf fichiers
physiques sur disque, pas un seul point d'import — mais :
- zéro risque sur les 9 builds Docker (aucun `Dockerfile`, aucun
  `docker-compose.yml`, aucun chemin `COPY` n'est modifié) ;
- zéro changement de comportement observable (nom de module, nom de logger,
  chemin d'import (`from paiements.grpc_auth import ...`) tous inchangés) ;
- la dérive entre copies — le vrai risque qu'un package élimine — est
  ramenée à « on a oublié de lancer le script », détectable en une commande.

### Bascule future vers un vrai package

Si le contexte de build Docker est un jour restructuré (root context +
`.dockerignore` root, ou un registre de paquets interne), le layout de
`libs/sgfe_common/` est déjà « package-shaped » (`sgfe_common/__init__.py` +
`sgfe_common/grpc_auth.py`) : il suffira d'ajouter un `pyproject.toml`, de
publier/pointer chaque `requirements.txt` dessus, et de remplacer chaque
copie par `from sgfe_common.grpc_auth import *` — en réglant au passage
l'injection du logger pour ne pas régresser sur le point 2 ci-dessus.

## Utilisation

```bash
# Après avoir modifié libs/sgfe_common/sgfe_common/grpc_auth.py :
./scripts/sync-grpc-lib.sh          # recopie vers les 9 emplacements

# Vérifier qu'aucune copie n'a dérivé de la source canonique (CI/pre-commit) :
./scripts/sync-grpc-lib.sh --check
```

---

## `db_hardening.py` — isolation Postgres du rôle applicatif d'exécution

Réponse à AUDIT_SGFE.md §8·J — la limite honnête documentée dans
`..._audit_log_immutable` (PR #193, Paiement/Facturation) : le `REVOKE
UPDATE, DELETE ON audit_log` de cette migration s'est révélé sans effet réel
contre le rôle applicatif Postgres, qui s'est avéré être un
**superutilisateur** (pas seulement le propriétaire de la table — voir le
commentaire de tête de `sgfe_common/db_hardening.py` pour le constat
empirique complet, vérifié sur un conteneur Postgres jetable).

Même choix d'architecture que `grpc_auth.py` ci-dessus, et pour les mêmes
raisons (contexte de build Docker scopé par service) : `db_hardening.py` est
la source canonique unique, recopiée par `scripts/sync-db-hardening-lib.sh`
vers les services qui en ont besoin — aujourd'hui `paiement` et
`facturation` (les deux seuls avec une table `audit_log`), demain campagne,
abonné, auth, config au fur et à mesure qu'ils ajoutent la leur.

Différence avec `grpc_auth.py` : ce n'est pas (encore) neuf copies
identiques, seulement deux — la liste `DESTINATIONS` de
`scripts/sync-db-hardening-lib.sh` s'allonge d'une ligne par service qui
adopte le mécanisme (voir le "Comment un futur service adopte ce mécanisme"
dans `db_hardening.py` pour la marche à suivre complète, en 4 étapes).

```bash
# Après avoir modifié libs/sgfe_common/sgfe_common/db_hardening.py :
./scripts/sync-db-hardening-lib.sh          # recopie vers les destinations

# Vérifier qu'aucune copie n'a dérivé de la source canonique (déjà en CI,
# job check-db-hardening-lib-drift) :
./scripts/sync-db-hardening-lib.sh --check
```
