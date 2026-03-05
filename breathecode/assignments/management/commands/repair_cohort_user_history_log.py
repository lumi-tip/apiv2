from django.core.management.base import BaseCommand

from breathecode.admissions.models import CohortUser
from breathecode.assignments.models import Task


class Command(BaseCommand):
    help = (
        "Repair history_log for CohortUser(s) after direct DB changes. "
        "Rebuilds delivered_assignments and pending_assignments from the Task table."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--cohort-id", type=int, help="Repair all cohort_users in this cohort")
        group.add_argument("--user-id", type=int, help="Repair all cohort_users for this user")
        group.add_argument("--cohort-user-id", type=int, help="Repair this specific cohort_user")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cohort_id = options.get("cohort_id")
        user_id = options.get("user_id")
        cohort_user_id = options.get("cohort_user_id")

        queryset = CohortUser.objects.filter(role="STUDENT").select_related("cohort", "user")
        if cohort_user_id is not None:
            queryset = queryset.filter(id=cohort_user_id)
        elif cohort_id is not None:
            queryset = queryset.filter(cohort_id=cohort_id)
        elif user_id is not None:
            queryset = queryset.filter(user_id=user_id)

        cohort_users = list(queryset)
        if not cohort_users:
            self.stdout.write(self.style.WARNING("No cohort_users found"))
            return

        self.stdout.write(f"Repairing history_log for {len(cohort_users)} cohort_user(s)...")

        def serialize_task(row):
            return {"id": row["id"], "type": row["task_type"]}

        repaired = 0
        for cohort_user in cohort_users:
            if not cohort_user.cohort_id:
                continue

            done_tasks = list(
                Task.objects.filter(
                    user=cohort_user.user,
                    cohort=cohort_user.cohort,
                    task_status="DONE",
                ).values("id", "task_type")
            )

            pending_tasks = list(
                Task.objects.filter(
                    user=cohort_user.user,
                    cohort=cohort_user.cohort,
                    task_status="PENDING",
                ).exclude(revision_status="IGNORED").values("id", "task_type")
            )

            new_history_log = cohort_user.history_log or {}
            new_history_log["delivered_assignments"] = [serialize_task(row) for row in done_tasks]
            new_history_log["pending_assignments"] = [serialize_task(row) for row in pending_tasks]

            if not dry_run:
                cohort_user.history_log = new_history_log
                cohort_user.save(update_fields=["history_log"])

            repaired += 1
            self.stdout.write(
                f"  CohortUser {cohort_user.id} (user={cohort_user.user_id}, cohort={cohort_user.cohort_id}): "
                f"{len(new_history_log['delivered_assignments'])} delivered, "
                f"{len(new_history_log['pending_assignments'])} pending"
            )

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"DRY RUN: Would repair {repaired} cohort_user(s)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Repaired {repaired} cohort_user(s)"))
