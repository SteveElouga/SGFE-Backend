"""Seed de démo — campagnes multi-mois avec created_by (auth). Idempotent.

2 campagnes appartiennent au superviseur, 1 à l'admin → permet de tester le
scope SUPERVISEUR de statsParMois (created_by == user.user_id).
"""

import uuid
from datetime import date

from campagnes.models import Campagne

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")
U_ADMIN = str(uuid.uuid5(NS, "user-admin"))
U_SUPERVISEUR = str(uuid.uuid5(NS, "user-superviseur"))


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

print(f"OK — {len(CAMPAGNES)} campagnes de démo")
