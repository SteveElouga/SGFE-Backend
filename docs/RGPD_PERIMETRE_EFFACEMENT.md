# Périmètre réel du droit à l'effacement (RGPD, article 17)

> Constat de départ (audit « Radiographie SGFE », recoupé par
> `AUDIT_SGFE.md` et `docs/CONFORMITE_SOC2_OWASP.md` §3.3 V14/§4 item 6) :
> le droit à l'effacement n'était tranché noir sur blanc que pour 2 services
> sur 9 (`auth`, `abonne`). Ce document documente, **service par service**,
> ce qui est anonymisé, ce qui est délibérément conservé et pourquoi — la
> clarification que l'audit réclamait comme « actuellement implicite, jamais
> tranché noir sur blanc ».

## 1. La règle, une fois pour toutes

Le droit à l'effacement (RGPD article 17) **ne signifie jamais supprimer ou
vider des pièces comptables/légales que la loi impose de conserver**
(factures, paiements, preuves de transaction). L'article 17 lui-même le
prévoit : le §3(b) écarte le droit à l'effacement « dans la mesure où le
traitement est nécessaire […] pour respecter une obligation légale qui
requiert le traitement » — en l'occurrence les obligations fiscales et
comptables de conservation des pièces justificatives (au Cameroun comme dans
la plupart des juridictions, plusieurs années après l'opération).

Le patron déjà en place dans `auth`/`abonne` (voir §2) applique cette règle
correctement : il **anonymise** les champs directement identifiants (nom,
téléphone, adresse, e-mail — remplacés par un placeholder explicite, jamais
vidés silencieusement), et ne touche **jamais** à la ligne elle-même, ni aux
montants/dates/statuts qui ont une valeur comptable ou d'historique métier.
Ce document réplique cette même distinction sur les 7 services qui
n'avaient encore jamais été examinés sous cet angle.

**Ce qui compte comme « directement identifiant »** dans ce document : un
champ qui stocke, **en propre, dans la table d'un service**, une donnée
nominative d'un abonné ou d'un utilisateur interne (nom, prénom, téléphone,
adresse, e-mail, ou un texte libre qui les embarque). Une simple clé
étrangère opaque vers un autre service (`abonne_id`, `agent_id`, `created_by`
— un UUID sans signification lisible en dehors du service propriétaire) n'en
est **pas une** : la supprimer ou la vider casserait la traçabilité
comptable/métier sans retirer la moindre information nominative, puisque le
service propriétaire de cette identité (abonne/auth) a lui-même déjà
anonymisé la sienne.

## 2. Le patron de référence — `auth` et `abonne`

| | `abonne` (`AbonneService.anonymiser_abonne`) | `auth` (`UserAdminService.anonymiser_utilisateur`) |
|---|---|---|
| Fichier | `services/abonne/abonnes/services.py` | `services/auth/comptes/services.py` |
| Déclenché par | RPC `AnonymiserAbonne` (gRPC), mutation gateway `anonymiserAbonne` (ADMIN) | RPC `AnonymiserUtilisateur` (gRPC), mutation gateway `anonymiserUtilisateur` (ADMIN) + purge automatique quotidienne (`purge_rgpd_job`, 3 ans après désactivation) |
| Précondition | Statut `RESILIE` uniquement (sinon `ValidationError`) | Compte désactivé (`is_active=False`) uniquement (sinon `ValueError`) |
| Champs anonymisés | `nom`, `prenom`, `telephone_whatsapp`, `adresse` | `username`, `email`, `phone_number` |
| Valeurs de remplacement | Constantes **explicites** (`NOM_ANONYMISE = "Abonné anonymisé"`, `PRENOM_ANONYMISE = "(RGPD)"`, `TELEPHONE_ANONYMISE = "+00000000000"`, `ADRESSE_ANONYMISEE = "Adresse supprimée (RGPD)"`) | Préfixes **dérivés de l'UUID** (`PREFIXE_USERNAME_ANONYMISE = "utilisateur-anonymise-"`, `PREFIXE_TELEPHONE_ANONYMISE = "+000"`) — nécessaire ici car `username`/`phone_number` sont `unique=True` en base, contrairement à l'Abonné où une valeur littérale suffit |
| Champs préservés | `id`, `numero_abonne`, `statut`, compteur, historique — et tout ce qui vit dans un AUTRE service (factures, paiements) | `id`, `role`, `AuditLog` (jamais réécrit ni supprimé — « chantier séparé », même principe que « jamais les factures/paiements ») |
| Idempotence | Oui — réappliquer les mêmes valeurs ne lève pas d'erreur | Oui — dérivées de `user.id`, stables |

C'est ce patron exact (nommage des méthodes `anonymiser_*`, constantes de
remplacement explicites nommées `*_ANONYMISE(E)`, précondition sur un état
terminal, idempotence, jamais de suppression de ligne) qui a été répliqué
pour `notification` (§3.4) et qui sert de référence à l'analyse des 6 autres
services.

