"""Accès base de données pour les tables de stats dénormalisées."""

from stats.models import StatsCampagne, StatsFacturation, StatsPaiements


class StatsCampagneRepository:
    def get_or_create(self, campagne_id: str) -> StatsCampagne:
        obj, _ = StatsCampagne.objects.get_or_create(campagne_id=campagne_id)
        return obj

    def get(self, campagne_id: str) -> StatsCampagne:
        """Lève ObjectDoesNotExist si absente."""
        return StatsCampagne.objects.get(campagne_id=campagne_id)

    def get_derniere(self) -> StatsCampagne | None:
        """La campagne la plus récemment mise à jour = « campagne en cours »."""
        return StatsCampagne.objects.order_by("-updated_at").first()

    def list_all(self) -> list[StatsCampagne]:
        return list(StatsCampagne.objects.order_by("-updated_at"))

    def save(self, stats: StatsCampagne) -> StatsCampagne:
        stats.save()
        return stats


class StatsFacturationRepository:
    def get_or_create(self, campagne_id: str) -> StatsFacturation:
        obj, _ = StatsFacturation.objects.get_or_create(campagne_id=campagne_id)
        return obj

    def get_or_none(self, campagne_id: str) -> StatsFacturation | None:
        return StatsFacturation.objects.filter(campagne_id=campagne_id).first()

    def list_all(self) -> list[StatsFacturation]:
        return list(StatsFacturation.objects.all())

    def save(self, stats: StatsFacturation) -> StatsFacturation:
        stats.save()
        return stats


class StatsPaiementsRepository:
    def get_or_create(self, campagne_id: str) -> StatsPaiements:
        obj, _ = StatsPaiements.objects.get_or_create(campagne_id=campagne_id)
        return obj

    def get_or_none(self, campagne_id: str) -> StatsPaiements | None:
        return StatsPaiements.objects.filter(campagne_id=campagne_id).first()

    def list_all(self) -> list[StatsPaiements]:
        return list(StatsPaiements.objects.all())

    def save(self, stats: StatsPaiements) -> StatsPaiements:
        stats.save()
        return stats
