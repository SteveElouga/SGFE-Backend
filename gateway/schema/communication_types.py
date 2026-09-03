"""Types Strawberry pour les diffusions (Notification Service)."""

import strawberry


@strawberry.type
class Diffusion:
    diffusion_id: str
    message: str
    statut: str  # EN_COURS | TERMINEE
    nb_total: int
    nb_envoyes: int
    nb_echecs: int
    created_by: str
    created_at: str


def diffusion_from_grpc(r, cree_par: str = "") -> Diffusion:
    return Diffusion(
        diffusion_id=r.diffusion_id,
        message=r.message,
        statut=r.statut,
        nb_total=r.nb_total,
        nb_envoyes=r.nb_envoyes,
        nb_echecs=r.nb_echecs,
        created_by=cree_par or r.created_by,
        created_at=r.created_at,
    )
