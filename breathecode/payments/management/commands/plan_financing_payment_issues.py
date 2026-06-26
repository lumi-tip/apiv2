"""
Lista usuarios con problemas de pago en plan financings asociados a uno o más planes.

Incluye financings con:
  - status PAYMENT_ISSUE, o
  - status ACTIVE cuyo next_payment_at ya pasó.

Uso:
    python manage.py plan_financing_payment_issues --plan-slugs full-stack
    python manage.py plan_financing_payment_issues --plan-slugs full-stack,data-science
    python manage.py plan_financing_payment_issues full-stack data-science
    python manage.py plan_financing_payment_issues --plan-slugs full-stack --json
    python manage.py plan_financing_payment_issues --plan-slugs full-stack --verbose
"""

import json

from django.core.management.base import BaseCommand
from django.db.models import Prefetch, Q
from django.utils import timezone

from breathecode.payments.models import Plan, PlanFinancing


class Command(BaseCommand):
    help = (
        "Lista usuarios con PAYMENT_ISSUE o cuota vencida (ACTIVE + next_payment_at pasado) "
        "en plan financings de uno o más planes (por slug)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "plan_slugs",
            nargs="*",
            help="Slug(s) del plan. También puedes usar --plan-slugs.",
        )
        parser.add_argument(
            "--plan-slugs",
            dest="plan_slugs_flag",
            type=str,
            help="Slugs de planes separados por coma (ej: full-stack,data-science).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Imprime el resultado completo en JSON.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Imprime el reporte detallado en lugar de solo la lista de emails.",
        )

    def handle(self, *args, **options):
        plan_slugs = self._resolve_plan_slugs(options)
        if not plan_slugs:
            self.stdout.write(
                self.style.ERROR("Debes indicar al menos un plan slug (argumento posicional o --plan-slugs).")
            )
            return

        existing_plans = Plan.objects.filter(slug__in=plan_slugs).values_list("slug", flat=True)
        missing_slugs = sorted(set(plan_slugs) - set(existing_plans))
        if missing_slugs:
            self.stdout.write(
                self.style.WARNING(f"Planes no encontrados: {', '.join(missing_slugs)}")
            )

        now = timezone.now()
        financings = (
            PlanFinancing.objects.filter(
                Q(status=PlanFinancing.Status.PAYMENT_ISSUE)
                | Q(status=PlanFinancing.Status.ACTIVE, next_payment_at__lt=now),
                plans__slug__in=plan_slugs,
            )
            .select_related("user", "academy", "currency")
            .prefetch_related(Prefetch("plans", queryset=Plan.objects.only("id", "slug", "title")))
            .distinct()
            .order_by("user__email", "id")
        )

        results = [self._serialize_financing(financing, now) for financing in financings]

        if options["json"]:
            self.stdout.write(json.dumps(results, indent=2, default=str))
            return

        if options["verbose"]:
            self._print_report(plan_slugs, results, missing_slugs)
            return

        self._print_email_list(results, missing_slugs)

    def _resolve_plan_slugs(self, options):
        slugs = list(options.get("plan_slugs") or [])
        if options.get("plan_slugs_flag"):
            slugs.extend(s.strip() for s in options["plan_slugs_flag"].split(",") if s.strip())
        return sorted(set(slugs))

    def _issue_reason(self, financing, now):
        if financing.status == PlanFinancing.Status.PAYMENT_ISSUE:
            return "payment_issue"
        if (
            financing.status == PlanFinancing.Status.ACTIVE
            and financing.next_payment_at
            and financing.next_payment_at < now
        ):
            return "overdue_payment"
        return "unknown"

    def _serialize_financing(self, financing, now):
        user = financing.user
        plan_slugs = sorted({plan.slug for plan in financing.plans.all() if plan.slug})
        issue_reason = self._issue_reason(financing, now)
        return {
            "plan_financing_id": financing.id,
            "user_id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "academy_id": financing.academy_id,
            "academy_slug": financing.academy.slug if financing.academy_id else None,
            "academy_name": financing.academy.name if financing.academy_id else None,
            "status": financing.status,
            "issue_reason": issue_reason,
            "status_message": financing.status_message,
            "plan_slugs": plan_slugs,
            "installments_paid": financing.installments_paid,
            "how_many_installments": financing.how_many_installments,
            "monthly_price": financing.monthly_price,
            "currency_code": financing.currency.code if financing.currency_id else None,
            "next_payment_at": financing.next_payment_at,
            "valid_until": financing.valid_until,
            "plan_expires_at": financing.plan_expires_at,
            "externally_managed": financing.externally_managed,
            "created_at": financing.created_at,
        }

    def _issue_reason_label(self, issue_reason):
        return {
            "payment_issue": "PAYMENT_ISSUE",
            "overdue_payment": "ACTIVE con cuota vencida",
        }.get(issue_reason, issue_reason)

    def _unique_emails(self, results):
        seen = set()
        emails = []
        for row in results:
            email = (row["email"] or "").strip().lower()
            if email and email not in seen:
                seen.add(email)
                emails.append(email)
        return sorted(emails)

    def _print_email_list(self, results, missing_slugs):
        if missing_slugs:
            self.stderr.write(
                self.style.WARNING(f"Planes no encontrados: {', '.join(missing_slugs)}\n")
            )

        emails = self._unique_emails(results)
        if not emails:
            return

        for email in emails:
            self.stdout.write(f"- {email}")

    def _print_report(self, requested_slugs, results, missing_slugs):
        self.stdout.write(f"\n{'=' * 90}")
        self.stdout.write("Plan financings con problemas de pago")
        self.stdout.write("Criterios: PAYMENT_ISSUE o ACTIVE con next_payment_at vencido")
        self.stdout.write(f"Planes filtrados: {', '.join(requested_slugs)}")
        if missing_slugs:
            self.stdout.write(self.style.WARNING(f"Slugs inexistentes: {', '.join(missing_slugs)}"))
        self.stdout.write(f"{'=' * 90}\n")

        if not results:
            self.stdout.write(
                self.style.SUCCESS("No se encontraron usuarios con problemas de pago para esos planes.\n")
            )
            return

        unique_users = {row["user_id"] for row in results}
        payment_issue_count = sum(1 for row in results if row["issue_reason"] == "payment_issue")
        overdue_count = sum(1 for row in results if row["issue_reason"] == "overdue_payment")

        self.stdout.write(
            self.style.WARNING(
                f"Se encontraron {len(results)} plan financing(s) "
                f"({len(unique_users)} usuario(s) único(s)): "
                f"{payment_issue_count} PAYMENT_ISSUE, {overdue_count} cuota vencida\n"
            )
        )

        for idx, row in enumerate(results, 1):
            full_name = f"{row['first_name']} {row['last_name']}".strip() or "(sin nombre)"
            self.stdout.write(f"{idx}. {full_name} <{row['email']}>")
            self.stdout.write(f"   - Motivo: {self._issue_reason_label(row['issue_reason'])}")
            self.stdout.write(f"   - User ID: {row['user_id']}")
            self.stdout.write(f"   - Plan financing ID: {row['plan_financing_id']}")
            self.stdout.write(f"   - Status: {row['status']}")
            self.stdout.write(f"   - Planes: {', '.join(row['plan_slugs']) or 'N/A'}")
            self.stdout.write(
                f"   - Academia: {row['academy_name']} (slug: {row['academy_slug']}, id: {row['academy_id']})"
            )
            self.stdout.write(
                f"   - Cuotas: {row['installments_paid']}/{row['how_many_installments']} | "
                f"Próximo pago: {row['next_payment_at']} | Válido hasta: {row['valid_until']}"
            )
            if row["status_message"]:
                self.stdout.write(f"   - Mensaje: {row['status_message']}")
            self.stdout.write("")

        self.stdout.write(f"{'=' * 90}")
        self.stdout.write(
            self.style.WARNING(
                f"Total: {len(results)} plan financing(s), {len(unique_users)} usuario(s) único(s)"
            )
        )
        self.stdout.write(f"{'=' * 90}\n")
