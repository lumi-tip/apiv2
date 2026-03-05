from django.core.management.base import BaseCommand, CommandError

from breathecode.admissions.models import Cohort, CohortUser
from breathecode.assignments.models import Task
from breathecode.authenticate.models import User
from breathecode.certificate.actions import generate_certificate, get_assets_from_syllabus
from breathecode.certificate.models import UserSpecialty


class Command(BaseCommand):
    help = (
        "Force graduation + certificate generation for cohorts without mandatory projects, "
        "only when all lessons and exercises are completed"
    )

    def add_arguments(self, parser):
        parser.add_argument("--cohort-id", type=int, help="Procesar todos los estudiantes de un cohort")
        parser.add_argument("--user-id", type=int, help="Procesar un usuario en sus cohorts")
        parser.add_argument("--user-email", type=str, help="Procesar un usuario por email en sus cohorts")
        parser.add_argument("--dry-run", action="store_true", help="Simula el proceso sin guardar cambios")

    def _resolve_target_user(self, options):
        user_id = options.get("user_id")
        user_email = options.get("user_email")

        if user_id and user_email:
            raise CommandError("Use solo uno: --user-id o --user-email")

        if user_id:
            user = User.objects.filter(id=user_id).first()
            if not user:
                raise CommandError(f"User con id {user_id} no encontrado")
            return user

        if user_email:
            user = User.objects.filter(email=user_email).first()
            if not user:
                raise CommandError(f"User con email {user_email} no encontrado")
            return user

        return None

    def _get_targets(self, cohort_id=None, user=None):
        query = CohortUser.objects.filter(role="STUDENT").exclude(cohort__stage="DELETED")
        if cohort_id:
            query = query.filter(cohort_id=cohort_id)
        if user:
            query = query.filter(user_id=user.id)

        return query.select_related("user", "cohort", "cohort__syllabus_version")

    def _evaluate_completion(self, cohort_user):
        cohort = cohort_user.cohort
        user = cohort_user.user
        syllabus_version = cohort.syllabus_version

        mandatory_projects = set(get_assets_from_syllabus(syllabus_version, task_types=["PROJECT"], only_mandatory=True) or [])
        if mandatory_projects:
            return {
                "processable": False,
                "reason": "has-mandatory-projects",
            }

        lesson_slugs = set(get_assets_from_syllabus(syllabus_version, task_types=["LESSON"], only_mandatory=False) or [])
        exercise_slugs = set(get_assets_from_syllabus(syllabus_version, task_types=["EXERCISE"], only_mandatory=False) or [])
        if not lesson_slugs and not exercise_slugs:
            return {
                "processable": False,
                "reason": "no-trackable-assets",
            }

        done_lessons = set(
            Task.objects.filter(
                user=user,
                cohort=cohort,
                task_type=Task.TaskType.LESSON,
                task_status=Task.TaskStatus.DONE,
                associated_slug__in=list(lesson_slugs),
            ).values_list("associated_slug", flat=True)
        )
        completed_exercises = set(
            Task.objects.filter(
                user=user,
                cohort=cohort,
                task_type=Task.TaskType.EXERCISE,
                revision_status__in=[Task.RevisionStatus.APPROVED, Task.RevisionStatus.IGNORED],
                associated_slug__in=list(exercise_slugs),
            ).values_list("associated_slug", flat=True)
        )

        missing_lessons = sorted(list(lesson_slugs - done_lessons))
        missing_exercises = sorted(list(exercise_slugs - completed_exercises))
        return {
            "processable": len(missing_lessons) == 0 and len(missing_exercises) == 0,
            "reason": "ok" if len(missing_lessons) == 0 and len(missing_exercises) == 0 else "incomplete-assets",
            "missing_lessons": missing_lessons,
            "missing_exercises": missing_exercises,
            "lesson_total": len(lesson_slugs),
            "exercise_total": len(exercise_slugs),
            "lesson_done": len(done_lessons),
            "exercise_done": len(completed_exercises),
        }

    def handle(self, *args, **options):
        cohort_id = options.get("cohort_id")
        dry_run = options.get("dry_run", False)
        user = self._resolve_target_user(options)

        if not cohort_id and not user:
            raise CommandError("Debes pasar --cohort-id o --user-id/--user-email")

        if cohort_id and user:
            raise CommandError("Elige un solo modo: por cohorte (--cohort-id) o por usuario (--user-id/--user-email)")

        if cohort_id:
            cohort = Cohort.objects.filter(id=cohort_id).first()
            if not cohort:
                raise CommandError(f"Cohort con id {cohort_id} no encontrado")
            if not cohort.syllabus_version:
                raise CommandError(
                    f"Cohort con id {cohort_id} no tiene syllabus_version, no se puede ejecutar el comando"
                )

        targets = list(self._get_targets(cohort_id=cohort_id, user=user))
        if not targets:
            self.stdout.write(self.style.WARNING("No se encontraron CohortUser estudiantes para procesar"))
            return

        counters = {
            "processed": 0,
            "graduated": 0,
            "cert_generated": 0,
            "already_certified": 0,
            "skipped_with_projects": 0,
            "skipped_incomplete": 0,
            "skipped_no_assets": 0,
            "skipped_no_syllabus": 0,
            "errors": 0,
        }

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN activo: no se guardarán cambios"))

        for cu in targets:
            counters["processed"] += 1
            cohort = cu.cohort
            user = cu.user
            full_name = user.get_full_name().strip() if hasattr(user, "get_full_name") else ""
            display_name = full_name or getattr(user, "username", "") or "-"
            user_ref = f"user={user.id} email={user.email or '-'} name={display_name}"

            if not cohort or not cohort.syllabus_version:
                counters["skipped_no_syllabus"] += 1
                self.stdout.write(
                    f"⏭ {user_ref} cohort={getattr(cohort, 'id', None)}: sin syllabus_version"
                )
                continue

            try:
                completion = self._evaluate_completion(cu)
                if not completion["processable"]:
                    reason = completion["reason"]
                    if reason == "has-mandatory-projects":
                        counters["skipped_with_projects"] += 1
                        self.stdout.write(f"⏭ {user_ref} cohort={cohort.id}: tiene proyectos obligatorios")
                    elif reason == "no-trackable-assets":
                        counters["skipped_no_assets"] += 1
                        self.stdout.write(f"⏭ {user_ref} cohort={cohort.id}: sin lessons/exercises medibles")
                    else:
                        counters["skipped_incomplete"] += 1
                        self.stdout.write(
                            f"⏭ {user_ref} cohort={cohort.id}: incompleto "
                            f"(lessons {completion['lesson_done']}/{completion['lesson_total']}, "
                            f"exercises {completion['exercise_done']}/{completion['exercise_total']})"
                        )
                    continue

                if cu.educational_status != "GRADUATED":
                    if not dry_run:
                        cu.educational_status = "GRADUATED"
                        cu.save(update_fields=["educational_status"])
                    counters["graduated"] += 1
                    self.stdout.write(f"✓ {user_ref} cohort={cohort.id}: graduado")

                existing_certificate = UserSpecialty.objects.filter(
                    user_id=user.id, cohort_id=cohort.id, status="PERSISTED"
                ).exists()
                if existing_certificate:
                    counters["already_certified"] += 1
                    self.stdout.write(f"⏭ {user_ref} cohort={cohort.id}: ya tiene certificado")
                    continue

                if dry_run:
                    counters["cert_generated"] += 1
                    self.stdout.write(f"[DRY-RUN] ✓ {user_ref} cohort={cohort.id}: se generaría certificado")
                    continue

                cert = generate_certificate(user, cohort)
                if cert.status == "PERSISTED":
                    counters["cert_generated"] += 1
                    self.stdout.write(f"✓ {user_ref} cohort={cohort.id}: certificado generado")
                else:
                    counters["errors"] += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"✗ {user_ref} cohort={cohort.id}: certificado no persistido ({cert.status_text})"
                        )
                    )

            except Exception as e:
                counters["errors"] += 1
                self.stdout.write(
                    self.style.ERROR(f"✗ {user_ref} cohort={cohort.id}: error inesperado: {str(e)}")
                )

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("RESUMEN force_specialty_generation")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Procesados: {counters['processed']}")
        self.stdout.write(f"Graduados: {counters['graduated']}")
        self.stdout.write(f"Certificados generados: {counters['cert_generated']}")
        self.stdout.write(f"Ya certificados: {counters['already_certified']}")
        self.stdout.write(f"Saltados por proyectos obligatorios: {counters['skipped_with_projects']}")
        self.stdout.write(f"Saltados por progreso incompleto: {counters['skipped_incomplete']}")
        self.stdout.write(f"Saltados por no tener assets medibles: {counters['skipped_no_assets']}")
        self.stdout.write(f"Saltados por no tener syllabus: {counters['skipped_no_syllabus']}")
        self.stdout.write(f"Errores: {counters['errors']}")
        self.stdout.write("=" * 60)
