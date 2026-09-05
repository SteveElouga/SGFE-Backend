"""Chaînage de hash pour rendre les logs locaux tamper-evident — voir
AUDIT_SGFE.md §J, "Journalisation de sécurité centralisée et inviolable".

## Le problème

Le bloc `LOGGING` de chaque composant (PR #193, 9 composants) écrit des
fichiers locaux (`TimedRotatingFileHandler`) sans aucune garantie
d'intégrité : n'importe qui avec un accès au disque peut éditer ou supprimer
une ligne sans laisser de trace détectable. "Inviolable" restait donc
purement déclaratif.

## Le mécanisme retenu : chaînage de hash SHA-256, façon "blockchain de log"

`ChainedHashFormatter` enveloppe le `Formatter` standard : pour chaque
enregistrement émis, il calcule

    hash_n = sha256(hash_(n-1) + ligne_formatee_n)

et l'ajoute en suffixe de la ligne (` log_hash=<hex>`). `hash_(n-1)` est
gardé en mémoire, dans l'instance du formatter — PAS dans le message
lui-même, pour qu'un attaquant qui ne connaît que le contenu du fichier ne
puisse pas reconstruire la chaîne sans rejouer chaque étape depuis le début.

Propriété obtenue : modifier ou supprimer une ligne casse le calcul de hash
de TOUTES les lignes qui la suivent dans le fichier — un futur auditeur qui
relit le fichier avec `verifier_chaine_logs.py` (même dossier) détecte
immédiatement à quelle ligne la chaîne a été rompue.

## Ce que ça garantit VRAIMENT (à ne jamais survendre)

- Détecte une modification ou une suppression de ligne **après coup**, en
  relisant le fichier avec l'outil de vérification dédié.
- Le coût de falsifier une ligne sans se faire détecter n'est plus nul : un
  attaquant doit recalculer la ligne modifiée ET toutes celles qui suivent,
  jusqu'à la fin du fichier — silencieusement, un simple éditeur de texte ne
  suffit plus.

## Ce que ça NE garantit PAS

- **Pas un WORM** (Write Once Read Many) : rien n'empêche physiquement une
  écriture sur le fichier. Un attaquant avec un accès root/au disque peut
  toujours réécrire le fichier ET recalculer une chaîne de hash cohérente de
  bout en bout — cette bibliothèque rend la falsification plus coûteuse et
  moins silencieuse, elle ne l'empêche pas.
- **Pas de persistance de l'état de chaînage entre deux redémarrages du
  processus** (limite v1, assumée plutôt que sur-ingénierée) : `_previous_hash`
  vit en mémoire process. Un redémarrage repart du `GENESIS_HASH` documenté
  ci-dessous — la chaîne à l'intérieur d'une même exécution reste vérifiable
  de bout en bout, mais elle ne relie pas deux exécutions séparées par un
  redémarrage. Un renforcement futur raisonnable serait de persister le
  dernier hash connu (fichier à côté, ou table dédiée) et de le relire au
  démarrage — explicitement hors périmètre de cette version.
- **Pas une signature cryptographique externe** : aucune clé privée, aucun
  tiers de confiance. Une vraie preuve inviolable au sens strict exigerait un
  ancrage externe (ex. horodatage/signature envoyés vers un système que
  l'attaquant ne contrôle pas) — hors périmètre ici, volontairement, pour
  rester proportionné à la demande (un mécanisme "simple et générique").
- Ne couvre que les enregistrements qui passent par ce formatter — un
  composant qui écrit ailleurs (`print`, un autre logger non câblé) n'est pas
  concerné.

## Portée de cette PR : câblé sur 2 composants, pas 9

Câblé uniquement sur les deux points d'entrée les plus sensibles pour la
sécurité — Auth (`services/auth/comptes/log_integrity.py`) et Gateway
(`gateway/schema/log_integrity.py`). Étendre aux 7 autres composants est une
répétition à l'identique du câblage ci-dessous (voir "Comment un futur
service adopte ce mécanisme") — délibérément non fait ici, hors de
proportion pour cette tâche : 2 composants suffisent comme preuve du
mécanisme.

## Pourquoi un `Formatter`, pas un `Filter`, et pourquoi seulement le handler "file"

Un `logging.Filter` attaché à un logger nommé (`security`, `__name__`...) ne
s'applique qu'aux enregistrements qui transitent PAR ce logger précis, pas à
ceux qui remontent depuis des loggers enfants vers les handlers du logger
racine (`Logger.callHandlers` appelle directement les handlers de chaque
ancêtre, sans repasser par leurs filtres à eux). Un `Formatter`, lui, est
invoqué par CHAQUE `Handler.emit()`, donc par construction pour tout
enregistrement qui atteint ce handler, quel que soit le logger d'origine —
plus simple et plus fiable ici.

Ce formatter n'est câblé QUE sur le handler "file" (jamais "console") : deux
handlers qui partageraient la MÊME instance de formatter verraient chacun
`format()` appelé pour un même enregistrement, ce qui ferait avancer l'état
`_previous_hash` deux fois par ligne et casserait la lisibilité de la chaîne
du fichier prise isolément (elle dépendrait alors d'un hash calculé pour la
sortie console, invisible dans le fichier). Le fichier est la cible réelle de
la preuve d'intégrité (la console d'un conteneur n'est pas l'artefact qu'un
auditeur relit) ; il suffit donc de donner au handler "file" un nom de
formatter dédié, distinct de celui de "console" (voir settings.py d'Auth et
de la Gateway pour l'exemple de câblage).

## Comment un futur service adopte ce mécanisme

1. Ajouter la destination du service à `scripts/sync-log-integrity-lib.sh`
   (tableau `DESTINATIONS`), puis lancer `./scripts/sync-log-integrity-lib.sh`.
2. Dans le bloc `LOGGING` du `settings.py` du service, ajouter un formatter
   dédié pour le handler "file" (jamais "console") :

   ```python
   "formatters": {
       "iso8601": {...},  # inchangé, pour "console"
       "iso8601_chained": {
           "()": "<app>.log_integrity.ChainedHashFormatter",
           "format": "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
           "datefmt": "%Y-%m-%dT%H:%M:%S",
       },
   },
   ```

   puis référencer `"formatter": "iso8601_chained"` sur le handler "file"
   (au lieu de `"iso8601"`).
3. Lancer `./scripts/sync-log-integrity-lib.sh --check` (déjà en CI, job
   `check-log-integrity-lib-drift`) pour confirmer l'absence de dérive.

## Pourquoi pas un vrai package Python importé

Même choix, et pour les mêmes raisons, que `sgfe_common.grpc_auth` et
`sgfe_common.db_hardening` — voir `libs/sgfe_common/README.md`. En bref : le
contexte de build Docker de chaque service est scopé à son seul dossier,
donc aucun `Dockerfile` ne peut voir `libs/sgfe_common/` au moment du build.
Ce fichier reste la source canonique, recopiée telle quelle par
`scripts/sync-log-integrity-lib.sh` (même mécanique de bandeau + vérification
de hash que les deux scripts de synchronisation existants).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading

# Valeur de "hash précédent" au tout début d'une chaîne (démarrage du
# processus, ou premier enregistrement jamais émis par ce formatter). Valeur
# arbitraire mais FIXE et documentée — ce qui compte est qu'elle soit connue
# à l'avance par `verifier_chaine_logs.py` pour pouvoir vérifier la toute
# première ligne d'un segment de log démarré à froid.
GENESIS_HASH = "0" * 64

# Suffixe ajouté par `ChainedHashFormatter.format()` : ` log_hash=<64 hex>`
# en fin d'enregistrement (le `re.DOTALL` couvre le cas d'un enregistrement
# multi-lignes, ex. une trace d'exception jointe par `exc_info=True` — tout
# l'enregistrement, retours à la ligne compris, est haché comme un seul bloc).
# Partagé avec `verifier_chaine_logs.py` : NE PAS dupliquer ce pattern
# ailleurs, une dérive entre les deux casserait la vérification en silence.
LOG_HASH_SUFFIX_RE = re.compile(r"^(?P<content>.*) log_hash=(?P<hash>[0-9a-f]{64})$", re.DOTALL)


class ChainedHashFormatter(logging.Formatter):
    """`Formatter` qui chaîne un hash SHA-256 à chaque ligne de log émise.

    Voir la docstring de ce module pour la conception complète, ce que ça
    garantit et ce que ça ne garantit pas. Utilisation : référencer cette
    classe via la syntaxe `"()"` de `logging.config.dictConfig` sur le
    formatter du handler de fichier UNIQUEMENT (jamais sur "console" — voir
    la docstring du module pour la raison).

    Une instance = une chaîne indépendante. Deux handlers qui utiliseraient
    la MÊME instance verraient leur état interférer (voir docstring du
    module) — `dictConfig` crée une instance par NOM de formatter, donc
    donner un nom de formatter dédié au handler "file" suffit à garantir
    l'isolation.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._previous_hash = GENESIS_HASH
        # Défense en profondeur : `Handler.handle()` sérialise déjà les
        # appels à `emit()` (donc à `format()`) via le verrou propre du
        # handler, mais un verrou dédié ici documente explicitement
        # l'exigence d'atomicité de la mise à jour read-modify-write de
        # `_previous_hash`, sans dépendre d'un détail d'implémentation du
        # module `logging` qui pourrait changer.
        self._lock = threading.Lock()

    def format(self, record: logging.LogRecord) -> str:
        """Formate `record` normalement, puis ajoute `log_hash=<hex>` en
        suffixe — hash de `hash_precedent + ligne_formatee`."""
        base_line = super().format(record)
        with self._lock:
            digest = hashlib.sha256((self._previous_hash + base_line).encode("utf-8")).hexdigest()
            self._previous_hash = digest
        return f"{base_line} log_hash={digest}"
