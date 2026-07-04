import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "paiement:events"


def publish_paiement_event(
    paiement,
    statut_facture: str,
    event_type: str = "PAIEMENT_CREATED",
) -> None:
    """Publie un événement auto-porteur sur Redis (souscription `paiementCree`).

    Contrairement aux autres domaines, le proto paiement n'expose pas de
    `GetPaiement` : l'événement transporte donc directement les champs affichés
    du paiement (+ `statut_facture` issu du solde), la gateway reconstruit le
    type sans re-fetch. `facture_id` sert au filtrage par campagne côté gateway.

    Best-effort : l'échec Redis ne fait jamais échouer l'enregistrement du
    paiement (même contrat que abonnes/event_publisher.py).
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps(
            {
                "event_type": event_type,
                "paiement_id": str(paiement.id),
                "facture_id": str(paiement.facture_id),
                "montant": float(paiement.montant),
                "date_paiement": paiement.date_paiement.isoformat() if paiement.date_paiement else "",
                "mode_paiement": paiement.mode_paiement,
                "reference_transaction": paiement.reference_transaction or "",
                "created_at": paiement.created_at.isoformat() if paiement.created_at else "",
                "enregistre_par": paiement.enregistre_par or "",
                "statut_facture": statut_facture,
            }
        )
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_paiement_event ignoré (Redis indisponible) : %s", exc)
