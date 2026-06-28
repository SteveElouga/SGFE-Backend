# SRS — Spécification des Exigences Logicielles
## Système de Gestion de Facturation d'Eau

> **Norme de référence :** IEEE 830 (Software Requirements Specification)
> **Version :** 1.0.0
> **Date :** Juin 2026
> **Statut :** Validé

---

## Table des matières

1. [Introduction](#1-introduction)
2. [Description générale](#2-description-générale)
3. [Profils utilisateurs](#3-profils-utilisateurs)
4. [Exigences fonctionnelles](#4-exigences-fonctionnelles)
5. [User Stories](#5-user-stories)
6. [Règles métier](#6-règles-métier)
7. [Exigences non fonctionnelles](#7-exigences-non-fonctionnelles)
8. [Interfaces externes](#8-interfaces-externes)
9. [Contraintes](#9-contraintes)
10. [Glossaire](#10-glossaire)

---

## 1. Introduction

### 1.1 Objectif du document

Ce document décrit de manière exhaustive les exigences fonctionnelles et non fonctionnelles du **Système de Gestion de Facturation d'Eau**. Il constitue la référence contractuelle entre les parties prenantes (commanditaires, développeurs, testeurs) et sert de base à la conception, au développement et à la validation du système.

### 1.2 Périmètre du système

Le système digitalise l'intégralité du cycle de facturation d'eau, depuis la gestion des abonnés jusqu'au suivi des paiements, en passant par les campagnes de relevé d'index, la génération de factures PDF et l'envoi automatique via WhatsApp.

**Cycle couvert :**

```
Abonné → Compteur → Campagne de relevé → Facture → Envoi WhatsApp → Paiement → Reporting
```

**Ce qui est hors périmètre :**
- La gestion physique du réseau de distribution d'eau
- La comptabilité générale de l'entreprise
- La gestion des stocks et du matériel
- L'intégration avec des ERP tiers

### 1.3 Définitions et acronymes

| Terme | Définition |
|---|---|
| **Abonné** | Client bénéficiant d'un accès au service d'eau |
| **Compteur** | Appareil de mesure de la consommation d'eau d'un abonné |
| **Index** | Valeur affichée sur le compteur en m³ |
| **Relevé** | Opération de lecture et saisie d'un index |
| **Campagne** | Période organisée de relevé de tous les abonnés actifs |
| **Consommation** | Différence entre le nouvel index et l'ancien index (en m³) |
| **Camp** | Zone géographique précise à l'intérieur d'un quartier, identifiée par un numéro |
| **Ligne d'eau** | Désignation de l'accès au service d'eau d'un abonné |
| **Impayé** | Facture dont le solde restant est supérieur à zéro après la date limite de paiement |
| **Token** | Identifiant unique et temporaire permettant l'accès sécurisé à l'espace abonné |
| **PWA** | Progressive Web App — application web fonctionnant comme une application mobile |
| **gRPC** | Google Remote Procedure Call — protocole de communication inter-services |
| **GraphQL** | Langage de requête pour API, point d'entrée unique |
| **JWT** | JSON Web Token — mécanisme d'authentification sans état |
| **PDF** | Portable Document Format — format de fichier pour les factures |
| **FCFA** | Franc CFA — monnaie utilisée |
| **m³** | Mètre cube — unité de mesure de la consommation d'eau |
| **BSP** | Business Solution Provider — fournisseur d'accès à l'API WhatsApp |
| **ADR** | Architecture Decision Record — document de décision architecturale |
| **SRS** | Software Requirements Specification — ce document |

### 1.4 Public cible

| Public | Utilisation du document |
|---|---|
| Développeurs | Référence d'implémentation |
| Architectes | Base de conception technique |
| Testeurs QA | Critères d'acceptation et cas de test |
| Chef de projet | Suivi et validation du périmètre |
| Utilisateurs finaux | Validation des besoins |

### 1.5 Références

- IEEE 830 — Standard for Software Requirements Specifications
- C4 Model — Simon Brown (architecture)
- Arc42 — Template de documentation architecturale
- RFC 7519 — JSON Web Token (JWT)
- Protocol Buffers v3 — Google (gRPC)
- WhatsApp Business API — Meta Platforms
- Telnyx API Documentation

---

## 2. Description générale

### 2.1 Contexte et positionnement

Le système est destiné à une structure de distribution d'eau gérant jusqu'à **50 abonnés**. Actuellement, les opérations de relevé, facturation et suivi des paiements sont réalisées manuellement, entraînant des erreurs, des retards et une absence de traçabilité.

L'application vise à digitaliser et automatiser l'intégralité de ce processus, en permettant aux agents de terrain de saisir les index depuis leur téléphone mobile, et aux abonnés de recevoir leurs factures directement sur WhatsApp.

### 2.2 Fonctions principales du système

| # | Fonction | Description |
|---|---|---|
| F01 | Gestion des abonnés | Création, modification, suspension et historique des abonnés |
| F02 | Gestion des compteurs | Attribution, remplacement et localisation des compteurs |
| F03 | Campagnes de relevé | Organisation et suivi des relevés d'index mensuels |
| F04 | Saisie mobile | Interface mobile pour les agents de terrain |
| F05 | Génération de factures | Calcul automatique et génération de factures PDF |
| F06 | Envoi WhatsApp | Notification automatique des abonnés via WhatsApp |
| F07 | Espace abonné | Portail web sécurisé pour consulter l'historique |
| F08 | Suivi des paiements | Enregistrement et suivi des règlements |
| F09 | Gestion des impayés | Relances automatiques graduelles |
| F10 | Planification | Programmation des campagnes futures |
| F11 | Tableau de bord | Reporting en temps réel |
| F12 | Configuration | Paramétrage du système |

### 2.3 Environnement d'exploitation

- **Serveur :** MacBook Pro (serveur local)
- **Accès distant :** ngrok (tunnel HTTPS)
- **Orchestration :** Kubernetes + Minikube
- **Réseau :** Internet public (agents terrain, abonnés)
- **Appareils agents :** Smartphones (iOS/Android) via navigateur mobile

---

## 3. Profils utilisateurs

### 3.1 ADMIN

**Description :** Gestionnaire global du système. Accès complet sans restriction.

**Accès :**
- Gestion complète des abonnés et compteurs
- Création et clôture des campagnes de relevé
- Planification des campagnes futures
- Modification du tarif (prix du m³)
- Consultation et génération de toutes les factures
- Enregistrement des paiements
- Envoi et renvoi des notifications WhatsApp
- Consultation de tous les rapports et du tableau de bord
- Création et gestion des utilisateurs (agents, comptables)
- Modification des paramètres système (logo, infos société)
- Configuration des délais de relance impayés
- Révocation des tokens d'accès abonnés

**Contexte d'utilisation :** Bureau, ordinateur de bureau ou portable.

---

### 3.2 AGENT

**Description :** Agent de terrain chargé exclusivement de la saisie des index lors des campagnes de relevé.

**Accès autorisé :**
- Consultation de la liste des abonnés de la campagne en cours
- Saisie du nouvel index pour un abonné
- Ajout d'une observation sur un relevé
- Consultation de la progression de la campagne en cours
- Marquage d'un abonné comme "Non relevé" ou "Estimé"

**Accès refusé :**
- Création ou clôture d'une campagne
- Accès aux factures ou paiements
- Modification des abonnés ou compteurs
- Accès aux paramètres système

**Contexte d'utilisation :** Terrain, smartphone, réseau mobile (parfois instable).

---

### 3.3 COMPTABLE

**Description :** Responsable financier chargé de la facturation et du suivi des paiements.

**Accès autorisé :**
- Consultation de toutes les factures générées
- Enregistrement des paiements (montant libre, partiel ou total)
- Consultation des soldes et impayés
- Envoi ou renvoi d'une facture WhatsApp
- Consultation des rapports financiers
- Export des données de paiement

**Accès refusé :**
- Création ou modification des abonnés
- Accès aux campagnes de relevé
- Modification des paramètres système

**Contexte d'utilisation :** Bureau, ordinateur.

---

## 4. Exigences fonctionnelles

### 4.1 Auth Service

#### EF-AUTH-001 — Connexion
- Le système doit permettre à un utilisateur de se connecter avec un nom d'utilisateur et un mot de passe.
- En cas de succès, le système retourne un JWT avec une durée de validité de 24 heures ainsi qu'un refresh token de 7 jours.
- En cas d'échec, le système retourne un message d'erreur générique sans préciser si c'est le nom d'utilisateur ou le mot de passe qui est incorrect.
- Après 5 tentatives échouées consécutives, le compte est temporairement bloqué pendant 15 minutes.

#### EF-AUTH-002 — Déconnexion
- Le système doit permettre à un utilisateur de se déconnecter.
- Le token JWT est invalidé côté serveur.

#### EF-AUTH-003 — Rafraîchissement du token
- Le système doit permettre de renouveler un token arrivant à expiration sans demander les identifiants.

#### EF-AUTH-004 — Gestion des utilisateurs (Admin uniquement)
- L'admin peut créer un utilisateur avec : nom d'utilisateur, email, mot de passe temporaire, rôle.
- L'admin peut modifier un utilisateur (email, rôle).
- L'admin peut désactiver un utilisateur.
- Un utilisateur désactivé ne peut pas être supprimé (traçabilité).

#### EF-AUTH-005 — Validation des accès
- Chaque requête entrante dans l'API Gateway doit être accompagnée d'un JWT valide.
- Si le JWT est invalide ou expiré, le système retourne une erreur 401.
- Si le rôle ne permet pas l'action demandée, le système retourne une erreur 403.

---

### 4.2 Abonné Service

#### EF-ABO-001 — Création d'un abonné
- L'admin peut créer un abonné avec : numéro auto-généré (AB-XXXX), nom, prénom, téléphone WhatsApp, adresse.
- À la création, un compteur doit obligatoirement être associé.

#### EF-ABO-002 — Modification d'un abonné
- L'admin peut modifier toutes les informations d'un abonné.
- La modification du numéro WhatsApp prend effet immédiatement.

#### EF-ABO-003 — Suspension d'un abonné
- L'admin peut suspendre manuellement un abonné.
- Un abonné suspendu n'est plus inclus dans les nouvelles campagnes.
- Un abonné suspendu peut être réactivé.

#### EF-ABO-004 — Historique d'un abonné
- L'admin peut consulter l'historique complet : relevés, factures, paiements.

#### EF-ABO-005 — Gestion des compteurs
- Chaque abonné possède exactement un compteur actif à tout moment.
- Le compteur contient : numéro (entier), quartier, camp (entier), index initial, date de pose, statut.

#### EF-ABO-006 — Remplacement de compteur
- L'ancien compteur est archivé avec son dernier index.
- Le nouveau compteur est créé avec son propre index initial.
- L'historique du remplacement est conservé.

---

### 4.3 Campagne Service

#### EF-CAMP-001 — Création d'une campagne
- L'admin peut créer une campagne avec : nom, période (mois/année), date planifiée (optionnelle).
- Tous les abonnés actifs sont automatiquement ajoutés avec statut "À relever".
- L'ancien index est pré-rempli automatiquement (dernier nouveau_index ou index_initial).

#### EF-CAMP-002 — Planification d'une campagne
- La veille (J-1) de la date planifiée, notification WhatsApp aux admins et agents.
- Le jour J, la campagne passe automatiquement en statut "En cours" et notification est envoyée.

#### EF-CAMP-003 — Saisie d'un index (Agent)
- L'agent saisit le nouvel index depuis son téléphone.
- La consommation est calculée automatiquement.
- La date et heure de saisie sont enregistrées automatiquement.
- Le nouvel index doit être >= à l'ancien index (validation bloquante).
- Une observation textuelle peut être ajoutée.
- L'agent peut marquer "Non relevé" ou "Estimé" avec observation obligatoire.

#### EF-CAMP-004 — Suivi de progression
- Pourcentage de progression affiché en temps réel.
- Liste filtrable par quartier et par camp.
- Indicateurs visuels de statut.

#### EF-CAMP-005 — Clôture d'une campagne
- La clôture déclenche automatiquement la génération des factures.
- Une campagne clôturée ne peut plus être modifiée.

---

### 4.4 Facturation Service

#### EF-FACT-001 — Génération automatique des factures
- Une facture est générée pour chaque abonné relevé à la clôture de campagne.
- Contenu : numéro unique (FACT-AAAA-MM-XXXX), infos abonné, infos compteur, ancien index, nouvel index, consommation, prix unitaire, montant total, date de relevé, date limite de paiement.
- Le prix du m³ est copié dans la facture au moment de la génération.

#### EF-FACT-002 — Génération du PDF
- Chaque facture est convertie en PDF avec logo et informations de la société.

#### EF-FACT-003 — Gestion du tarif
- L'admin peut modifier le prix du m³.
- La modification n'affecte pas les factures déjà générées.
- Un historique des tarifs est conservé.

#### EF-FACT-004 — Consultation des factures
- Filtrable par campagne, abonné, statut, période.
- PDF téléchargeable depuis l'interface.

---

### 4.5 Paiement Service

#### EF-PAI-001 — Enregistrement d'un paiement
- Le comptable saisit : montant reçu (libre), date, mode (Espèces/Mobile Money/Virement), référence transaction (obligatoire si Mobile Money ou Virement).

#### EF-PAI-002 — Paiements partiels et multiples
- Plusieurs versements successifs sont possibles.
- Le solde restant est recalculé après chaque versement.
- Statut automatique : IMPAYÉE / PARTIELLE / PAYÉE.

#### EF-PAI-003 — Historique des paiements
- Chaque versement affiche : montant, date, mode, référence, enregistré par.

#### EF-PAI-004 — Liste des impayés
- Affiche : abonné, montant total, montant payé, solde restant, jours de retard, étape de relance.

---

### 4.6 Gestion des impayés (Paiement Service)

#### EF-IMP-001 — Déclenchement automatique
- Le lendemain de la date limite, si non entièrement payée, la facture passe en IMPAYÉE.

#### EF-IMP-002 — Processus de relance graduée

| Étape | Délai (configurable) | Action |
|---|---|---|
| 1 | J+0 | WhatsApp rappel doux |
| 2 | J+3 | WhatsApp rappel ferme |
| 3 | J+7 | WhatsApp avertissement + notification admin |
| 4 | J+10 | Suspension ligne d'eau + WhatsApp |

#### EF-IMP-003 — Suspension automatique
- Si l'option `suspension_auto` est activée, la ligne d'eau est suspendue automatiquement à l'étape 4.

#### EF-IMP-004 — Paiement partiel et relances
- Un versement partiel suspend les relances pendant 5 jours (configurable).

#### EF-IMP-005 — Rétablissement après paiement
- Facture PAYÉE : suivi d'impayé marqué résolu, ligne d'eau rétablie si suspendue, WhatsApp de confirmation envoyé.

#### EF-IMP-006 — Configuration des délais
- L'admin configure les délais de chaque étape et active/désactive la suspension automatique.

---

### 4.7 Notification Service

#### EF-NOTIF-001 — Envoi de facture WhatsApp
- Message contenant : récapitulatif textuel, PDF en pièce jointe, lien tokenisé avec date d'expiration explicite.

**Format du message :**
```
Bonjour [Prénom NOM],

Votre facture d'eau - [Mois Année]

Consommation : [X] m³
Montant dû    : [X] FCFA
Date limite   : [JJ/MM/AAAA]

📄 Votre facture est en pièce jointe.

🔗 Consultez votre historique :
https://[domaine]/espace/[token]

(Lien valable jusqu'au [JJ/MM/AAAA])
```

#### EF-NOTIF-002 — Lien tokenisé
- Token UUID v4 unique par envoi.
- Valide 20 jours par défaut (configurable).
- Date d'expiration affichée explicitement dans le message.
- Révocable par l'admin.

#### EF-NOTIF-003 — Espace abonné (accès public tokenisé)
- Accessible sans compte via le token dans l'URL.
- Affiche : toutes les factures (avec statut), historique de consommation, statut des paiements.
- Boutons d'export : PDF et CSV.

#### EF-NOTIF-004 — Messages de relance impayés

**Étape 1 — Rappel doux :**
```
Bonjour [Prénom NOM],

Votre facture de [Mois] d'un montant de [X] FCFA
est arrivée à échéance aujourd'hui.

Merci de régulariser votre situation dans les
meilleurs délais.

🔗 [lien espace abonné]
```

**Étape 2 — Rappel ferme :**
```
Bonjour [Prénom NOM],

Votre facture de [Mois] ([X] FCFA) est impayée
depuis 3 jours.

⚠️ Sans paiement, votre ligne d'eau fera l'objet
d'un avertissement.
```

**Étape 3 — Avertissement :**
```
Bonjour [Prénom NOM],

AVERTISSEMENT — Votre ligne d'eau est en situation
d'impayé depuis 7 jours ([X] FCFA).

🚨 Sans paiement dans les 3 jours, votre ligne d'eau
sera suspendue.
```

**Étape 4 — Suspension :**
```
Bonjour [Prénom NOM],

Votre ligne d'eau a été suspendue en raison d'un
impayé de [X] FCFA (Facture [Mois]).

Pour rétablir votre ligne d'eau, contactez notre
service au [numéro].
```

**Rétablissement :**
```
Bonjour [Prénom NOM],

Votre paiement de [X] FCFA a été reçu.
Votre ligne d'eau est maintenant rétablie.
```

#### EF-NOTIF-005 — Notifications administratives
- Admins et agents notifiés : J-1 et J d'une campagne planifiée, chaque suspension de ligne d'eau, chaque échec d'envoi WhatsApp.

#### EF-NOTIF-006 — Suivi des envois
- Chaque tentative enregistre : statut, date, ID Telnyx, erreur si échec.
- Renvoi possible depuis l'interface (comptable et admin).

---

### 4.8 Reporting Service

#### EF-REP-001 — Tableau de bord principal
- Progression campagne en cours (%, abonnés restants)
- Factures générées / envoyées
- Montant total facturé (période en cours)
- Montant encaissé / Montant impayé
- Nombre d'abonnés en impayé
- Consommation globale (m³)

#### EF-REP-002 — Statistiques par campagne
- Progression, consommation totale, montant facturé, taux de recouvrement.

#### EF-REP-003 — Statistiques globales
- Évolution multi-périodes de la consommation et du recouvrement.

---

### 4.9 Config Service

#### EF-CONF-001 — Informations de la société
- Nom, adresse, téléphone, logo (apparaissent sur les factures PDF).

#### EF-CONF-002 — Configuration de l'application
- Clé API Telnyx, numéro WhatsApp Business
- Délais de relance impayés (4 étapes)
- Activation/désactivation suspension automatique
- Délai de suspension des relances après versement partiel (défaut : 5 jours)
- Durée de validité des tokens d'accès (défaut : 20 jours)
- Délai de paiement (défaut : 5 jours)

---

## 5. User Stories

### 5.1 Stories — ADMIN

---

**US-ADMIN-001 — Créer un abonné**
```
En tant qu'admin
Je veux créer un nouvel abonné avec son compteur
Afin de l'intégrer dans le système et l'inclure dans les prochaines campagnes

Critères d'acceptation :
  ✅ Formulaire : nom, prénom, téléphone WhatsApp, adresse
  ✅ Compteur créé simultanément (numéro, quartier, camp, index initial)
  ✅ Numéro d'abonné généré automatiquement (AB-XXXX)
  ✅ Abonné actif par défaut
  ✅ Message de confirmation affiché
```

---

**US-ADMIN-002 — Créer une campagne de relevé**
```
En tant qu'admin
Je veux créer une nouvelle campagne de relevé mensuel
Afin d'organiser la collecte des index de tous les abonnés actifs

Critères d'acceptation :
  ✅ Nom et période saisis (ex : "Juin 2026", Juin/2026)
  ✅ Tous les abonnés actifs automatiquement ajoutés
  ✅ Ancien index pré-rempli pour chaque abonné
  ✅ Date planifiée optionnelle
  ✅ Progression initiale : 0%
```

---

**US-ADMIN-003 — Planifier une campagne**
```
En tant qu'admin
Je veux planifier une campagne à l'avance
Afin d'anticiper l'organisation des équipes terrain

Critères d'acceptation :
  ✅ Date future saisie
  ✅ WhatsApp envoyé J-1 aux admins et agents
  ✅ Campagne passe en "En cours" automatiquement le jour J
  ✅ WhatsApp envoyé le jour J
```

---

**US-ADMIN-004 — Clôturer une campagne**
```
En tant qu'admin
Je veux clôturer une campagne terminée
Afin de déclencher la génération automatique des factures

Critères d'acceptation :
  ✅ Clôture uniquement pour les campagnes "En cours"
  ✅ Confirmation demandée
  ✅ Factures générées automatiquement
  ✅ PDFs générés pour chaque facture
  ✅ Campagne "Clôturée" et non modifiable
```

---

**US-ADMIN-005 — Remplacer un compteur**
```
En tant qu'admin
Je veux enregistrer le remplacement d'un compteur
Afin de maintenir la continuité des relevés

Critères d'acceptation :
  ✅ Ancien compteur archivé avec son dernier index
  ✅ Nouveau compteur créé avec son index initial
  ✅ Historique du remplacement conservé
  ✅ Prochain relevé utilise l'index initial du nouveau compteur
```

---

### 5.2 Stories — AGENT

---

**US-AGENT-001 — Consulter la liste des abonnés à relever**
```
En tant qu'agent
Je veux voir la liste des abonnés que je dois relever
Afin d'organiser mon itinéraire terrain

Critères d'acceptation :
  ✅ Accessible depuis mon téléphone
  ✅ Affiche : nom, quartier, camp, numéro compteur, statut
  ✅ Filtrable par quartier et camp
  ✅ Progression globale visible
```

---

**US-AGENT-002 — Saisir un index**
```
En tant qu'agent
Je veux saisir le nouvel index d'un abonné depuis mon téléphone
Afin d'enregistrer sa consommation du mois

Critères d'acceptation :
  ✅ Ancien index affiché en lecture seule
  ✅ Saisie du nouvel index (clavier numérique)
  ✅ Consommation calculée et affichée instantanément
  ✅ Blocage si nouvel index < ancien index
  ✅ Observation optionnelle
  ✅ Date/heure enregistrées automatiquement
  ✅ Statut abonné passe à "Relevé"
```

---

**US-AGENT-003 — Marquer un abonné comme non relevé**
```
En tant qu'agent
Je veux indiquer qu'un abonné n'a pas pu être relevé
Afin de signaler la situation à l'admin

Critères d'acceptation :
  ✅ Statuts disponibles : "Non relevé", "Estimé"
  ✅ Observation obligatoire dans ce cas
  ✅ Abonné clairement identifié dans la liste des restants
```

---

### 5.3 Stories — COMPTABLE

---

**US-COMPT-001 — Enregistrer un paiement**
```
En tant que comptable
Je veux enregistrer le paiement d'une facture
Afin de mettre à jour le solde de l'abonné

Critères d'acceptation :
  ✅ Recherche par numéro de facture, nom d'abonné, période
  ✅ Montant reçu libre (partiel possible)
  ✅ Mode de paiement sélectionnable
  ✅ Référence transaction obligatoire si Mobile Money ou Virement
  ✅ Solde restant mis à jour automatiquement
  ✅ Statut facture mis à jour (PARTIELLE ou PAYÉE)
```

---

**US-COMPT-002 — Consulter les impayés**
```
En tant que comptable
Je veux voir la liste de tous les impayés
Afin de prioriser les relances

Critères d'acceptation :
  ✅ Affiche : abonné, montant total, payé, solde, jours de retard, étape relance
  ✅ Tri par montant ou ancienneté
  ✅ Enregistrement d'un paiement direct depuis la liste
```

---

**US-COMPT-003 — Renvoyer une facture WhatsApp**
```
En tant que comptable
Je veux renvoyer une facture par WhatsApp à un abonné
Afin de corriger un échec d'envoi

Critères d'acceptation :
  ✅ Renvoi depuis la liste des factures
  ✅ Nouveau token généré
  ✅ Historique des envois mis à jour
```

---

## 6. Règles métier

### 6.1 Règles de calcul

| ID | Règle |
|---|---|
| RM-001 | `Consommation (m³) = Nouveau index − Ancien index` |
| RM-002 | `Montant facture (FCFA) = Consommation × Prix du m³` |
| RM-003 | `Solde restant = Montant facture − Somme des versements` |
| RM-004 | `Date limite paiement = Date de relevé + Délai paiement (défaut : 5 jours)` |
| RM-005 | `Date expiration token = Date d'envoi + Durée validité (défaut : 20 jours)` |

### 6.2 Règles de statut

| ID | Objet | Règle |
|---|---|---|
| RS-001 | Facture | `montant_paye = 0` → IMPAYÉE |
| RS-002 | Facture | `0 < montant_paye < montant_total` → PARTIELLE |
| RS-003 | Facture | `montant_paye >= montant_total` → PAYÉE |
| RS-004 | Abonné | Suspension manuelle ou automatique → SUSPENDU |
| RS-005 | Abonné | Paiement intégral après suspension → ACTIF |
| RS-006 | Relevé | Nouveau index saisi → RELEVÉ |
| RS-007 | Relevé | Marqué manuellement → NON_RELEVÉ ou ESTIMÉ |

### 6.3 Règles de validation

| ID | Règle |
|---|---|
| RV-001 | Le nouveau index doit être >= à l'ancien index |
| RV-002 | Un abonné suspendu ne peut pas être ajouté à une campagne |
| RV-003 | Une campagne clôturée ne peut plus être modifiée |
| RV-004 | Un seul tarif actif à la fois |
| RV-005 | Référence de transaction obligatoire pour Mobile Money et Virement |
| RV-006 | Un token expiré ou révoqué ne donne plus accès à l'espace abonné |
| RV-007 | Compte bloqué 15 min après 5 tentatives de connexion échouées |

### 6.4 Règles de relance impayés

| Étape | Délai (défaut) | Action |
|---|---|---|
| 1 | J+0 | WhatsApp rappel doux |
| 2 | J+3 | WhatsApp rappel ferme |
| 3 | J+7 | WhatsApp avertissement + notification admin |
| 4 | J+10 | Suspension ligne d'eau (si option activée) + WhatsApp |

---

## 7. Exigences non fonctionnelles

### 7.1 Performance

| ID | Exigence |
|---|---|
| ENF-PERF-001 | Temps de réponse API Gateway ≤ 2 secondes pour 95% des requêtes |
| ENF-PERF-002 | Génération d'un PDF ≤ 5 secondes |
| ENF-PERF-003 | Chargement du tableau de bord ≤ 3 secondes |
| ENF-PERF-004 | Validation de saisie d'index ≤ 1 seconde |

### 7.2 Sécurité

| ID | Exigence |
|---|---|
| ENF-SEC-001 | Toutes les communications chiffrées en TLS/HTTPS |
| ENF-SEC-002 | Mots de passe hashés avec bcrypt (coût minimum : 12) |
| ENF-SEC-003 | JWT avec durée de vie de 24 heures |
| ENF-SEC-004 | Tokens d'accès abonnés en UUID v4 uniques |
| ENF-SEC-005 | Clé API Telnyx stockée chiffrée en base |
| ENF-SEC-006 | Contrôle d'accès par rôle sur chaque endpoint |
| ENF-SEC-007 | Tentatives de connexion échouées loguées |

### 7.3 Disponibilité

| ID | Exigence |
|---|---|
| ENF-DISP-001 | Disponible pendant les heures de travail (7h–20h, heure locale) |
| ENF-DISP-002 | Panne d'un service sans impact sur les autres (isolation des pannes) |

### 7.4 Utilisabilité

| ID | Exigence |
|---|---|
| ENF-UX-001 | Interface agent utilisable sur smartphone 5 pouces minimum |
| ENF-UX-002 | Saisie d'un index en 3 interactions maximum |
| ENF-UX-003 | Application PWA ajoutable à l'écran d'accueil |
| ENF-UX-004 | Messages d'erreur clairs en français |

### 7.5 Maintenabilité

| ID | Exigence |
|---|---|
| ENF-MAINT-001 | Chaque service indépendant et déployable séparément |
| ENF-MAINT-002 | Contrats gRPC (.proto) versionnés |
| ENF-MAINT-003 | Variables d'environnement externalisées (aucun secret dans le code) |
| ENF-MAINT-004 | Chaque service produit des logs structurés JSON |

---

## 8. Interfaces externes

### 8.1 Telnyx WhatsApp API

| Propriété | Valeur |
|---|---|
| Fournisseur | Telnyx |
| Protocole | HTTPS REST |
| Authentification | Bearer Token (clé API) |
| Fonctions utilisées | Envoi de messages texte, envoi de fichiers PDF |
| Gestion des erreurs | Retry automatique (3 tentatives maximum) |
| Coût estimé | < $1/mois pour 50 abonnés |

### 8.2 Interface Angular PWA

| Propriété | Valeur |
|---|---|
| Protocole | GraphQL sur HTTPS |
| Authentification | JWT dans le header Authorization |
| Compatibilité | Chrome Mobile, Safari Mobile, Firefox Mobile |
| Résolution minimale | 320px de largeur |

### 8.3 Espace Abonné (accès public)

| Propriété | Valeur |
|---|---|
| Accès | URL avec token UUID dans le path |
| Authentification | Token UUID dans l'URL |
| Actions disponibles | Lecture seule, export PDF/CSV |
| Expiration | 20 jours par défaut (configurable) |

---

## 9. Contraintes

### 9.1 Contraintes techniques

| ID | Contrainte |
|---|---|
| CT-001 | Serveur : MacBook Pro utilisé comme serveur local |
| CT-002 | Accès distant : ngrok (tunnel HTTPS) |
| CT-003 | Orchestration : Kubernetes + Minikube |
| CT-004 | Backend : Django par microservice |
| CT-005 | Communication inter-services : gRPC exclusivement |
| CT-006 | Base de données : PostgreSQL par service |
| CT-007 | Frontend : Angular PWA (mobile-first) |
| CT-008 | Génération PDF : ReportLab (Python) |
| CT-009 | Déploiement : Canary Deployment |

### 9.2 Contraintes métier

| ID | Contrainte |
|---|---|
| CM-001 | Maximum 50 abonnés dans la version actuelle |
| CM-002 | Un abonné possède exactement un compteur actif |
| CM-003 | Prix du m³ : 500 FCFA (modifiable par l'admin) |
| CM-004 | Montant TTC = Montant HT (aucun frais supplémentaire) |
| CM-005 | Délai de paiement par défaut : 5 jours |
| CM-006 | Tokens d'accès abonné expirés après 20 jours par défaut |

### 9.3 Contraintes budgétaires

| ID | Contrainte |
|---|---|
| CB-001 | Hébergement gratuit (MacBook + ngrok) |
| CB-002 | Coûts WhatsApp : frais Telnyx + Meta uniquement (environ $1/mois) |

---

## 10. Glossaire

| Terme | Définition |
|---|---|
| **Index** | Valeur numérique affichée par un compteur d'eau, en m³ |
| **Relevé** | Action de lire et enregistrer l'index d'un compteur |
| **Campagne** | Période organisée (généralement mensuelle) de relevé de tous les compteurs actifs |
| **Clôture** | Action de terminer une campagne et déclencher la facturation |
| **Token** | Identifiant unique et temporaire sécurisant l'accès à l'espace abonné |
| **Ligne d'eau** | Désignation de la connexion au réseau d'eau d'un abonné |
| **Camp** | Zone précise dans un quartier, identifiée par un numéro entier |
| **Versement** | Paiement partiel ou total d'une facture |
| **Solde restant** | Différence entre montant total d'une facture et somme des versements |
| **Étape de relance** | Niveau d'escalade dans le recouvrement des impayés (1 à 4) |
| **Suspension** | Coupure de la ligne d'eau pour impayé ou décision administrative |
| **Rétablissement** | Réactivation de la ligne d'eau après régularisation |
| **BSP** | Business Solution Provider — partenaire officiel WhatsApp Business |
| **gRPC** | Framework de communication inter-services haute performance (Google) |
| **GraphQL** | Langage de requête API permettant au client de demander exactement ce dont il a besoin |
| **JWT** | JSON Web Token — standard de transmission sécurisée d'informations |
| **PWA** | Progressive Web App — web app avec expérience native |
| **Kubernetes** | Système d'orchestration de conteneurs |
| **Minikube** | Kubernetes léger pour environnement local |
| **Canary Deployment** | Déploiement progressif avec montée en charge contrôlée |
