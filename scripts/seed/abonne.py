"""Seed de démo — abonnés + compteurs (abonne-service). Idempotent.

Jusqu'ici absent de `scripts/seed_demo.sh` : le parc d'abonnés restait
toujours vide après un seed complet. Deux conséquences côté frontend
(vérifié dans le code des specs, pas supposé) :

- `abonnes-gestion.spec.ts` (ADMIN) a besoin d'au moins un abonné réel pour
  que `/abonnes` affiche une ligne, et d'un numéro/nom connu à l'avance
  (`E2E_ABONNE_RECHERCHE`) pour vérifier que la recherche filtre vraiment la
  liste plutôt que de cliquer une première ligne non filtrée. Voir
  `E2E_ABONNE_RECHERCHE` recommandé dans `scripts/seed/README.md`.
- `terrain-saisie-index.spec.ts` (AGENT) a besoin d'abonnés ACTIFS
  réellement rattachés à une campagne EN_COURS, avec un relevé A_RELEVER —
  `scripts/seed/campagne.py` rattache deux des abonnés ci-dessous
  (terrain-1/terrain-2) à sa campagne de démo « Delta » par ce même
  identifiant déterministe (uuid5), campagne-service et abonne-service étant
  deux bases distinctes sans FK inter-services.

Statuts variés (ACTIF/SUSPENDU/RESILIE) pour que l'écran de gestion des
abonnés ait de quoi afficher des badges différents, pas un parc uniforme.

`numero_abonne`/`numero_compteur` utilisent une plage réservée (AB-90xx /
900xx) plutôt que la suite naturelle (AB-0001…, que `NumerotationService`
utiliserait via l'API) : sur un environnement déjà peuplé par des créations
normales, ce seed ne doit jamais entrer en collision avec un numéro déjà
attribué.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")

# Date de pose arbitraire mais stable dans le passé — sans incidence
# fonctionnelle ici (aucune règle métier ne s'applique à `date_pose` dans ce
# seed), juste une valeur plausible plutôt qu'une date fixe qui vieillirait
# mal dans un futur lointain.
DATE_POSE = date.today() - timedelta(days=365)


def abid(cle: str) -> str:
    """UUID déterministe d'un abonné — même convention que `abonne_id` dans
    scripts/seed/paiement.py et scripts/seed/facturation.py (`abonne-<clé>`),
    et reprise telle quelle par scripts/seed/campagne.py pour rattacher ces
    abonnés à sa campagne EN_COURS sans pouvoir importer ce module (chaque
    script s'exécute dans le process/venv d'un service différent)."""
    return str(uuid.uuid5(NS, f"abonne-{cle}"))


# (clé, numero_abonne, numero_compteur, nom, prenom, téléphone E.164, quartier, camp, statut)
ABONNES = [
    (
        "terrain-1",
        "AB-9001",
        90001,
        "Mballa",
        "Jean",
        "+237699100001",
        "Nkolbisson",
        12,
        StatutAbonne.ACTIF,
    ),
    (
        "terrain-2",
        "AB-9002",
        90002,
        "Etoundi",
        "Marie",
        "+237699100002",
        "Nkolbisson",
        12,
        StatutAbonne.ACTIF,
    ),
    # Numéro dédié à la recherche e2e (abonnes-gestion.spec.ts) — voir
    # E2E_ABONNE_RECHERCHE recommandé dans scripts/seed/README.md.
    (
        "recherche",
        "AB-9003",
        90003,
        "Ngono",
        "Paul",
        "+237699100003",
        "Mvog-Ada",
        4,
        StatutAbonne.ACTIF,
    ),
    (
        "suspendu-1",
        "AB-9004",
        90004,
        "Fouda",
        "Alice",
        "+237699100004",
        "Mvog-Ada",
        4,
        StatutAbonne.SUSPENDU,
    ),
    (
        "resilie-1",
        "AB-9005",
        90005,
        "Biya",
        "Eric",
        "+237699100005",
        "Essos",
        7,
        StatutAbonne.RESILIE,
    ),
    (
        "actif-3",
        "AB-9006",
        90006,
        "Nkolo",
        "Sarah",
        "+237699100006",
        "Essos",
        7,
        StatutAbonne.ACTIF,
    ),
]

for (
    cle,
    numero_abonne,
    numero_compteur,
    nom,
    prenom,
    telephone,
    quartier,
    camp,
    statut,
) in ABONNES:
    aid = uuid.uuid5(NS, f"abonne-{cle}")
    abonne, _ = Abonne.objects.get_or_create(
        id=aid,
        defaults={
            "numero_abonne": numero_abonne,
            "nom": nom,
            "prenom": prenom,
            "telephone_whatsapp": telephone,
            "adresse": f"Quartier {quartier}, camp {camp}",
            "statut": statut,
        },
    )
    abonne.numero_abonne = numero_abonne
    abonne.nom = nom
    abonne.prenom = prenom
    abonne.telephone_whatsapp = telephone
    abonne.adresse = f"Quartier {quartier}, camp {camp}"
    abonne.statut = statut
    abonne.save()

    # Un abonné résilié n'a plus de compteur ACTIF (voir
    # AbonneService.resilier_abonne, qui désactive le compteur à la
    # résiliation) — reproduit ici directement puisqu'on écrit au niveau ORM,
    # sans passer par le service.
    compteur_statut = (
        StatutCompteur.DESACTIVE
        if statut == StatutAbonne.RESILIE
        else StatutCompteur.ACTIF
    )
    cid = uuid.uuid5(NS, f"compteur-{cle}")
    compteur, _ = Compteur.objects.get_or_create(
        id=cid,
        defaults={
            "abonne": abonne,
            "numero_compteur": numero_compteur,
            "quartier": quartier,
            "camp": camp,
            "index_initial": Decimal("100"),
            "date_pose": DATE_POSE,
            "statut": compteur_statut,
        },
    )
    compteur.abonne = abonne
    compteur.numero_compteur = numero_compteur
    compteur.quartier = quartier
    compteur.camp = camp
    compteur.index_initial = Decimal("100")
    compteur.date_pose = DATE_POSE
    compteur.statut = compteur_statut
    compteur.save()

    print(
        f"  {numero_abonne}  {nom:8} {prenom:6}  {statut:9}  compteur={numero_compteur} ({quartier}, camp {camp})"
    )

print(f"OK — {len(ABONNES)} abonnés de démo (recherche e2e : numéro {ABONNES[2][1]!r})")
