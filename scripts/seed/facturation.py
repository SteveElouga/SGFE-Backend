"""Seed de démo — factures backdatées sur 3 mois. Idempotent.

`date_generation` est en auto_now_add : on la force ensuite via .update() pour
étaler les factures sur plusieurs mois (statsParMois bucketise là-dessus).
montant = consommation * prix_m3 (500 FCFA/m3).
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from factures.models import Facture

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")
PRIX_M3 = Decimal("500")


def mois_decale(d: date, k: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) - k
    return total // 12, total % 12 + 1


def le_15(annee: int, mois: int) -> datetime:
    return datetime(annee, mois, 15, 12, 0, tzinfo=timezone.utc)


today = date.today()
A2, M2 = mois_decale(today, 2)
A1, M1 = mois_decale(today, 1)
A0, M0 = mois_decale(today, 0)

C_ALPHA = str(uuid.uuid5(NS, "camp-alpha"))
C_BETA = str(uuid.uuid5(NS, "camp-beta"))
C_GAMMA = str(uuid.uuid5(NS, "camp-gamma"))

# (clé uuid5, numero unique, campagne_id, conso m3, annee, mois, statut)
FACTURES = [
    ("fact-a1", "FACT-DEMO-A1", C_ALPHA, 24, A2, M2, "PAYEE"),
    ("fact-a2", "FACT-DEMO-A2", C_ALPHA, 16, A2, M2, "IMPAYEE"),
    ("fact-b1", "FACT-DEMO-B1", C_BETA, 30, A1, M1, "PAYEE"),
    ("fact-c1", "FACT-DEMO-C1", C_GAMMA, 40, A0, M0, "PAYEE"),
    ("fact-c2", "FACT-DEMO-C2", C_GAMMA, 10, A0, M0, "IMPAYEE"),
]

for cle, numero, campagne_id, conso, annee, mois, statut in FACTURES:
    fid = uuid.uuid5(NS, cle)
    abonne_id = str(uuid.uuid5(NS, f"abonne-{cle}"))
    conso_d = Decimal(conso)
    montant = conso_d * PRIX_M3
    gen = le_15(annee, mois)
    Facture.objects.update_or_create(
        id=fid,
        defaults={
            "numero_facture": numero,
            "abonne_id": abonne_id,
            "campagne_id": campagne_id,
            "ancien_index": Decimal("100"),
            "nouveau_index": Decimal("100") + conso_d,
            "consommation": conso_d,
            "prix_m3": PRIX_M3,
            "montant": montant,
            "statut": statut,
            "date_releve": gen.date(),
            "date_limite_paiement": gen.date(),
        },
    )
    # auto_now_add ignore la valeur passée à la création → on backdate ici.
    Facture.objects.filter(id=fid).update(date_generation=gen)
    print(f"  {numero:14} {annee}-{mois:02d}  conso={conso:>3}  montant={montant}")

print(f"OK — {len(FACTURES)} factures de démo (date_generation backdatée sur 3 mois)")
