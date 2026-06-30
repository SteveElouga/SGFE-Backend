import strawberry


@strawberry.type
class InfosSociete:
    nom: str
    adresse: str
    telephone: str
    logo_path: str
    updated_at: str


@strawberry.type
class ConfigParam:
    cle: str
    valeur: str
    description: str


def infos_from_grpc(response) -> InfosSociete:
    """Construit un type GraphQL InfosSociete depuis un InfosSocieteResponse gRPC."""
    return InfosSociete(
        nom=response.nom,
        adresse=response.adresse,
        telephone=response.telephone,
        logo_path=response.logo_path,
        updated_at=response.updated_at,
    )


def config_from_grpc(response) -> ConfigParam:
    """Construit un type GraphQL ConfigParam depuis un ConfigResponse gRPC."""
    return ConfigParam(
        cle=response.cle,
        valeur=response.valeur,
        description=response.description,
    )
