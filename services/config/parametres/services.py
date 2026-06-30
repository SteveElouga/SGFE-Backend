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
        """Met à jour les champs fournis (chaîne vide = inchangé)."""
        infos = self.repo.get_or_create()
        if nom:
            infos.nom = nom
        if adresse:
            infos.adresse = adresse
        if telephone:
            infos.telephone = telephone
        if logo_path:
            infos.logo_path = logo_path
        return self.repo.save(infos)


class ConfigService:
    """Gestion des paramètres de configuration clé/valeur."""

    def __init__(self) -> None:
        self.repo = ConfigParamRepository()

    def get(self, cle: str) -> ConfigParam:
        """Retourne le paramètre ; l'initialise avec sa valeur par défaut si absent."""
        return self.repo.get_or_default(cle)

    def update(self, cle: str, valeur: str) -> ConfigParam:
        """Met à jour la valeur d'un paramètre existant ou l'initialise puis le met à jour."""
        self.repo.get_or_default(cle)
        return self.repo.update(cle, valeur)

    def list_all(self) -> list[ConfigParam]:
        """Liste tous les paramètres en initialisant les valeurs par défaut manquantes."""
        return self.repo.list_all()
