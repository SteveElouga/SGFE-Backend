"""Constructeurs de messages WhatsApp selon la spec SRS.

EF-NOTIF-001 : message de facture avec token d'accès.
EF-NOTIF-004 : messages de relance impayés (étapes 1 à 4) + rétablissement.
"""


def build_message_facture(
    prenom_nom: str,
    periode: str,
    consommation: float,
    montant: float,
    date_limite: str,
    token: str,
    date_expiration_token: str,
    frontend_url: str,
) -> str:
    """Construit le message WhatsApp d'envoi de facture (EF-NOTIF-001).

    Args:
        prenom_nom: Prénom et NOM de l'abonné (ex. "Jean DUPONT").
        periode: Mois et année de la facture (ex. "Juillet 2025").
        consommation: Volume consommé en m³.
        montant: Montant dû en FCFA.
        date_limite: Date limite de paiement au format JJ/MM/AAAA.
        token: UUID du token d'accès à l'espace abonné.
        date_expiration_token: Date d'expiration du token au format JJ/MM/AAAA.
        frontend_url: URL de base du frontend Angular.

    Returns:
        Message WhatsApp formaté.
    """
    lien = f"{frontend_url}/espace/{token}"
    consommation_str = (
        f"{consommation:.0f}"
        if consommation == int(consommation)
        else f"{consommation}"
    )
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Votre facture d'eau - {periode}\n\n"
        f"Consommation : {consommation_str} m³\n"
        f"Montant dû    : {montant_str} FCFA\n"
        f"Date limite   : {date_limite}\n\n"
        f"📄 Votre facture est en pièce jointe.\n\n"
        f"🔗 Consultez votre historique :\n"
        f"{lien}\n\n"
        f"(Lien valable jusqu'au {date_expiration_token})"
    )


def build_message_relance_1(
    prenom_nom: str,
    periode: str,
    montant: float,
    lien_espace: str,
) -> str:
    """Construit le message de relance étape 1 — Rappel doux (EF-NOTIF-004).

    Args:
        prenom_nom: Prénom et NOM de l'abonné.
        periode: Mois de la facture impayée (ex. "Juillet").
        montant: Montant impayé en FCFA.
        lien_espace: URL complète de l'espace abonné (avec token).

    Returns:
        Message WhatsApp formaté.
    """
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Votre facture de {periode} d'un montant de {montant_str} FCFA\n"
        f"est arrivée à échéance aujourd'hui.\n\n"
        f"Merci de régulariser votre situation dans les\n"
        f"meilleurs délais.\n\n"
        f"🔗 {lien_espace}"
    )


def build_message_relance_2(
    prenom_nom: str,
    periode: str,
    montant: float,
) -> str:
    """Construit le message de relance étape 2 — Rappel ferme (EF-NOTIF-004).

    Args:
        prenom_nom: Prénom et NOM de l'abonné.
        periode: Mois de la facture impayée.
        montant: Montant impayé en FCFA.

    Returns:
        Message WhatsApp formaté.
    """
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Votre facture de {periode} ({montant_str} FCFA) est impayée\n"
        f"depuis 3 jours.\n\n"
        f"⚠️ Sans paiement, votre ligne d'eau fera l'objet\n"
        f"d'un avertissement."
    )


def build_message_relance_3(
    prenom_nom: str,
    montant: float,
) -> str:
    """Construit le message de relance étape 3 — Avertissement (EF-NOTIF-004).

    Args:
        prenom_nom: Prénom et NOM de l'abonné.
        montant: Montant impayé en FCFA.

    Returns:
        Message WhatsApp formaté.
    """
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"AVERTISSEMENT — Votre ligne d'eau est en situation\n"
        f"d'impayé depuis 7 jours ({montant_str} FCFA).\n\n"
        f"🚨 Sans paiement dans les 3 jours, votre ligne d'eau\n"
        f"sera suspendue."
    )


def build_message_relance_4(
    prenom_nom: str,
    montant: float,
    periode: str,
    telephone_societe: str,
) -> str:
    """Construit le message de relance étape 4 — Suspension (EF-NOTIF-004).

    Args:
        prenom_nom: Prénom et NOM de l'abonné.
        montant: Montant impayé en FCFA.
        periode: Mois de la facture impayée.
        telephone_societe: Numéro de contact de la société.

    Returns:
        Message WhatsApp formaté.
    """
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Votre ligne d'eau a été suspendue en raison d'un\n"
        f"impayé de {montant_str} FCFA (Facture {periode}).\n\n"
        f"Pour rétablir votre ligne d'eau, contactez notre\n"
        f"service au {telephone_societe}."
    )
