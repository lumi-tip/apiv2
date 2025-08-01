import logging
from typing import Type

from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.contrib.auth.models import Group

from breathecode.authenticate.models import GoogleWebhook
from breathecode.authenticate.signals import google_webhook_saved
from breathecode.mentorship.models import MentorshipSession
from breathecode.mentorship.signals import mentorship_session_status
from breathecode.payments import tasks

from .models import Consumable, Plan, PlanFinancing, Subscription
from .signals import (
    consume_service,
    grant_service_permissions,
    lose_service_permissions,
    reimburse_service_units,
    update_plan_m2m_service_items,
    grant_plan_permissions,
    revoke_plan_permissions,
)

logger = logging.getLogger(__name__)


@receiver(consume_service, sender=Consumable)
def consume_service_receiver(sender: Type[Consumable], instance: Consumable, how_many: float, **kwargs):
    if instance.how_many == 0:
        lose_service_permissions.send_robust(instance=instance, sender=sender)
        return

    if instance.how_many == -1:
        return

    instance.how_many -= how_many
    instance.save()

    if instance.how_many == 0:
        lose_service_permissions.send_robust(instance=instance, sender=sender)


@receiver(reimburse_service_units, sender=Consumable)
def reimburse_service_units_receiver(sender: Type[Consumable], instance: Consumable, how_many: float, **kwargs):
    if instance.how_many == -1:
        return

    grant_permissions = not instance.how_many and how_many

    instance.how_many += how_many
    instance.save()

    if grant_permissions:
        grant_service_permissions.send_robust(instance=instance, sender=sender)


@receiver(lose_service_permissions, sender=Consumable)
def lose_service_permissions_receiver(sender: Type[Consumable], instance: Consumable, **kwargs):
    now = timezone.now()

    if instance.how_many != 0:
        return

    consumables = Consumable.objects.filter(Q(valid_until__lte=now) | Q(valid_until=None), user=instance.user).exclude(
        how_many=0
    )

    # for group in instance.user.groups.all():
    for group in instance.service_item.service.groups.all():
        # if group ==
        how_many = consumables.filter(service_item__service__groups__name=group.name).distinct().count()
        if how_many == 0:
            instance.user.groups.remove(group)


@receiver(grant_service_permissions, sender=Consumable)
def grant_service_permissions_receiver(sender: Type[Consumable], instance: Consumable, **kwargs):
    groups = instance.service_item.service.groups.all()

    for group in groups:
        if not instance.user.groups.filter(name=group.name).exists():
            instance.user.groups.add(group)


@receiver(grant_plan_permissions, sender=Subscription)
@receiver(grant_plan_permissions, sender=PlanFinancing)
def grant_plan_permissions_receiver(
    sender: Type[Subscription] | Type[PlanFinancing], instance: Subscription | PlanFinancing, **kwargs
):
    """
    Add the user to the Paid Student group when a subscription/plan financing is created
    or when its status changes to ACTIVE
    """
    group = Group.objects.filter(name="Paid Student").first()
    if group and not instance.user.groups.filter(name="Paid Student").exists():
        instance.user.groups.add(group)


@receiver(revoke_plan_permissions, sender=Subscription)
@receiver(revoke_plan_permissions, sender=PlanFinancing)
def revoke_plan_permissions_receiver(sender, instance, **kwargs):
    """
    Remove the user from the Paid Student group only if the user has
    NO other active subscriptions or plan financings.
    """
    group = Group.objects.filter(name="Paid Student").first()
    user = instance.user

    has_active_subscription = Subscription.objects.filter(user=user, status=Subscription.Status.ACTIVE).exists()
    has_active_planfinancing = PlanFinancing.objects.filter(user=user, status=PlanFinancing.Status.ACTIVE).exists()

    if group and user.groups.filter(name="Paid Student").exists():
        if not (has_active_subscription or has_active_planfinancing):
            user.groups.remove(group)


@receiver(mentorship_session_status, sender=MentorshipSession)
def post_mentoring_session_ended(sender: Type[MentorshipSession], instance: MentorshipSession, **kwargs):
    if instance.mentee and instance.service and instance.status in ["FAILED", "IGNORED"]:
        tasks.refund_mentoring_session.delay(instance.id)


@receiver(m2m_changed, sender=Plan.service_items.through)
def plan_m2m_wrapper(sender: Type[Plan.service_items.through], instance: Plan, **kwargs):
    if kwargs["action"] != "post_add":
        return

    update_plan_m2m_service_items.send_robust(sender=sender, instance=instance)


@receiver(update_plan_m2m_service_items, sender=Plan.service_items.through)
def plan_m2m_changed(sender: Type[Plan.service_items.through], instance: Plan, **kwargs):
    tasks.update_service_stock_schedulers.delay(instance.id)


@receiver(google_webhook_saved, sender=GoogleWebhook)
def process_google_webhook_on_created(sender: Type[GoogleWebhook], instance: GoogleWebhook, created: bool, **kwargs):
    if created:
        tasks.process_google_webhook.delay(instance.id)