## 3. Périmètre réel, service par service

### 3.1 `campagne` — rien à anonymiser, déjà conforme par conception

**Constat** : `campagne` ne stocke, sur aucune de ses tables
(`Campagne`, `CampagneAgent`, `Releve`, `ReleveAudit`, `AffectationZone`,
`RegenerationFactureEnAttente`, `AuditLog` — voir
`services/campagne/campagnes/models.py`), **aucun champ nominatif propre à
un abonné** — uniquement des références opaques (`abonne_id`, `agent_id`,
`created_by`, `demande_par`, tous des UUID/chaînes en `CharField(max_length=36)`
sans signification lisible en dehors d'Abonné Service/Auth Service).

Le seul champ nominatif de tout ce service est `ReleveAudit.auteur_username`
(snapshot du nom d'utilisateur d'un AGENT/SUPERVISEUR interne au moment
d'une saisie/correction d'index) et `AuditLog.acteur_nom` (même snapshot,
journal transverse). Les deux sont des journaux d'audit internes
(traçabilité « qui a fait quoi »), volontairement **hors périmètre** —
même principe que l'`AuditLog` d'`auth` lui-même, qui n'est pas réécrit par
`anonymiser_utilisateur` (« chantier séparé, même principe que jamais les
factures/paiements côté conservation légale », `services/auth/comptes/services.py`).
Un journal d'audit métier immuable (voir `AUDIT_SGFE.md` §10.7, migrations
`0010_audit_log_immutable`/`0011_audit_log_role_runtime`) sert la preuve
d'accountability (SOC 2 CC7.2/CC7.3) : il n'est pas la donnée personnelle
d'un tiers externe (l'abonné), mais l'historique d'action d'un agent interne
dont le propre effacement se traite dans `auth`, jamais par ricochet dans
les services qui l'ont seulement vu passer.

Un abonné anonymisé garde donc ses relevés intacts dans `campagne`
(`abonne_id` opaque, index, dates, quartier/camp) — c'est le comportement
attendu : rien de nominatif n'y a jamais été stocké.

**Décision : aucun mécanisme d'anonymisation nécessaire.**

### 3.2 `facturation` — rien à anonymiser, déjà conforme par conception

**Constat** : `Facture` (voir `services/facturation/factures/models.py`)
référence l'abonné uniquement par `abonne_id` (opaque) et porte un champ
`numero_mobile_money` — vérifié dans `campagnes/services.py`/
`factures/services.py` : c'est le numéro Mobile Money **de la campagne**
(saisi une fois par le créateur de la campagne, copié tel quel sur chaque
facture générée pour indiquer où payer), **pas** le numéro personnel de
l'abonné qui reçoit la facture. Aucune PII abonné en propre.

