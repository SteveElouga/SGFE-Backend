#!/usr/bin/env python3
"""Import ponctuel des abonnés + relevés et génération des factures d'une période.

Pilote la Gateway GraphQL (localhost:8080) avec un compte ADMIN, en réutilisant
la logique métier réelle (donc montants/consommations corrects).

⚠️ Particularité importante (limite backend) : le service Campagne dérive
l'« ancien index » d'un relevé du DERNIER relevé connu, et retombe à 0 s'il n'y
en a aucun — il n'utilise PAS `compteur.index_initial`. Pour facturer la conso
juin→juillet, on procède donc en DEUX temps :
    1. campagne « baseline » : on saisit l'index de JUIN (établit le point de départ) ;
    2. campagne de facturation : on saisit l'index de JUILLET → ancien index = juin,
       donc conso = juillet − juin, correct.

NE FAIT AUCUN ENVOI (génération avec envoyerWhatsappAuto=false). Envoi séparé :
    python import_factures.py --send <CAMPAGNE_ID>

Identifiants admin via variables d'environnement :
    export SGFE_ADMIN_USER="<login admin>"
    export SGFE_ADMIN_PASS="<mot de passe admin>"
    export SGFE_GATEWAY="http://localhost:8080/graphql"   # optionnel
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.environ.get("SGFE_GATEWAY", "http://localhost:8080/graphql")


class GraphQLError(RuntimeError):
    pass


def gql(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(GATEWAY, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise GraphQLError(
            f"Gateway injoignable ({GATEWAY}) : {exc}. Le stack local tourne-t-il ?"
        ) from exc
    if payload.get("errors"):
        raise GraphQLError(
            "; ".join(e.get("message", str(e)) for e in payload["errors"])
        )
    return payload["data"]


def login() -> str:
    user, pw = os.environ.get("SGFE_ADMIN_USER"), os.environ.get("SGFE_ADMIN_PASS")
    if not user or not pw:
        sys.exit(
            "❌ Définis SGFE_ADMIN_USER et SGFE_ADMIN_PASS (compte ADMIN) avant de lancer."
        )
    q = "mutation($id:String!,$pw:String!){ login(identifier:$id,password:$pw){ accessToken user{ username } } }"
    data = gql(q, {"id": user, "pw": pw})
    print(f"✓ Connecté en tant que {data['login']['user']['username']}")
    return data["login"]["accessToken"]


def read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)]
    if not rows:
        sys.exit(f"❌ CSV vide : {path}")
    print(f"✓ {len(rows)} abonnés lus depuis {path}")
    return rows


# --- helpers GraphQL du parcours ---------------------------------------------
def creer_campagne(token, nom, mois, annee, date_planifiee) -> str:
    q = "mutation($i:CreateCampagneInput!){ creerCampagne(input:$i){ campagneId nom statut } }"
    inp = {
        "nom": nom,
        "periodeMois": mois,
        "periodeAnnee": annee,
        "datePlanifiee": date_planifiee,
        "genererFacturesAuto": False,
        "envoyerWhatsappAuto": False,
        "demarrerMaintenant": True,
    }
    c = gql(q, {"i": inp}, token)["creerCampagne"]
    print(f"✓ Campagne « {c['nom']} » créée ({c['statut']}) — id={c['campagneId']}")
    return c["campagneId"]


def ajouter(token, cid, ids) -> None:
    q = (
        "mutation($c:String!,$ids:[String!]!){ ajouterAbonnesCampagne(campagneId:$c,abonneIds:$ids)"
        "{ nbAjoutes nbIgnores } }"
    )
    r = gql(q, {"c": cid, "ids": ids}, token)["ajouterAbonnesCampagne"]
    print(f"  abonnés rattachés : {r['nbAjoutes']} ajoutés, {r['nbIgnores']} ignorés")


def saisir(token, cid, aid, index) -> None:
    q = "mutation($i:SaisirIndexInput!){ saisirIndex(input:$i){ nouveauIndex } }"
    gql(
        q,
        {
            "i": {
                "campagneId": cid,
                "abonneId": aid,
                "nouveauIndex": index,
                "observation": "",
            }
        },
        token,
    )


def cloturer(token, cid) -> None:
    gql(
        "mutation($c:String!){ cloturerCampagne(campagneId:$c){ statut } }",
        {"c": cid},
        token,
    )


def run_import(args) -> None:
    rows = read_rows(args.csv)
    total_attendu = sum(int(float(r["consommation_m3"])) for r in rows) * args.prix_m3
    token = login()

    # Tarif du m³
    gql(
        "mutation($p:Float!,$d:String!){ updateTarif(prixM3:$p,dateEffet:$d){ prixM3 } }",
        {"p": float(args.prix_m3), "d": args.date_effet},
        token,
    )
    print(f"✓ Tarif actif : {args.prix_m3} FCFA/m³ (effet {args.date_effet})")

    # Abonnés (+ compteur). On garde juin ET juillet pour les deux passes.
    q = "mutation($i:CreateAbonneInput!){ createAbonne(input:$i){ id numeroAbonne prenom } }"
    ab = []  # (numero, prenom, id, juin, juillet)
    for r in rows:
        inp = {
            "nom": r["nom"],
            "prenom": r["prenom"],
            "telephoneWhatsapp": r["telephone_whatsapp"],
            "adresse": "",
            "numeroCompteur": int(r["numero_compteur"]),
            "quartier": r["quartier"],
            "camp": int(r["camp"]),
            "indexInitial": float(r["index_initial_juin"]),
            "datePose": args.date_pose,
        }
        d = gql(q, {"i": inp}, token)["createAbonne"]
        ab.append(
            (
                d["numeroAbonne"],
                d["prenom"],
                d["id"],
                float(r["index_initial_juin"]),
                float(r["index_juillet"]),
            )
        )
        print(f"  + {d['numeroAbonne']} {d['prenom']}")
    print(f"✓ {len(ab)} abonnés créés")
    ids = [a[2] for a in ab]

    # PASSE 1 — baseline JUIN (établit l'ancien index ; pas de facture, pas d'envoi)
    print("\n— Passe 1 : baseline juin (point de départ des index) —")
    base = creer_campagne(
        token, "Init baseline juin — NE PAS ENVOYER", 6, args.annee, "2026-06-01"
    )
    ajouter(token, base, ids)
    for _, _, aid, juin, _ in ab:
        saisir(token, base, aid, juin)
    cloturer(token, base)
    print("✓ Baseline juin posée")

    # PASSE 2 — facturation JUILLET (ancien index = juin → conso = juillet − juin)
    print("\n— Passe 2 : facturation juillet —")
    cid = creer_campagne(token, args.campagne, args.mois, args.annee, args.date_effet)
    ajouter(token, cid, ids)
    for _, _, aid, _, juillet in ab:
        saisir(token, cid, aid, juillet)
    cloturer(token, cid)

    q = (
        "mutation($c:String!){ genererFactures(campagneId:$c,envoyerWhatsappAuto:false)"
        "{ factureId numeroFacture abonneNom ancienIndex nouveauIndex consommation montant statut } }"
    )
    factures = gql(q, {"c": cid}, token)["genererFactures"]
    total = sum(f["montant"] for f in factures)
    print(f"\n✓ {len(factures)} factures générées (NON envoyées) :")
    for f in factures:
        print(
            f"    {f.get('numeroFacture', '?'):18} {f.get('abonneNom', ''):20} "
            f"{int(f['ancienIndex'])}→{int(f['nouveauIndex'])}  "
            f"conso {int(f['consommation']):>4} m³  {int(f['montant']):>8} FCFA"
        )
    print(
        f"\n  Total facturé : {int(total)} FCFA   (attendu : {int(total_attendu)} FCFA)"
    )
    if int(total) != int(total_attendu):
        print("  ⚠ Écart avec le total attendu — À VÉRIFIER avant tout envoi.")
    print(
        f"\n➡️  Vérifie, puis pour ENVOYER :  python {os.path.basename(__file__)} --send {cid}\n"
    )


def run_send(campagne_id: str) -> None:
    token = login()
    print(f"Envoi WhatsApp des factures de la campagne {campagne_id}…")
    n = gql(
        "mutation($c:String!){ envoyerToutesFacturesWhatsapp(campagneId:$c) }",
        {"c": campagne_id},
        token,
    )
    print(f"✓ {n['envoyerToutesFacturesWhatsapp']} messages WhatsApp envoyés.")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Import abonnés/relevés + génération des factures (SGFE)."
    )
    p.add_argument(
        "--send",
        metavar="CAMPAGNE_ID",
        help="Envoie les factures WhatsApp d'une campagne.",
    )
    p.add_argument("--csv", default="import_abonnes_juillet2026.csv")
    p.add_argument("--campagne", default="Facturation Juillet 2026")
    p.add_argument("--mois", type=int, default=7)
    p.add_argument("--annee", type=int, default=2026)
    p.add_argument("--prix-m3", type=float, default=500.0)
    p.add_argument("--date-effet", default="2026-07-01")
    p.add_argument("--date-pose", default="2025-01-01")
    args = p.parse_args()
    try:
        run_send(args.send) if args.send else run_import(args)
    except GraphQLError as exc:
        sys.exit(f"\n❌ Erreur GraphQL : {exc}")


if __name__ == "__main__":
    main()
