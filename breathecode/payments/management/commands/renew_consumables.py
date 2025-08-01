from datetime import timedelta
from django.core.management.base import BaseCommand
from ...models import ServiceStockScheduler
from django.utils import timezone
from django.db.models import Max

from ... import tasks


# renew the credits every 1 hours
class Command(BaseCommand):
    help = "Renew credits"

    def handle(self, *args, **options):
        self.utc_now = timezone.now()
        self.subscriptions()
        self.plan_financing()

    def subscriptions(self):
        stock_schedulers_to_renew = (
            ServiceStockScheduler.objects.annotate(last_consumable_valid_until=Max("consumables__valid_until"))
            .filter(
                last_consumable_valid_until__lte=self.utc_now + timedelta(hours=1),
                plan_handler__subscription__next_payment_at__gt=self.utc_now,
            )
            .exclude(plan_handler__subscription__status="CANCELLED")
            .exclude(plan_handler__subscription__status="DEPRECATED")
            .exclude(plan_handler__subscription__status="PAYMENT_ISSUE")
        )

        stock_schedulers_to_renew_ids = list(stock_schedulers_to_renew.values_list("id", flat=True))

        for stock_scheduler_id in stock_schedulers_to_renew_ids:
            tasks.renew_consumables.delay(stock_scheduler_id)

    def plan_financing(self):
        stock_schedulers_to_renew = (
            ServiceStockScheduler.objects.annotate(last_consumable_valid_until=Max("consumables__valid_until"))
            .filter(
                last_consumable_valid_until__lte=self.utc_now + timedelta(hours=2),
                plan_handler__plan_financing__next_payment_at__gt=self.utc_now,
            )
            .exclude(plan_handler__plan_financing__status="CANCELLED")
            .exclude(plan_handler__plan_financing__status="DEPRECATED")
            .exclude(plan_handler__plan_financing__status="PAYMENT_ISSUE")
        )

        stock_schedulers_to_renew_ids = list(stock_schedulers_to_renew.values_list("id", flat=True))

        for stock_scheduler_id in stock_schedulers_to_renew_ids:
            tasks.renew_consumables.delay(stock_scheduler_id)