`Facture.annulee_par` (`max_length=150`, un nom d'utilisateur interne) est
en revanche **une pièce constitutive de la facture elle-même** : c'est la
trace, sur le document comptable, de qui a autorisé son annulation et
pourquoi (`motif_annulation`) — exactement le type de champ que la règle
du §1 protège explicitement (obligation de conservation intégrale des
pièces comptables, y compris leur propre piste d'audit). Le vider serait
retirer une information que la loi impose de garder sur la facture, pas
anonymiser une PII d'abonné.

`AuditLog.acteur_nom` : même raisonnement qu'en §3.1 (journal d'audit
interne, hors périmètre).

**Décision : aucun mécanisme d'anonymisation nécessaire.**

### 3.3 `paiement` — rien à anonymiser, déjà conforme par conception

**Constat** : `Paiement`, `SoldeFacture`, `AvoirAbonne`, `MouvementAvoir`,
`SessionPaiementEnLigne`, `SuiviImpaye` (voir
`services/paiement/paiements/models.py`) ne référencent l'abonné que par
`abonne_id` (opaque). `enregistre_par`/`annule_par`/`cree_par` sont des
identifiants Auth Service (`CharField(max_length=36)`, un UUID — jamais un
nom en clair, à la différence de `Facture.annulee_par` côté facturation).
Aucune PII abonné ou tiers en propre nulle part dans ce service.

Ce service est, par nature, celui où la règle du §1 pèse le plus lourd :
chaque ligne (`Paiement`, `SoldeFacture`…) **est** une pièce comptable —
preuve d'un encaissement, d'un solde, d'un avoir. Même si ce service avait
stocké une PII abonné (ce qui n'est pas le cas), la règle interdirait de
toute façon d'y toucher sur ces tables précises. Il se trouve qu'aucune
anonymisation n'est même nécessaire pour l'atteindre : la conception
actuelle (référence opaque uniquement) est déjà conforme.

**Décision : aucun mécanisme d'anonymisation nécessaire — et, par construction,
aucun ne serait de toute façon licite sur les montants/dates/statuts de ce
service (article 17.3.b RGPD + obligations de conservation comptable).**

### 3.4 `notification` — anonymisation implémentée (le vrai écart de l'audit)

**Constat** : c'est le seul service, hors `abonne`/`auth`, qui stocke
réellement une PII abonné en propre :

- `Envoi.telephone` / `DiffusionEnvoi.telephone` (voir
  `services/notification/notifications/models.py`) : chiffrées au repos
  (`EncryptedCharField`, `notifications/fields.py`), mais avec la clé Fernet
  **propre à Notification Service** (`PII_ENCRYPTION_KEY`) — donc toujours
  déchiffrables après que le même numéro a été anonymisé côté Abonné
  Service (qui a sa propre clé, distincte). C'est exactement l'écart décrit
  par le constat de départ : « ses envois WhatsApp encore déchiffrables par
  leur propre clé ».
- `Envoi.dernier_message` : texte en clair qui embarque le prénom/nom de
  l'abonné au moment de l'envoi (`prenom_nom` interpolé dans le corps du
  message, voir `notifications/message_builder.py` et
  `EnvoiService._tenter_envoi`).

**Mécanisme implémenté** (même patron qu'`auth`/`abonne`, §2) :

- `EnvoiService.anonymiser_envois_abonne(abonne_id)` — anonymise
  `telephone` (`TELEPHONE_ANONYMISE = "+00000000000"`, valeur identique à
  celle d'Abonné Service : même nature de donnée) et `dernier_message`
  (`DERNIER_MESSAGE_ANONYMISE = "Message supprimé (RGPD)"`) sur tous les
  `Envoi` de l'abonné. Laisse `dernier_message` vide s'il l'était déjà (rien
  à effacer sur un envoi jamais tenté).
- `DiffusionService.anonymiser_envois_abonne(abonne_id)` — anonymise
  `telephone` sur tous les `DiffusionEnvoi` de l'abonné. `Diffusion.message`
  n'est **jamais** personnalisé par abonné (texte libre commun à tous les
  destinataires visés) : rien à y anonymiser pour un abonné précis.
- Précondition : ce service ne possède pas lui-même le statut de l'abonné
  (il n'a pas de colonne `statut`) — il interroge Abonné Service
  (`abonne_client.get_abonne`) et refuse (`ValueError`) si le statut n'est
  pas `RESILIE`, ou si Abonné Service est injoignable (échec fermé : pas de
  vérification possible, pas d'anonymisation). Même garde-fou que
  `AbonneService.anonymiser_abonne`, vérifié à distance faute d'état local.
- Exposé par le RPC `AnonymiserEnvoisAbonne` (`proto/notification_service.proto`),
  appelé par le servicer `NotificationServiceServicer.AnonymiserEnvoisAbonne`.
- **Cascade côté gateway** : `AbonneMutations.anonymiser_abonne`
  (`gateway/schema/abonne_mutations.py`) appelle désormais
  `notification_client.anonymiser_envois_abonne` juste après avoir anonymisé
  l'abonné lui-même — best-effort (un Notification Service injoignable ne
  fait pas échouer la mutation, qui a déjà anonymisé l'essentiel côté
  Abonné Service ; un admin peut relancer l'opération, idempotente, une fois
  le service de nouveau joignable). Sans cette cascade, le nouveau
  mécanisme existerait mais ne serait jamais appelé en pratique — la
  gateway reste le seul point d'orchestration RBAC de ce dépôt.

Préservés intacts : `facture_id`, `paiement_id`, `type_envoi`, `statut`,
`tentatives`, `created_at`, `erreur` — pas des pièces comptables, mais rien
ne justifie non plus de les effacer, et les garder conserve un historique de
support exploitable (nombre de tentatives, date, type de message).
`TokenAcces` n'a aucune PII propre (juste `abonne_id`, `facture_id`, un
token UUID, des dates) : non touché.

**Décision : mécanisme implémenté** — voir
`services/notification/notifications/services.py`
(`EnvoiService.anonymiser_envois_abonne`,
`DiffusionService.anonymiser_envois_abonne`),
`services/notification/notifications/repositories.py`
(`EnvoiRepository.list_by_abonne`, `DiffusionRepository.list_envois_by_abonne`),
`services/notification/notifications/grpc_server.py`
(`AnonymiserEnvoisAbonne`), `proto/notification_service.proto`, et la
cascade `gateway/schema/abonne_mutations.py`/`gateway/schema/grpc_clients.py`.
Tests : `services/notification/notifications/tests/test_anonymisation_rgpd.py`,
`services/notification/notifications/tests/test_grpc.py::TestAnonymiserEnvoisAbonneRPC`,
`gateway/schema/tests/test_abonne.py` (cascade + dégradation gracieuse).

### 3.5 `reporting` — rien à anonymiser, déjà conforme par conception

**Constat vérifié, pas supposé** : `StatsCampagne`, `StatsFacturation`,
`StatsPaiements`, `ProcessedEvent` (voir
`services/reporting/stats/models.py`) sont des tables **dénormalisées,
agrégées par `campagne_id`** — aucune ne porte de colonne `abonne_id`, ni
aucune donnée nominative. C'est un agrégateur strictement read-only côté
CQRS (ADR-019), alimenté par des compteurs/sommes (`total_abonnes`,
`nb_releves`, `montant_total_facture`…), jamais par une identité
individuelle. Confirmé également par `proto/reporting_service.proto` : tous
les RPC exposés (`GetDashboard`, `GetStatsCampagne`, `GetStatsGlobales`…)
raisonnent par campagne, jamais par abonné.

**Décision : aucun mécanisme d'anonymisation nécessaire — rien à anonymiser
ici, déjà conforme par conception.**

### 3.6 `config` — rien à anonymiser, déjà conforme par conception

**Constat** : `InfosSociete` (`services/config/parametres/models.py`) porte
bien `nom`/`adresse`/`telephone`, mais ce sont les coordonnées **de la
société exploitante elle-même** (singleton `id=1`, affiché sur les PDF de
factures) — la donnée du responsable de traitement, pas celle d'une
personne physique identifiable au sens de l'article 4(1) RGPD. Hors
périmètre du droit à l'effacement par nature, comme pour n'importe quelle
mention légale d'en-tête de facture.

`ConfigParam` est un magasin clé/valeur générique de paramètres opérationnels
(délais de relance, activation de fonctionnalités…), dont une clé,
`email_admin_notifications`, peut contenir une adresse e-mail. Ce n'est
cependant **pas un enregistrement lié à un identifiant de personne** qu'une
demande d'effacement viserait : c'est un réglage que n'importe quel ADMIN
peut corriger à tout moment par la mutation `SetConfigParam` déjà existante
(pas de mécanisme dédié à inventer — le canal de correction normal suffit,
contrairement à `Envoi.telephone` en §3.4, qui n'a aucun autre canal de
mise à jour une fois l'envoi historisé).

`AuditLog.acteur_nom` : même raisonnement qu'en §3.1 (journal d'audit
interne, hors périmètre).

**Décision : aucun mécanisme d'anonymisation nécessaire.**

### 3.7 `gateway` — hors sujet structurel (vérifié, pas supposé)

**Constat** : `gateway/gateway/settings.py` déclare explicitement
`DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}` — ce
composant n'a et ne peut avoir aucune base de données propre, donc aucune
table, donc aucune PII stockée en propre à anonymiser. C'est une gateway
GraphQL qui compose des appels gRPC vers les 8 autres services (RBAC +
agrégation), sans persistance locale (le seul usage de Redis constaté,
`schema/subscriptions.py`, est du pub/sub pour les abonnements GraphQL
temps réel — pas un magasin de données).

Le rôle légitime de `gateway` dans le périmètre RGPD n'est donc pas
l'anonymisation de données qu'il ne possède pas, mais l'**orchestration** :
c'est le seul point qui compose plusieurs appels gRPC sous un seul contrôle
RBAC (ADMIN uniquement). C'est la raison d'être de la cascade décrite en
§3.4 : `AbonneMutations.anonymiser_abonne` déclenche désormais, en plus de
l'anonymisation côté Abonné Service, celle des envois WhatsApp côté
Notification Service — sans quoi le nouveau mécanisme de `notification`
resterait implémenté mais jamais appelé en pratique.

**Décision : aucune anonymisation propre à `gateway` (pas de base de
données) ; contribution au périmètre RGPD via l'orchestration de la
cascade `notification` (§3.4), pas via un mécanisme d'anonymisation
supplémentaire.**

## 4. Tableau récapitulatif

| Service | PII propre stockée | Mécanisme d'anonymisation | Statut |
|---|---|---|---|
| `auth` | `username`, `email`, `phone_number` | `UserAdminService.anonymiser_utilisateur` + purge auto (3 ans) | ✅ Fait (PR #213, préexistant) |
| `abonne` | `nom`, `prenom`, `telephone_whatsapp`, `adresse` | `AbonneService.anonymiser_abonne` | ✅ Fait (PR #179, préexistant) |
| `campagne` | Aucune (hors journal d'audit interne, hors périmètre) | — | ✅ Déjà conforme par conception |
| `facturation` | Aucune (hors piste d'audit de la facture elle-même, protégée) | — | ✅ Déjà conforme par conception |
| `paiement` | Aucune | — | ✅ Déjà conforme par conception |
| `notification` | `Envoi.telephone`, `Envoi.dernier_message`, `DiffusionEnvoi.telephone` | `EnvoiService.anonymiser_envois_abonne` + `DiffusionService.anonymiser_envois_abonne` | ✅ **Nouveau — implémenté ici** |
| `reporting` | Aucune (agrégateur read-only, zéro `abonne_id`) | — | ✅ Déjà conforme par conception |
| `config` | Aucune PII de personne physique liée à un identifiant (infos société = responsable de traitement) | — | ✅ Déjà conforme par conception |
| `gateway` | Aucune (pas de base de données propre — `ENGINE: dummy`) | — (orchestration de la cascade `notification`) | ✅ Hors sujet structurel |

## 5. Ce qui reste délibérément hors périmètre (limite assumée, pas un oubli)

- **Les journaux d'audit internes** (`AuditLog.acteur_nom`,
  `ReleveAudit.auteur_username` dans `campagne`/`facturation`/`paiement`/
  `config`/`auth`) ne sont anonymisés nulle part, y compris dans `auth`
  lui-même pour son propre `AuditLog`. C'est un choix de conception déjà
  pris et documenté (`services/auth/comptes/services.py` :
  « jamais réécrit ni supprimé ici, même principe que jamais les
  factures/paiements ») — pas une extension au-delà du périmètre de cette
  mission. Ils ne portent d'ailleurs aucune identité d'abonné (vérifié
  §3.1/3.2/3.3 : `detail=` ne référence jamais que des `abonne_id` opaques,
  jamais un nom/téléphone).
- **Les fichiers de logs applicatifs** (rotation locale,
  `TimedRotatingFileHandler`, voir `docs/CONFORMITE_SOC2_OWASP.md` §J) sur
  les 9 composants peuvent transitoirement contenir des fragments de PII
  dans un message de log (ex. un numéro de téléphone dans un message
  d'erreur WhatsApp). Aucune politique de purge/rédaction de logs n'existe
  dans ce dépôt, sur aucun service — sujet transverse d'observabilité, hors
  périmètre d'un mécanisme d'anonymisation par table.
- **PDF déjà générés** (`Facture.pdf_path`, reçus WhatsApp) : contiennent
  nécessairement l'identité de l'abonné au moment de l'émission (c'est leur
  fonction). Ce sont eux-mêmes des pièces comptables/preuves de transaction
  au sens du §1 — non couverts par un droit à l'effacement, pour la même
  raison qu'une facture papier archivée ne l'est pas.
