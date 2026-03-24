"""
Tests for betting app utility functions.
Written first per TDD — run with: pytest betting/tests/test_utils.py
"""
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.contrib.auth.models import User
from django.utils import timezone

from betting.models import Account, Deposit, SportingEvent, Wager
from betting.utils import (
    get_week_start,
    get_weekly_deposited,
    get_weekly_remaining,
    get_banner_context,
    settle_wager,
    WEEKLY_LIMIT,
)


class TestGetWeekStart:
    """get_week_start() returns Monday 12:01 AM Pacific Time as UTC datetime."""

    def test_returns_aware_datetime(self):
        result = get_week_start()
        assert result.tzinfo is not None

    def test_returns_monday(self):
        import pytz
        pacific = pytz.timezone('America/Los_Angeles')
        result = get_week_start().astimezone(pacific)
        assert result.weekday() == 0  # Monday

    def test_returns_12_01_am(self):
        import pytz
        pacific = pytz.timezone('America/Los_Angeles')
        result = get_week_start().astimezone(pacific)
        assert result.hour == 0
        assert result.minute == 1
        assert result.second == 0

    def test_week_start_is_in_the_past_or_present(self):
        """Week start should always be at or before right now."""
        result = get_week_start()
        assert result <= timezone.now()


@pytest.mark.django_db
class TestGetWeeklyDeposited:
    """get_weekly_deposited() sums deposits made since the week started."""

    def setup_method(self):
        self.user = User.objects.create_user(username='deptest', password='testpass123')

    def test_returns_zero_with_no_deposits(self):
        assert get_weekly_deposited(self.user) == Decimal('0.00')

    def test_sums_current_week_deposits(self):
        Deposit.objects.create(user=self.user, amount=Decimal('5.00'))
        Deposit.objects.create(user=self.user, amount=Decimal('3.00'))
        assert get_weekly_deposited(self.user) == Decimal('8.00')

    def test_excludes_deposits_from_last_week(self):
        """Deposits older than the current week start are not counted."""
        old_deposit = Deposit.objects.create(user=self.user, amount=Decimal('10.00'))
        # Force created_at to be 8 days ago
        Deposit.objects.filter(pk=old_deposit.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=8)
        )
        assert get_weekly_deposited(self.user) == Decimal('0.00')


@pytest.mark.django_db
class TestGetWeeklyRemaining:
    """get_weekly_remaining() returns how much more can be deposited this week."""

    def setup_method(self):
        self.user = User.objects.create_user(username='remtest', password='testpass123')

    def test_full_limit_available_with_no_deposits(self):
        assert get_weekly_remaining(self.user) == WEEKLY_LIMIT

    def test_reduces_by_deposit_amount(self):
        Deposit.objects.create(user=self.user, amount=Decimal('4.00'))
        assert get_weekly_remaining(self.user) == Decimal('6.00')

    def test_never_goes_below_zero(self):
        Deposit.objects.create(user=self.user, amount=Decimal('10.00'))
        assert get_weekly_remaining(self.user) == Decimal('0.00')


@pytest.mark.django_db
class TestGetBannerContext:
    """get_banner_context() returns balance, wagered amount, and weekly deposit info."""

    def setup_method(self):
        self.user = User.objects.create_user(username='bannertest', password='testpass123')
        self.user.account.balance = Decimal('25.00')
        self.user.account.save()

    def test_returns_balance(self):
        ctx = get_banner_context(self.user)
        assert ctx['balance'] == Decimal('25.00')

    def test_returns_zero_wagered_when_no_wagers(self):
        ctx = get_banner_context(self.user)
        assert ctx['wagered_amount'] == Decimal('0.00')
        assert ctx['wager_count'] == 0

    def test_returns_pending_wager_totals(self):
        event = SportingEvent.objects.create(
            home_team='A', away_team='B',
            event_time=timezone.now() + timezone.timedelta(days=1),
            spread=Decimal('-3.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=timezone.now().date(),
        )
        Wager.objects.create(user=self.user, event=event, amount=Decimal('10.00'), pick='home')
        ctx = get_banner_context(self.user)
        assert ctx['wagered_amount'] == Decimal('10.00')
        assert ctx['wager_count'] == 1

    def test_does_not_count_settled_wagers(self):
        event = SportingEvent.objects.create(
            home_team='A', away_team='B',
            event_time=timezone.now() + timezone.timedelta(days=1),
            spread=Decimal('-3.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=timezone.now().date(),
        )
        Wager.objects.create(
            user=self.user, event=event, amount=Decimal('10.00'),
            pick='home', status='won', payout=Decimal('9.09'),
        )
        ctx = get_banner_context(self.user)
        assert ctx['wagered_amount'] == Decimal('0.00')
        assert ctx['wager_count'] == 0

    def test_returns_weekly_deposit_info(self):
        Deposit.objects.create(user=self.user, amount=Decimal('6.00'))
        ctx = get_banner_context(self.user)
        assert ctx['weekly_deposited'] == Decimal('6.00')
        assert ctx['weekly_remaining'] == Decimal('4.00')
        assert ctx['weekly_limit'] == WEEKLY_LIMIT


@pytest.mark.django_db
class TestSettleWager:
    """settle_wager() correctly settles a wager based on event outcome."""

    def setup_method(self):
        self.user = User.objects.create_user(username='settletest', password='testpass123')
        self.user.account.balance = Decimal('0.00')
        self.user.account.save()
        self.event = SportingEvent.objects.create(
            home_team='Home Team',
            away_team='Away Team',
            event_time=timezone.now() - timezone.timedelta(hours=2),
            spread=Decimal('-5.5'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
            status='final',
            home_score=80,
            away_score=70,
        )

    def test_home_wins_covers_spread(self):
        """Home wins by 10, covers -5.5 spread → home pick wins."""
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        wager.refresh_from_db()
        assert wager.status == 'won'

    def test_home_wins_does_not_cover_spread(self):
        """Home wins by 10, but spread is -15.5 → home pick loses."""
        self.event.spread = Decimal('-15.5')
        self.event.save()
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        wager.refresh_from_db()
        assert wager.status == 'lost'

    def test_away_covers_spread(self):
        """Home wins by 3, but spread is -5.5 → away pick wins."""
        self.event.home_score = 73
        self.event.away_score = 70
        self.event.save()
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='away'
        )
        settle_wager(wager)
        wager.refresh_from_db()
        assert wager.status == 'won'

    def test_winner_payout_calculated_at_minus_110(self):
        """Winning -110 wager of $10 profits ~$9.09."""
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        wager.refresh_from_db()
        assert wager.status == 'won'
        expected_profit = (Decimal('10.00') * 100 / 110).quantize(Decimal('0.01'))
        assert wager.payout == expected_profit

    def test_winner_balance_increases(self):
        """Winner gets amount + profit added back to balance."""
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        self.user.account.refresh_from_db()
        # Balance was 0, now should be amount + profit
        assert self.user.account.balance > Decimal('10.00')

    def test_loser_balance_unchanged(self):
        """Loser's balance is not changed (amount was already deducted at placement)."""
        self.event.spread = Decimal('-15.5')
        self.event.save()
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        self.user.account.refresh_from_db()
        assert self.user.account.balance == Decimal('0.00')

    def test_settled_at_timestamp_set(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        settle_wager(wager)
        wager.refresh_from_db()
        assert wager.settled_at is not None
