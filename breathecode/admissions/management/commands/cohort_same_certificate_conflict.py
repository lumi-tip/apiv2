from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from breathecode.admissions.models import Cohort, CohortUser
from breathecode.authenticate.models import User


class Command(BaseCommand):
    help = (
        "Lista las cohortes donde un estudiante ya está inscrito con el mismo certificado, "
        "por eso no puede añadirse a otra cohorte."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="ID del usuario/estudiante",
        )
        parser.add_argument(
            "--user-email",
            type=str,
            help="Email del usuario/estudiante",
        )
        parser.add_argument(
            "--cohort-id",
            type=int,
            required=True,
            help="ID de la cohorte a la que intentas añadir al estudiante",
        )

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        user_email = options.get("user_email")
        cohort_id = options["cohort_id"]

        if not user_id and not user_email:
            raise CommandError("Debes indicar --user-id o --user-email")

        if user_id:
            user = User.objects.filter(id=user_id).first()
            if not user:
                raise CommandError(f"Usuario con ID {user_id} no encontrado")
        else:
            user = User.objects.filter(email=user_email).first()
            if not user:
                raise CommandError(f"Usuario con email '{user_email}' no encontrado")

        cohort = Cohort.objects.filter(id=cohort_id).first()
        if not cohort:
            raise CommandError(f"Cohorte con ID {cohort_id} no encontrada")

        prior_cohort_users = CohortUser.objects.filter(
            Q(educational_status="ACTIVE") | Q(educational_status__isnull=True),
            user_id=user.id,
            role="STUDENT",
            cohort__schedule=cohort.schedule,
        ).exclude(cohort_id=cohort_id).select_related("cohort")

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("CONFLICTO: mismo certificado")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Usuario: {user.email} (ID: {user.id})")
        self.stdout.write(f"Cohorte destino: {cohort.name} (slug: {cohort.slug}, ID: {cohort.id})")
        is_saas = cohort.available_as_saas is True or (
            cohort.available_as_saas is None and getattr(cohort.academy, "available_as_saas", False) is True
        )
        if is_saas:
            self.stdout.write(self.style.WARNING("Tipo: SaaS (los estudiantes pueden cursar varios cursos SaaS)"))
        if cohort.schedule:
            self.stdout.write(f"Certificado/schedule: {cohort.schedule.name}")
        else:
            self.stdout.write("Certificado/schedule: (ninguno)")
        self.stdout.write("")

        count = prior_cohort_users.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("✓ No hay conflicto. El estudiante NO está en otra cohorte con el mismo certificado."))
            return

        if is_saas:
            self.stdout.write(self.style.SUCCESS(
                "✓ Cohorte SaaS: la restricción de mismo certificado NO aplica. El estudiante puede añadirse."
            ))
            self.stdout.write("")

        self.stdout.write(self.style.WARNING(f"El estudiante ya está en {count} cohorte(s) con el mismo schedule:"))
        self.stdout.write("")
        for cu in prior_cohort_users:
            cohort_ref = cu.cohort
            self.stdout.write(f"  • {cohort_ref.name}")
            self.stdout.write(f"    slug: {cohort_ref.slug}")
            self.stdout.write(f"    ID: {cohort_ref.id}")
            self.stdout.write(f"    educational_status: {cu.educational_status or '(null)'}")
            self.stdout.write("")

        self.stdout.write("-" * 60)
        self.stdout.write(
            "Para añadir al estudiante a esta cohorte, cambia su educational_status "
            "en la(s) cohorte(s) anterior(es) a algo distinto de ACTIVE (ej: GRADUATED, DROPPED)."
        )
        self.stdout.write("")
