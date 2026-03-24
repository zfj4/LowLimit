"""
Tests for betting app models.
Written first per TDD — run with: pytest betting/tests/test_models.py
"""
import pytest
from decimal import Decimal
from django.contrib.auth.models import User
from django.utils import timezone

from betting.models import Account, Deposit, SportingEvent, Wager


@pytest.mark.django_db
class TestAccountAutoCreation:
    """Account is created automatically when a User is created."""

    def test_account_created_on_user_creation(self):
        user = User.objects.create_user(username='testuser', password='testpass123')
        assert hasattr(user, 'account')
        assert isinstance(user.account, Account)

    def test_account_initial_balance_is_zero(self):
        user = User.objects.create_user(username='testuser2', password='testpass123')
        assert user.account.balance == Decimal('0.00')

    def test_one_account_per_user(self):
        user = User.objects.create_user(username='testuser3', password='testpass123')
        assert Account.objects.filter(user=user).count() == 1


@pytest.mark.django_db
class TestDepositModel:
    """Deposit records a user's fund deposit with amount and timestamp."""

    def test_deposit_stores_amount(self):
        user = User.objects.create_user(username='depositor', password='testpass123')
        deposit = Deposit.objects.create(user=user, amount=Decimal('5.00'))
        assert deposit.amount == Decimal('5.00')

    def test_deposit_has_timestamp(self):
        user = User.objects.create_user(username='depositor2', password='testpass123')
        deposit = Deposit.objects.create(user=user, amount=Decimal('5.00'))
        assert deposit.created_at is not None

    def test_deposit_linked_to_user(self):
        user = User.objects.create_user(username='depositor3', password='testpass123')
        Deposit.objects.create(user=user, amount=Decimal('3.00'))
        assert user.deposits.count() == 1


@pytest.mark.django_db
class TestSportingEventModel:
    """SportingEvent stores game details including teams, spread, odds."""

    def setup_method(self):
        self.event_time = timezone.now() + timezone.timedelta(days=2)
        self.event = SportingEvent.objects.create(
            home_team='Duke Blue Devils',
            away_team='UNC Tar Heels',
            event_time=self.event_time,
            spread=Decimal('-4.5'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
        )

    def test_event_stores_teams(self):
        assert self.event.home_team == 'Duke Blue Devils'
        assert self.event.away_team == 'UNC Tar Heels'

    def test_event_default_status_is_upcoming(self):
        assert self.event.status == 'upcoming'

    def test_event_spread_display_home_favored(self):
        home_spread, away_spread = self.event.spread_display()
        assert home_spread == '-4.5'
        assert away_spread == '+4.5'

    def test_event_spread_display_away_favored(self):
        event = SportingEvent.objects.create(
            home_team='Team A',
            away_team='Team B',
            event_time=self.event_time,
            spread=Decimal('3.5'),
            home_odds=-110,
            away_odds=-110,
            gender='W',
            week_start=timezone.now().date(),
        )
        home_spread, away_spread = event.spread_display()
        assert home_spread == '+3.5'
        assert away_spread == '-3.5'

    def test_event_spread_display_pick_em(self):
        event = SportingEvent.objects.create(
            home_team='Team A',
            away_team='Team B',
            event_time=self.event_time,
            spread=Decimal('0.0'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
        )
        home_spread, away_spread = event.spread_display()
        assert home_spread == 'PK'
        assert away_spread == 'PK'

    def test_scores_are_nullable(self):
        assert self.event.home_score is None
        assert self.event.away_score is None


@pytest.mark.django_db
class TestWagerModel:
    """Wager records a user's bet on a sporting event."""

    def setup_method(self):
        self.user = User.objects.create_user(username='wagerer', password='testpass123')
        self.user.account.balance = Decimal('50.00')
        self.user.account.save()
        self.event = SportingEvent.objects.create(
            home_team='Kansas Jayhawks',
            away_team='Iowa State Cyclones',
            event_time=timezone.now() + timezone.timedelta(days=1),
            spread=Decimal('-6.5'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
        )

    def test_wager_stores_amount_and_pick(self):
        wager = Wager.objects.create(
            user=self.user,
            event=self.event,
            amount=Decimal('10.00'),
            pick='home',
        )
        assert wager.amount == Decimal('10.00')
        assert wager.pick == 'home'

    def test_wager_default_status_is_pending(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('5.00'), pick='away'
        )
        assert wager.status == 'pending'

    def test_picked_team_home(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('5.00'), pick='home'
        )
        assert wager.picked_team() == 'Kansas Jayhawks'

    def test_picked_team_away(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('5.00'), pick='away'
        )
        assert wager.picked_team() == 'Iowa State Cyclones'

    def test_net_change_pending_is_none(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        assert wager.net_change() is None

    def test_net_change_won(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home',
            status='won', payout=Decimal('9.09'),
        )
        assert wager.net_change() == Decimal('9.09')

    def test_net_change_lost(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home',
            status='lost', payout=Decimal('0.00'),
        )
        assert wager.net_change() == Decimal('-10.00')

    def test_net_change_push(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home',
            status='push', payout=Decimal('0.00'),
        )
        assert wager.net_change() == Decimal('0.00')


@pytest.mark.django_db
class TestWagerSettlementSignal:
    """When a SportingEvent is marked final with scores, pending wagers are settled."""

    def setup_method(self):
        self.user = User.objects.create_user(username='settler', password='testpass123')
        self.user.account.balance = Decimal('100.00')
        self.user.account.save()
        self.event = SportingEvent.objects.create(
            home_team='Gonzaga Bulldogs',
            away_team='Saint Mary\'s Gaels',
            event_time=timezone.now() - timezone.timedelta(hours=3),
            spread=Decimal('-8.5'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
        )

    def test_winning_home_wager_settled(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        # Home wins by 10, covers -8.5 spread
        self.event.home_score = 85
        self.event.away_score = 75
        self.event.status = 'final'
        self.event.save()

        wager.refresh_from_db()
        assert wager.status == 'won'
        assert wager.payout is not None
        assert wager.payout > 0

    def test_losing_home_wager_settled(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        # Home wins by only 5, doesn't cover -8.5 spread
        self.event.home_score = 80
        self.event.away_score = 75
        self.event.status = 'final'
        self.event.save()

        wager.refresh_from_db()
        assert wager.status == 'lost'

    def test_winning_away_wager_settled(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='away'
        )
        # Home wins by 5, away covers +8.5
        self.event.home_score = 80
        self.event.away_score = 75
        self.event.status = 'final'
        self.event.save()

        wager.refresh_from_db()
        assert wager.status == 'won'

    def test_balance_updated_on_win(self):
        initial_balance = self.user.account.balance
        Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        # Deduct from balance (simulating placing wager)
        self.user.account.balance -= Decimal('10.00')
        self.user.account.save()

        self.event.home_score = 90
        self.event.away_score = 70
        self.event.status = 'final'
        self.event.save()

        self.user.account.refresh_from_db()
        assert self.user.account.balance > initial_balance - Decimal('10.00')

    def test_wager_not_settled_if_no_scores(self):
        wager = Wager.objects.create(
            user=self.user, event=self.event, amount=Decimal('10.00'), pick='home'
        )
        self.event.status = 'final'
        # No scores set — should not settle
        self.event.save()

        wager.refresh_from_db()
        assert wager.status == 'pending'
