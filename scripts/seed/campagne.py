"""Seed de démo — campagnes multi-mois avec created_by (auth). Idempotent.

3 campagnes CLOTUREE (2 au superviseur, 1 à l'admin) → permet de tester le
scope SUPERVISEUR de statsParMois (created_by == user.user_id). Ne pas
toucher à ces 3-là : dashboard-consultation.spec.ts (frontend) et le tableau
de scripts/seed/README.md dépendent de leurs valeurs exactes.

+ 1 campagne EN_COURS (« Delta ») avec 2 abonnés réellement rattachés
(relevés A_RELEVER) et demo_agent affecté — jusqu'ici absente : ce script ne
créait QUE des campagnes CLOTUREE, donc aucune saisie d'index n'était
possible et `terrain-saisie-index.spec.ts` (frontend) ne trouvait jamais de
campagne EN_COURS. Cette campagne n'a ni facture ni paiement (voir
scripts/seed/facturation.py / paiement.py) : elle n'entre dans aucun agrégat
de statsParMois, donc sans effet sur le tableau ci-dessus.
"""

import uuid
from datetime import date
from decimal import Decimal

from campagnes.models import Campagne, CampagneAgent, Releve

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")
U_ADMIN = str(uuid.uuid5(NS, "user-admin"))
U_SUPERVISEUR = str(uuid.uuid5(NS, "user-superviseur"))
# Même convention que scripts/seed/auth.py : uuid5(NS, f"user-{role.lower()}").
U_AGENT = str(uuid.uuid5(NS, "user-agent"))


def abonne_id(cle: str) -> str:
    """UUID déterministe d'un abonné créé par scripts/seed/abonne.py —
    reprend sa convention (`abonne-<clé>`) sans pouvoir importer ce module :
    campagne-service et abonne-service tournent dans des process/venvs
    distincts, chacun avec ses seuls modèles Django installés."""
    return str(uuid.uuid5(NS, f"abonne-{cle}"))


def mois_decale(d: date, k: int) -> tuple[int, int]:
    """(annee, mois) du mois situé k mois avant d."""
    total = d.year * 12 + (d.month - 1) - k
    return total // 12, total % 12 + 1


today = date.today()
a2, m2 = mois_decale(today, 2)
a1, m1 = mois_decale(today, 1)
a0, m0 = mois_decale(today, 0)

# (clé uuid5, nom, annee, mois, created_by)
CAMPAGNES = [
    ("camp-alpha", "Démo Alpha (superviseur)", a2, m2, U_SUPERVISEUR),
    ("camp-beta", "Démo Beta (superviseur)", a1, m1, U_SUPERVISEUR),
    ("camp-gamma", "Démo Gamma (admin)", a0, m0, U_ADMIN),
]

for cle, nom, annee, mois, created_by in CAMPAGNES:
    cid = uuid.uuid5(NS, cle)
    camp, _ = Campagne.objects.get_or_create(
        id=cid,
        defaults={
            "nom": nom,
            "periode_mois": mois,
            "periode_annee": annee,
            "statut": "CLOTUREE",
            "created_by": created_by,
        },
    )
    camp.nom = nom
    camp.periode_mois = mois
    camp.periode_annee = annee
    camp.statut = "CLOTUREE"
    camp.created_by = created_by
    camp.save()
    print(f"  {nom:28} {annee}-{mois:02d}  created_by={created_by}")

print(f"OK — {len(CAMPAGNES)} campagnes CLOTUREE de démo")

# ─── Campagne EN_COURS avec de vrais relevés à saisir ────────────────────────
#
# Nécessaire à terrain-saisie-index.spec.ts (frontend) : « suppose une
# campagne EN_COURS avec au moins un abonné "à relever" dans la tournée de ce
# compte AGENT ». demo_agent doit y être affecté (CampagneRepository.list_all
# filtre `agents_affectes__agent_id=agent_id`, voir campagnes/repositories.py)
# — sans CampagneAgent, `campagnes()` (gateway) ne renverrait jamais cette
# campagne à demo_agent, quel que soit son statut.
DELTA_ID = uuid.uuid5(NS, "camp-delta")
campagne_delta, _ = Campagne.objects.get_or_create(
    id=DELTA_ID,
    defaults={
        "nom": "Démo Delta (en cours — terrain)",
        "periode_mois": m0,
        "periode_annee": a0,
        "statut": "EN_COURS",
        "created_by": U_SUPERVISEUR,
    },
)
campagne_delta.nom = "Démo Delta (en cours — terrain)"
campagne_delta.periode_mois = m0
campagne_delta.periode_annee = a0
campagne_delta.statut = "EN_COURS"
campagne_delta.created_by = U_SUPERVISEUR
campagne_delta.save()
print(
    f"  {campagne_delta.nom:28} {a0}-{m0:02d}  created_by={U_SUPERVISEUR}  statut=EN_COURS"
)

# Affectation globale (aucune zone) : sans zone affectée, `list_tournee`
# (campagnes/services.py) fait couvrir à l'agent TOUTE la campagne plutôt que
# de le restreindre à un périmètre vide — voir sa docstring.
CampagneAgent.objects.get_or_create(campagne=campagne_delta, agent_id=U_AGENT)
print(f"  agent affecté : {U_AGENT} (demo_agent, aucune zone → toute la campagne)")

# Abonnés ACTIFS réellement rattachés (créés par scripts/seed/abonne.py, même
# clés `terrain-1`/`terrain-2`) — quartier/camp copiés du compteur qu'ils y
# reçoivent, pour rester cohérent avec la zone réelle de l'abonné même si
# aucune AffectationZone ne s'en sert ici.
ABONNES_A_RELEVER = [
    ("terrain-1", "Nkolbisson", 12),
    ("terrain-2", "Nkolbisson", 12),
]
for cle, quartier, camp_num in ABONNES_A_RELEVER:
    releve_id = uuid.uuid5(NS, f"releve-delta-{cle}")
    Releve.objects.get_or_create(
        id=releve_id,
        defaults={
            "campagne": campagne_delta,
            "abonne_id": abonne_id(cle),
            "ancien_index": Decimal("100"),
            "statut": "A_RELEVER",
            "quartier": quartier,
            "camp": camp_num,
        },
    )
    print(f"    relevé A_RELEVER — abonné {cle} ({quartier}, camp {camp_num})")

print(
    f"OK — 1 campagne EN_COURS de démo (Delta), {len(ABONNES_A_RELEVER)} relevé(s) à saisir"
)
