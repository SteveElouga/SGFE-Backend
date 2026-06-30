from parametres.models import ConfigParam, InfosSociete


def infos_to_response(infos: InfosSociete) -> dict:
    """Convertit un objet InfosSociete en dict compatible avec InfosSocieteResponse (proto)."""
    return {
        "nom": infos.nom,
        "adresse": infos.adresse,
        "telephone": infos.telephone,
        "logo_path": infos.logo_path,
        "updated_at": infos.updated_at.isoformat() if infos.updated_at else "",
    }


def config_to_response(param: ConfigParam) -> dict:
    """Convertit un ConfigParam en dict compatible avec ConfigResponse (proto)."""
    return {
        "cle": param.cle,
        "valeur": param.valeur,
        "description": param.description,
    }
