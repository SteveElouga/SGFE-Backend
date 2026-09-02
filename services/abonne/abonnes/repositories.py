from abonnes.models import Abonne, Compteur, HistoriqueCompteur, StatutAbonne, StatutCompteur


class AbonneRepository:
    def get_by_id(self, abonne_id: str) -> Abonne:
        return Abonne.objects.get(id=abonne_id)

    def list_all(self, statut: str | None = None) -> list[Abonne]:
        qs = Abonne.objects.all()
        if statut:
            qs = qs.filter(statut=statut)
        return list(qs.order_by("numero_abonne"))

    def list_actifs(self) -> list[Abonne]:
        return list(Abonne.objects.filter(statut=StatutAbonne.ACTIF).order_by("numero_abonne"))

    def last_numero(self, for_update: bool = False) -> str | None:
        qs = Abonne.objects.select_for_update() if for_update else Abonne.objects.all()
        last = qs.order_by("-numero_abonne").values_list("numero_abonne", flat=True).first()
        return last

    def create(self, numero_abonne: str, nom: str, prenom: str, telephone_whatsapp: str, adresse: str) -> Abonne:
        return Abonne.objects.create(
            numero_abonne=numero_abonne,
            nom=nom,
            prenom=prenom,
            telephone_whatsapp=telephone_whatsapp,
            adresse=adresse,
        )

    def save(self, abonne: Abonne) -> Abonne:
        abonne.save()
        return abonne


class CompteurRepository:
    def get_actif(self, abonne_id: str) -> Compteur:
        return Compteur.objects.get(abonne_id=abonne_id, statut=StatutCompteur.ACTIF)

    def create(
        self,
        abonne: Abonne,
        numero_compteur: int,
        quartier: str,
        camp: int,
        index_initial: float,
        date_pose: str,
        position: str = "",
    ) -> Compteur:
        return Compteur.objects.create(
            abonne=abonne,
            numero_compteur=numero_compteur,
            quartier=quartier,
            camp=camp,
            index_initial=index_initial,
            date_pose=date_pose,
            position=position,
        )

    def save(self, compteur: Compteur) -> Compteur:
        compteur.save()
        return compteur

    def list_zones(self) -> list[dict]:
        """Zones de relevé distinctes (quartier, camp) avec le nombre d'abonnés
        actifs — un abonné actif = un compteur ACTIF rattaché à un abonné ACTIF."""
        from django.db.models import Count

        rows = (
            Compteur.objects.filter(statut=StatutCompteur.ACTIF, abonne__statut=StatutAbonne.ACTIF)
            .values("quartier", "camp")
            .annotate(nb_abonnes=Count("abonne_id", distinct=True))
            .order_by("quartier", "camp")
        )
        return list(rows)


class HistoriqueCompteurRepository:
    def create(
        self,
        abonne: Abonne,
        ancien_compteur: Compteur,
        nouveau_compteur: Compteur,
        index_fermeture: float,
        date_remplacement: str,
        motif: str = "",
    ) -> HistoriqueCompteur:
        return HistoriqueCompteur.objects.create(
            abonne=abonne,
            ancien_compteur=ancien_compteur,
            nouveau_compteur=nouveau_compteur,
            index_fermeture=index_fermeture,
            date_remplacement=date_remplacement,
            motif=motif,
        )

    def list_by_abonne(self, abonne_id: str) -> list[HistoriqueCompteur]:
        return list(
            HistoriqueCompteur.objects.filter(abonne_id=abonne_id)
            .select_related("ancien_compteur", "nouveau_compteur")
            .order_by("-date_remplacement")
        )
