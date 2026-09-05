from django.db import transaction

from parametres.audit import enregistrer_audit
from parametres.models import ConfigParam, InfosSociete
from parametres.repositories import ConfigParamRepository, InfosSocieteRepository


class InfosSocieteService:
    """Gestion des informations de la société (singleton)."""

    def __init__(self) -> None:
        self.repo = InfosSocieteRepository()

    def get(self) -> InfosSociete:
        """Retourne les infos société, en créant l'enregistrement vide si nécessaire."""
        return self.repo.get_or_create()

    def update(
        self,
        nom: str = "",
        adresse: str = "",
        telephone: str = "",
        logo_path: str = "",
    ) -> InfosSociete:
        """Met à jour les champs fournis (chaîne vide = inchangé).

        La mutation et l'entrée d'audit qu'elle produit commitent — ou
        échouent — ensemble (voir `parametres.audit.enregistrer_audit`).
        """
        with transaction.atomic():
            infos = self.repo.get_or_create()
            champs_modifies: list[str] = []
            if nom:
                infos.nom = nom
                champs_modifies.append(f"nom={nom!r}")
            if adresse:
                infos.adresse = adresse
                champs_modifies.append(f"adresse={adresse!r}")
            if telephone:
                infos.telephone = telephone
                champs_modifies.append(f"telephone={telephone!r}")
            if logo_path:
                infos.logo_path = logo_path
                champs_modifies.append(f"logo_path={logo_path!r}")
            infos = self.repo.save(infos)
            enregistrer_audit(
                action="INFOS_SOCIETE_MODIFIEES",
                objet_type="InfosSociete",
                objet_id=str(infos.pk),
                detail=" — ".join(champs_modifies) if champs_modifies else "aucun champ fourni",
            )
        return infos


class ConfigService:
    """Gestion des paramètres de configuration clé/valeur."""

    def __init__(self) -> None:
        self.repo = ConfigParamRepository()

    def get(self, cle: str) -> ConfigParam:
        """Retourne le paramètre ; l'initialise avec sa valeur par défaut si absent."""
        return self.repo.get_or_default(cle)

    def update(self, cle: str, valeur: str) -> ConfigParam:
        """Met à jour la valeur d'un paramètre existant ou l'initialise puis le met à jour.

        La mutation et l'entrée d'audit qu'elle produit commitent — ou
        échouent — ensemble (voir `parametres.audit.enregistrer_audit`).
        """
        with transaction.atomic():
            avant = self.repo.get_or_default(cle)
            valeur_avant = avant.valeur
            param = self.repo.update(cle, valeur)
            enregistrer_audit(
                action="CONFIG_PARAM_MODIFIE",
                objet_type="ConfigParam",
                objet_id=cle,
                detail=f"valeur : {valeur_avant!r} → {valeur!r}",
            )
        return param

    def list_all(self) -> list[ConfigParam]:
        """Liste tous les paramètres en initialisant les valeurs par défaut manquantes."""
        return self.repo.list_all()
