from abonnes.models import Abonne, Compteur


def _date_to_str(value) -> str:
    # `date_pose` reste une str tant que l'instance n'a pas été relue depuis
    # la BD (Django ne convertit pas les champs à l'assignation, seulement à
    # la lecture) — gère les deux cas plutôt que de supposer un objet `date`.
    return value.isoformat() if hasattr(value, "isoformat") else value


def compteur_to_response(compteur: Compteur) -> dict:
    return {
        "compteur_id": str(compteur.id),
        "numero_compteur": compteur.numero_compteur,
        "quartier": compteur.quartier,
        "camp": compteur.camp,
        "index_initial": float(compteur.index_initial),
        "date_pose": _date_to_str(compteur.date_pose),
        "statut": compteur.statut,
    }


def abonne_to_response(abonne: Abonne, compteur: Compteur | None = None) -> dict:
    return {
        "abonne_id": str(abonne.id),
        "numero_abonne": abonne.numero_abonne,
        "nom": abonne.nom,
        "prenom": abonne.prenom,
        "telephone_whatsapp": abonne.telephone_whatsapp,
        "adresse": abonne.adresse,
        "statut": abonne.statut,
        "created_at": abonne.created_at.isoformat(),
        "compteur": compteur_to_response(compteur) if compteur else None,
    }
