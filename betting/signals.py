from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Account, SportingEvent


@receiver(post_save, sender=User)
def create_account_for_new_user(sender, instance, created, **kwargs):
    if created:
        Account.objects.create(user=instance)


@receiver(post_save, sender=SportingEvent)
def settle_wagers_on_event_completion(sender, instance, **kwargs):
    """Automatically settle pending wagers when an event is marked final with scores."""
    if (
        instance.status == 'final'
        and instance.home_score is not None
        and instance.away_score is not None
    ):
        from .utils import settle_wager
        for wager in instance.wagers.filter(status='pending'):
            settle_wager(wager)
