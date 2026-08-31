"""Constructeurs de messages WhatsApp selon la spec SRS.

EF-NOTIF-001 : message de facture avec token d'accès.
EF-NOTIF-004 : messages de relance impayés (étapes 1 à 4) + rétablissement.
"""


def _fcfa(montant: float) -> str:
    """Montant en FCFA, séparateur de milliers par espace insécable fine.

    Le PDF écrit « 23 500 » ; le message écrivait « 23500 ». Sur une facture à
    cinq chiffres, l'abonné doit pouvoir relire son total sans le compter.
    """
    entier = int(round(montant))
    return f"{entier:,}".replace(",", "\u202f")


def build_message_facture(
    prenom_nom: str,
    periode: str,
    consommation: float,
    montant: float,
    date_limite: str,
    token: str,
    date_expiration_token: str,
    frontend_url: str,
    numero_mobile_money: str = "",
    solde_anterieur: float = 0,
    nb_factures_anterieures: int = 0,
    plus_ancienne_echeance: str = "",
    avoir_impute: float = 0,
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
        numero_mobile_money: Numéro Mobile Money pour le paiement (optionnel).

    Returns:
        Message WhatsApp formaté.
    """
    lien = f"{frontend_url}/espace/{token}"
    consommation_str = f"{consommation:.0f}" if consommation == int(consommation) else f"{consommation}"

    paiement_mobile = f"\n💳 Paiement Mobile Money : {numero_mobile_money}\n" if numero_mobile_money else ""

    # Le total réellement dû, identique à celui du PDF joint : consommation du
    # mois, plus la dette antérieure, moins l'avoir déjà imputé. Il ne descend
    # jamais sous zéro — un avoir supérieur à la facture la solde sans créer
    # de créance envers l'abonné.
    total = max(0.0, montant + solde_anterieur - avoir_impute)

    # Détail affiché seulement s'il y a quelque chose à détailler. Sur une
    # facture ordinaire — pas d'antériorité, pas d'avoir — le message garde sa
    # forme courte : une ligne de montant, et c'est tout.
    lignes_detail = ""
    if solde_anterieur > 0 or avoir_impute > 0:
        lignes_detail = f"Montant du mois : {_fcfa(montant)} FCFA\n"
        if solde_anterieur > 0:
            pluriel = "s" if nb_factures_anterieures > 1 else ""
            lignes_detail += (
                f"Solde antérieur ({nb_factures_anterieures} facture{pluriel}) : {_fcfa(solde_anterieur)} FCFA\n"
            )
        if avoir_impute > 0:
            lignes_detail += f"Avoir appliqué : − {_fcfa(avoir_impute)} FCFA\n"
        lignes_detail += "──────────────────\n"

    # L'âge de la dette pèse plus que son montant : c'est lui qui fait payer.
    # Le PDF porte déjà cette mention, le message la reprend.
    note_anciennete = ""
    if solde_anterieur > 0 and plus_ancienne_echeance:
        note_anciennete = f"\n⚠️ Dont {_fcfa(solde_anterieur)} FCFA dus depuis le {plus_ancienne_echeance}.\n"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Votre facture d'eau - {periode}\n\n"
        f"Consommation : {consommation_str} m³\n"
        f"{lignes_detail}"
        f"TOTAL À PAYER : {_fcfa(total)} FCFA\n"
        f"Date limite : {date_limite}\n"
        f"{note_anciennete}"
        f"{paiement_mobile}\n"
        f"📄 Votre facture est en pièce jointe.\n\n"
        f"🔗 Consultez votre historique :\n"
        f"{lien}\n\n"
        f"(Lien valable jusqu'au {date_expiration_token})"
    )


def build_message_recu(
    prenom_nom: str,
    periode: str,
    montant: float,
    solde_restant: float,
) -> str:
    """Construit le message WhatsApp de confirmation de paiement (reçu joint).

    Args:
        prenom_nom: Prénom et NOM de l'abonné (ex. "Jean DUPONT").
        periode: Mois et année de la facture réglée (ex. "Juin 2026").
        montant: Montant du VERSEMENT reçu (ce que l'abonné a tendu), en FCFA.
        solde_restant: Ce qu'il doit encore EN TOUT, toutes factures confondues.

    Returns:
        Message WhatsApp formaté.

    Note sur la formulation. La phrase disait « ✅ Votre facture est soldée » — ce
    qui parlait de la mauvaise chose : le solde transmis est celui de l'abonné,
    pas d'une facture. Un versement au comptoir couvre souvent plusieurs
    factures, et le reçu PDF joint, lui, atteste l'imputation sur UNE facture.
    Les deux chiffres sont justes et mesurent deux choses différentes : chacun
    doit donc dire laquelle, sinon l'envoi paraît se contredire.
    """
    montant_str = f"{montant:.0f}" if montant == int(montant) else f"{montant}"
    if solde_restant <= 0:
        situation = "✅ Vous êtes à jour, plus rien n'est dû. Merci !"
    else:
        solde_str = f"{solde_restant:.0f}" if solde_restant == int(solde_restant) else f"{solde_restant}"
        situation = f"Reste dû, toutes factures : {solde_str} FCFA"

    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Nous confirmons la réception de votre paiement - {periode}\n\n"
        f"Montant réglé : {montant_str} FCFA\n"
        f"{situation}\n\n"
        f"📄 Votre reçu officiel est en pièce jointe.\n\n"
        f"Merci de votre confiance."
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


def build_message_retablissement(prenom_nom: str) -> str:
    """Message de RÉTABLISSEMENT de la ligne d'eau (EF-NOTIF-004, EF-IMP-005).

    Envoyé uniquement quand une suspension a réellement été levée.

    ── Deux mensonges retirés d'ici ─────────────────────────────────────────────

    **« Votre paiement de X FCFA a été reçu »**, où X valait `facture.montant`.
    Ce n'est jamais le versement : c'est la consommation du mois × le prix. Un
    abonné qui soldait les 2 000 restants d'une facture de 10 000 lisait « votre
    paiement de 10 000 FCFA a été reçu ». Le chiffre est donc retiré : le reçu,
    qui part par ailleurs, porte le montant réellement versé — lui le sait.

    **« Votre ligne d'eau est maintenant rétablie »**, affirmé sans condition.
    Or le cas de très loin le plus fréquent est un abonné qui n'a jamais été
    coupé : `AbonneServiceClient.reactiver_abonne` le dit dans son propre
    commentaire. Ce message ne part plus que sur un rétablissement réel — l'appel
    de réactivation rend maintenant s'il a agi ou non.

    Args:
        prenom_nom: Prénom et NOM de l'abonné.

    Returns:
        Message WhatsApp formaté.
    """
    return (
        f"Bonjour {prenom_nom},\n\nVotre dette est soldée et votre ligne d'eau est rétablie.\nMerci de votre règlement."
    )


def build_message_annulation_paiement(
    prenom_nom: str,
    periode: str,
    solde_restant: float,
) -> str:
    """Construit le message d'annulation d'un versement.

    Un versement annulé laisse l'abonné dans une situation qu'il ignore : il
    détient un reçu qui ne vaut plus rien, et une dette qu'il croyait éteinte.
    Ne rien dire, c'est le laisser découvrir la chose à la relance suivante.

    Le message ne nomme pas le montant annulé. Ce n'est pas un oubli : ce
    montant figure déjà sur le reçu que l'abonné détient, et le transporter
    jusqu'ici aurait demandé d'élargir le contrat `EnvoyerRelance` puis de
    régénérer les stubs de cinq services. Le chiffre qui appelle une action est
    de toute façon l'autre : **ce qui reste dû.**

    Le motif de l'annulation n'y figure pas non plus — il est destiné à la piste
    d'audit, et il est souvent écrit dans le vocabulaire du guichet
    (« doublon », « erreur de saisie ») plutôt que dans celui de l'abonné.

    Args:
        prenom_nom: Prénom et NOM de l'abonné.
        periode: Période de la facture concernée (ex. « Août 2026 »).
        solde_restant: Ce qui reste dû sur cette facture après annulation.

    Returns:
        Message WhatsApp formaté.
    """
    return (
        f"Bonjour {prenom_nom},\n\n"
        f"Un versement enregistré sur votre facture de {periode} a été annulé "
        f"par nos services.\n\n"
        f"Reste à payer : {_fcfa(solde_restant)} FCFA\n\n"
        f"Si vous avez bien effectué ce versement, contactez-nous : il s'agit "
        f"probablement d'une correction de saisie de notre part."
    )
