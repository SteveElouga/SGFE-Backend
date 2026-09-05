from django.utils.translation import gettext_lazy as _

from parametres.models import CONFIG_DEFAULTS, ConfigParam, InfosSociete


class InfosSocieteRepository:
    """Accès base de données pour l'enregistrement singleton InfosSociete."""

    def get_or_create(self) -> InfosSociete:
        """Retourne l'unique enregistrement, en le créant avec des valeurs vides si absent."""
        obj, _ = InfosSociete.objects.get_or_create(pk=1)
        return obj

    def save(self, infos: InfosSociete) -> InfosSociete:
        infos.save()
        return infos


class ConfigParamRepository:
    """Accès base de données pour les paramètres de configuration."""

    def get(self, cle: str) -> ConfigParam:
        """Lève ObjectDoesNotExist si la clé est inconnue."""
        return ConfigParam.objects.get(cle=cle)

    def get_or_default(self, cle: str) -> ConfigParam:
        """Retourne le paramètre existant ou le crée avec sa valeur par défaut."""
        if cle not in CONFIG_DEFAULTS:
            from django.core.exceptions import ObjectDoesNotExist

            raise ObjectDoesNotExist(_("Clé de configuration inconnue : {cle!r}").format(cle=cle))
        valeur_defaut, description = CONFIG_DEFAULTS[cle]
        obj, _created = ConfigParam.objects.get_or_create(
            cle=cle,
            defaults={"valeur": valeur_defaut, "description": description},
        )
        return obj

    def update(self, cle: str, valeur: str) -> ConfigParam:
        """Met à jour la valeur d'un paramètre existant. Lève ObjectDoesNotExist sinon."""
        param = self.get(cle)
        param.valeur = valeur
        param.save(update_fields=["valeur", "updated_at"])
        return param

    def list_all(self) -> list[ConfigParam]:
        """Retourne tous les paramètres, en initialisant les valeurs par défaut manquantes."""
        existing = {p.cle for p in ConfigParam.objects.all()}
        to_create = [
            ConfigParam(cle=cle, valeur=val, description=desc)
            for cle, (val, desc) in CONFIG_DEFAULTS.items()
            if cle not in existing
        ]
        if to_create:
            ConfigParam.objects.bulk_create(to_create)
        return list(ConfigParam.objects.all())
