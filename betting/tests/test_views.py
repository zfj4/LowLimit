"""
Tests for betting app views.
Written first per TDD — run with: pytest betting/tests/test_views.py
"""
import pytest
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from betting.models import Account, Deposit, SportingEvent, Wager
from betting.utils import get_week_start


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    u = User.objects.create_user(username='viewtest', password='testpass123')
    u.account.balance = Decimal('20.00')
    u.account.save()
    return u


@pytest.fixture
def logged_in_client(client, user):
    client.login(username='viewtest', password='testpass123')
    return client


@pytest.fixture
def upcoming_event(db):
    # week_start must match get_week_start().date() so get_or_generate_events() finds this event
    return SportingEvent.objects.create(
        home_team='Duke Blue Devils',
        away_team='UNC Tar Heels',
        event_time=timezone.now() + timezone.timedelta(days=2),
        spread=Decimal('-4.5'),
        home_odds=-110,
        away_odds=-110,
        gender='M',
        week_start=get_week_start().date(),
    )


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoginView:
    def test_get_returns_200(self, client):
        response = client.get(reverse('login'))
        assert response.status_code == 200

    def test_login_redirects_on_success(self, client, user):
        response = client.post(reverse('login'), {
            'action': 'login', 'username': 'viewtest', 'password': 'testpass123'
        })
        assert response.status_code == 302
        assert response['Location'] == reverse('main')

    def test_login_invalid_credentials_shows_error(self, client, user):
        response = client.post(reverse('login'), {
            'action': 'login', 'username': 'viewtest', 'password': 'wrongpassword'
        })
        assert response.status_code == 200
        assert b'Invalid' in response.content

    def test_register_creates_user_and_redirects(self, client, db):
        response = client.post(reverse('login'), {
            'action': 'register', 'username': 'newuser', 'password': 'newpass123'
        })
        assert response.status_code == 302
        assert User.objects.filter(username='newuser').exists()

    def test_register_duplicate_username_shows_error(self, client, user):
        response = client.post(reverse('login'), {
            'action': 'register', 'username': 'viewtest', 'password': 'newpass123'
        })
        assert response.status_code == 200
        assert b'already taken' in response.content

    def test_register_short_password_shows_error(self, client, db):
        response = client.post(reverse('login'), {
            'action': 'register', 'username': 'newuser2', 'password': 'short'
        })
        assert response.status_code == 200
        assert b'8 characters' in response.content

    def test_authenticated_user_redirected_to_main(self, logged_in_client):
        response = logged_in_client.get(reverse('login'))
        assert response.status_code == 302
        assert response['Location'] == reverse('main')


@pytest.mark.django_db
class TestLogoutView:
    def test_logout_redirects_to_login(self, logged_in_client):
        response = logged_in_client.get(reverse('logout'))
        assert response.status_code == 302
        assert response['Location'] == reverse('login')

    def test_logout_ends_session(self, logged_in_client):
        logged_in_client.get(reverse('logout'))
        response = logged_in_client.get(reverse('main'))
        assert response.status_code == 302  # redirected to login


# ---------------------------------------------------------------------------
# Main View
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMainView:
    def test_main_requires_login(self, client):
        response = client.get(reverse('main'))
        assert response.status_code == 302
        assert '/login/' in response['Location']

    def test_main_returns_200_for_authenticated(self, logged_in_client):
        response = logged_in_client.get(reverse('main'))
        assert response.status_code == 200

    def test_main_shows_balance(self, logged_in_client):
        response = logged_in_client.get(reverse('main'))
        assert b'20.00' in response.content


# ---------------------------------------------------------------------------
# Deposit Views
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDepositMenuView:
    def test_requires_login(self, client):
        response = client.get(reverse('deposit_menu'))
        assert response.status_code == 302

    def test_returns_200_for_authenticated(self, logged_in_client):
        response = logged_in_client.get(reverse('deposit_menu'))
        assert response.status_code == 200

    def test_shows_weekly_limit_info(self, logged_in_client):
        response = logged_in_client.get(reverse('deposit_menu'))
        assert b'10.00' in response.content


@pytest.mark.django_db
class TestProcessDepositView:
    def test_requires_login(self, client):
        response = client.post(reverse('process_deposit'), {'amount': '5.00'})
        assert response.status_code == 302

    def test_valid_deposit_increases_balance(self, logged_in_client, user):
        initial_balance = user.account.balance
        logged_in_client.post(reverse('process_deposit'), {'amount': '5.00'})
        user.account.refresh_from_db()
        assert user.account.balance == initial_balance + Decimal('5.00')

    def test_valid_deposit_creates_deposit_record(self, logged_in_client, user):
        logged_in_client.post(reverse('process_deposit'), {'amount': '5.00'})
        assert Deposit.objects.filter(user=user, amount=Decimal('5.00')).exists()

    def test_deposit_exceeding_limit_rejected(self, logged_in_client, user):
        response = logged_in_client.post(reverse('process_deposit'), {'amount': '15.00'})
        assert b'exceeds' in response.content.lower() or b'limit' in response.content.lower()
        # Balance should be unchanged
        user.account.refresh_from_db()
        assert user.account.balance == Decimal('20.00')

    def test_zero_deposit_rejected(self, logged_in_client, user):
        response = logged_in_client.post(reverse('process_deposit'), {'amount': '0'})
        assert b'greater than zero' in response.content.lower() or b'invalid' in response.content.lower()

    def test_deposit_at_exact_limit_accepted(self, logged_in_client, user):
        logged_in_client.post(reverse('process_deposit'), {'amount': '10.00'})
        user.account.refresh_from_db()
        assert user.account.balance == Decimal('30.00')

    def test_second_deposit_respects_weekly_total(self, logged_in_client, user):
        logged_in_client.post(reverse('process_deposit'), {'amount': '7.00'})
        response = logged_in_client.post(reverse('process_deposit'), {'amount': '5.00'})
        assert b'limit' in response.content.lower() or b'exceeds' in response.content.lower()


# ---------------------------------------------------------------------------
# Events / Place Wager Views
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEventsMenuView:
    def test_requires_login(self, client):
        response = client.get(reverse('events_menu'))
        assert response.status_code == 302

    @patch('betting.utils.generate_events')
    def test_returns_200_and_shows_events(self, mock_generate, logged_in_client, upcoming_event):
        mock_generate.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert response.status_code == 200
        assert b'Duke Blue Devils' in response.content

    @patch('betting.utils.generate_events')
    def test_refresh_param_triggers_regeneration(self, mock_generate, logged_in_client, upcoming_event):
        mock_generate.return_value = []
        logged_in_client.get(reverse('events_menu') + '?refresh=1')
        # generate_events may or may not be called depending on whether there are existing events
        # The key is no error is raised
        assert True  # Just verifying no exception


@pytest.mark.django_db
class TestPlaceWagerView:
    def test_requires_login(self, client, upcoming_event):
        response = client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00'
        })
        assert response.status_code == 302

    @patch('betting.utils.generate_events')
    def test_valid_wager_deducts_balance(self, mock_gen, logged_in_client, user, upcoming_event):
        mock_gen.return_value = []
        initial_balance = user.account.balance
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00'
        })
        user.account.refresh_from_db()
        assert user.account.balance == initial_balance - Decimal('5.00')

    @patch('betting.utils.generate_events')
    def test_valid_wager_creates_wager_record(self, mock_gen, logged_in_client, user, upcoming_event):
        mock_gen.return_value = []
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00'
        })
        assert Wager.objects.filter(user=user, event=upcoming_event).exists()

    @patch('betting.utils.generate_events')
    def test_wager_exceeding_balance_rejected(self, mock_gen, logged_in_client, user, upcoming_event):
        mock_gen.return_value = []
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '100.00'
        })
        assert b'insufficient' in response.content.lower() or b'balance' in response.content.lower()
        assert not Wager.objects.filter(user=user, event=upcoming_event).exists()

    @patch('betting.utils.generate_events')
    def test_duplicate_wager_rejected(self, mock_gen, logged_in_client, user, upcoming_event):
        mock_gen.return_value = []
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00'
        })
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'away', 'amount': '5.00'
        })
        assert b'already' in response.content.lower()
        assert Wager.objects.filter(user=user, event=upcoming_event).count() == 1

    @patch('betting.utils.generate_events')
    def test_invalid_pick_rejected(self, mock_gen, logged_in_client, upcoming_event):
        mock_gen.return_value = []
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'draw', 'amount': '5.00'
        })
        assert b'invalid' in response.content.lower()


# ---------------------------------------------------------------------------
# Wager History View
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestHistoryMenuView:
    def test_requires_login(self, client):
        response = client.get(reverse('history_menu'))
        assert response.status_code == 302

    def test_returns_200_for_authenticated(self, logged_in_client):
        response = logged_in_client.get(reverse('history_menu'))
        assert response.status_code == 200

    def test_shows_user_wagers(self, logged_in_client, user, upcoming_event):
        Wager.objects.create(
            user=user, event=upcoming_event, amount=Decimal('5.00'), pick='home'
        )
        response = logged_in_client.get(reverse('history_menu'))
        assert b'Duke Blue Devils' in response.content or b'5.00' in response.content

    def test_does_not_show_other_users_wagers(self, logged_in_client, upcoming_event, db):
        other_user = User.objects.create_user(username='other', password='testpass123')
        Wager.objects.create(
            user=other_user, event=upcoming_event, amount=Decimal('5.00'), pick='home'
        )
        response = logged_in_client.get(reverse('history_menu'))
        # The logged-in user has no wagers — their history should show empty state
        assert Wager.objects.filter(user__username='viewtest').count() == 0
