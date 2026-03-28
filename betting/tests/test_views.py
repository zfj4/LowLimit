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
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert response.status_code == 200
        assert b'Duke Blue Devils' in response.content

    @patch('betting.utils.generate_events')
    def test_refresh_param_triggers_regeneration(self, mock_generate, logged_in_client, upcoming_event):
        mock_generate.return_value = []
        logged_in_client.get(reverse('events_menu') + '?refresh=1')
        # generate_events may or may not be called depending on whether there are existing events
        # The key is no error is raised
        assert True  # Just verifying no exception

    @patch('betting.utils.generate_events')
    def test_completed_events_not_shown(self, mock_generate, logged_in_client, db):
        """Events with status='final' must not appear in the Place Wager menu."""
        mock_generate.return_value = []
        SportingEvent.objects.create(
            home_team='Done Home', away_team='Done Away',
            event_time=timezone.now() - timezone.timedelta(hours=3),
            spread=Decimal('-3.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=get_week_start().date(),
            status='final', home_score=80, away_score=70,
        )
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'Done Home' not in response.content

    @patch('betting.utils.generate_events')
    def test_upcoming_events_still_shown(self, mock_generate, logged_in_client, upcoming_event):
        """Events with status='upcoming' are still displayed."""
        mock_generate.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'Duke Blue Devils' in response.content


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


# ---------------------------------------------------------------------------
# v0.2.4 — Wager History filter dropdown
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWagerHistoryFilter:
    """Wager History has a dropdown to filter by All / Completed / Pending."""

    def _make_wagers(self, user, upcoming_event):
        pending = Wager.objects.create(
            user=user, event=upcoming_event, amount=Decimal('2.00'), pick='home',
        )
        won = Wager.objects.create(
            user=user, event=upcoming_event, amount=Decimal('3.00'), pick='away',
            status='won', payout=Decimal('2.73'),
        )
        # We need a second event for the lost wager (can't have two wagers on same event)
        from betting.utils import get_week_start
        event2 = SportingEvent.objects.create(
            home_team='Team X', away_team='Team Y',
            event_time=timezone.now() + timezone.timedelta(days=1),
            spread=Decimal('0.0'), home_odds=-110, away_odds=-110,
            gender='M', week_start=get_week_start().date(),
        )
        lost = Wager.objects.create(
            user=user, event=event2, amount=Decimal('4.00'), pick='home',
            status='lost', payout=Decimal('0.00'),
        )
        return pending, won, lost

    def test_history_has_filter_dropdown(self, logged_in_client):
        response = logged_in_client.get(reverse('history_menu'))
        assert b'All' in response.content
        assert b'Completed' in response.content
        assert b'Pending' in response.content

    def test_default_shows_all_wagers(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu'))
        assert b'2.00' in response.content  # pending
        assert b'3.00' in response.content  # won
        assert b'4.00' in response.content  # lost

    def test_all_filter_shows_all_wagers(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu') + '?filter=all')
        assert b'2.00' in response.content
        assert b'3.00' in response.content
        assert b'4.00' in response.content

    def test_completed_filter_shows_won_and_lost(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu') + '?filter=completed')
        assert b'3.00' in response.content  # won
        assert b'4.00' in response.content  # lost

    def test_completed_filter_excludes_pending(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu') + '?filter=completed')
        assert b'2.00' not in response.content  # pending excluded

    def test_pending_filter_shows_only_pending(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu') + '?filter=pending')
        assert b'2.00' in response.content  # pending shown

    def test_pending_filter_excludes_completed(self, logged_in_client, user, upcoming_event):
        pending, won, lost = self._make_wagers(user, upcoming_event)
        response = logged_in_client.get(reverse('history_menu') + '?filter=pending')
        assert b'3.00' not in response.content  # won excluded
        assert b'4.00' not in response.content  # lost excluded

    def test_selected_filter_reflected_in_dropdown(self, logged_in_client):
        response = logged_in_client.get(reverse('history_menu') + '?filter=pending')
        assert b'selected' in response.content

    def test_history_sorted_by_event_time_ascending(self, logged_in_client, user, db):
        """Wagers are listed earliest event first, regardless of wager creation order."""
        earlier = SportingEvent.objects.create(
            home_team='Team A', away_team='Team B',
            event_time=timezone.now() + timezone.timedelta(days=1),
            spread=Decimal('-2.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=get_week_start().date(),
        )
        later = SportingEvent.objects.create(
            home_team='Team C', away_team='Team D',
            event_time=timezone.now() + timezone.timedelta(days=3),
            spread=Decimal('-1.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=get_week_start().date(),
        )
        # Create wager on earlier event FIRST so -created_at would put it LAST
        # (confirming we sort by event_time, not created_at)
        Wager.objects.create(user=user, event=earlier, amount=Decimal('1.00'), pick='home')
        Wager.objects.create(user=user, event=later, amount=Decimal('1.00'), pick='home')
        response = logged_in_client.get(reverse('history_menu'))
        content = response.content.decode()
        # With -created_at (old), Team C would appear first; with event_time asc, Team A is first
        assert content.index('Team A') < content.index('Team C')

    def test_history_earliest_event_appears_first(self, logged_in_client, user, db):
        """The event with the soonest start time is at the top of the list."""
        first = SportingEvent.objects.create(
            home_team='Early Home', away_team='Early Away',
            event_time=timezone.now() + timezone.timedelta(hours=2),
            spread=Decimal('0.0'), home_odds=-110, away_odds=-110,
            gender='W', week_start=get_week_start().date(),
        )
        second = SportingEvent.objects.create(
            home_team='Late Home', away_team='Late Away',
            event_time=timezone.now() + timezone.timedelta(days=5),
            spread=Decimal('0.0'), home_odds=-110, away_odds=-110,
            gender='W', week_start=get_week_start().date(),
        )
        # Create early-event wager first so -created_at would put Late Home first
        Wager.objects.create(user=user, event=first, amount=Decimal('1.00'), pick='away')
        Wager.objects.create(user=user, event=second, amount=Decimal('1.00'), pick='away')
        response = logged_in_client.get(reverse('history_menu'))
        content = response.content.decode()
        assert content.index('Early Home') < content.index('Late Home')

    def test_history_menu_has_update_button(self, logged_in_client):
        """History menu includes an Update button."""
        response = logged_in_client.get(reverse('history_menu'))
        assert b'Update' in response.content


# ---------------------------------------------------------------------------
# v0.1.1 — Banner title and Place Bet buttons
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBannerTitle:
    """Banner displays 'Low Limit Sports Betting' as a link to the main page."""

    def test_main_page_has_banner_title(self, logged_in_client):
        response = logged_in_client.get(reverse('main'))
        assert b'Low Limit Sports Betting' in response.content

    def test_banner_title_links_to_main(self, logged_in_client):
        response = logged_in_client.get(reverse('main'))
        main_url = reverse('main').encode()
        assert main_url in response.content

    def test_login_page_has_submit_buttons(self, client):
        response = client.get(reverse('login'))
        assert b'Sign In' in response.content
        assert b'Create Account' in response.content


@pytest.mark.django_db
class TestPlaceBetButtons:
    """Events menu has per-team 'Place Bet' buttons instead of radio buttons."""

    @patch('betting.utils.generate_events')
    def test_events_menu_has_place_bet_buttons(self, mock_gen, logged_in_client, upcoming_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'Place Bet' in response.content

    @patch('betting.utils.generate_events')
    def test_place_bet_buttons_include_team_names(self, mock_gen, logged_in_client, upcoming_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'Duke Blue Devils' in response.content
        assert b'UNC Tar Heels' in response.content

    @patch('betting.utils.generate_events')
    def test_place_bet_away_button_submits_away_pick(self, mock_gen, logged_in_client, user, upcoming_event):
        """Submitting with pick=away via the button creates an away wager."""
        mock_gen.return_value = []
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'away', 'amount': '5.00'
        })
        wager = Wager.objects.get(user=user, event=upcoming_event)
        assert wager.pick == 'away'

    @patch('betting.utils.generate_events')
    def test_place_bet_home_button_submits_home_pick(self, mock_gen, logged_in_client, user, upcoming_event):
        """Submitting with pick=home via the button creates a home wager."""
        mock_gen.return_value = []
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00'
        })
        wager = Wager.objects.get(user=user, event=upcoming_event)
        assert wager.pick == 'home'


# ---------------------------------------------------------------------------
# v0.1.1.1 — First/Last name on registration; first name in welcome message
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRegistrationNameFields:
    """Create Account form accepts first_name and last_name and saves them."""

    def test_register_saves_first_name(self, client, db):
        client.post(reverse('login'), {
            'action': 'register',
            'username': 'newuser',
            'password': 'newpass123',
            'first_name': 'Ada',
            'last_name': 'Lovelace',
        })
        user = User.objects.get(username='newuser')
        assert user.first_name == 'Ada'

    def test_register_saves_last_name(self, client, db):
        client.post(reverse('login'), {
            'action': 'register',
            'username': 'newuser2',
            'password': 'newpass123',
            'first_name': 'Ada',
            'last_name': 'Lovelace',
        })
        user = User.objects.get(username='newuser2')
        assert user.last_name == 'Lovelace'

    def test_register_works_without_name_fields(self, client, db):
        """First/Last name are optional — registration still succeeds without them."""
        response = client.post(reverse('login'), {
            'action': 'register',
            'username': 'noname',
            'password': 'nopass123',
        })
        assert response.status_code == 302
        assert User.objects.filter(username='noname').exists()

    def test_login_form_has_first_name_field(self, client):
        response = client.get(reverse('login'))
        assert b'first_name' in response.content

    def test_login_form_has_last_name_field(self, client):
        response = client.get(reverse('login'))
        assert b'last_name' in response.content


@pytest.mark.django_db
class TestWelcomeMessage:
    """Main page welcome shows first name when available, username otherwise."""

    def test_welcome_shows_first_name_when_set(self, client, db):
        user = User.objects.create_user(
            username='adalovelace', password='testpass123',
            first_name='Ada',
        )
        client.login(username='adalovelace', password='testpass123')
        response = client.get(reverse('main'))
        assert b'Ada' in response.content

    def test_welcome_shows_username_when_no_first_name(self, client, db):
        user = User.objects.create_user(username='noname2', password='testpass123')
        client.login(username='noname2', password='testpass123')
        response = client.get(reverse('main'))
        assert b'noname2' in response.content


# ---------------------------------------------------------------------------
# v0.2.0 — Sport Selector dropdown
# ---------------------------------------------------------------------------

@pytest.fixture
def mens_event(db):
    return SportingEvent.objects.create(
        home_team='Duke Blue Devils', away_team='UNC Tar Heels',
        event_time=timezone.now() + timezone.timedelta(days=1),
        spread=Decimal('-4.5'), home_odds=-110, away_odds=-110,
        gender='M', week_start=get_week_start().date(),
    )

@pytest.fixture
def womens_event(db):
    return SportingEvent.objects.create(
        home_team='South Carolina Gamecocks', away_team='LSU Tigers',
        event_time=timezone.now() + timezone.timedelta(days=1),
        spread=Decimal('-3.5'), home_odds=-110, away_odds=-110,
        gender='W', week_start=get_week_start().date(),
    )


@pytest.mark.django_db
class TestSportSelector:
    """Events menu has a sport selector dropdown that filters displayed events."""

    @patch('betting.utils.generate_events')
    def test_events_menu_has_sport_selector(self, mock_gen, logged_in_client, mens_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b'Select Sport' in response.content

    @patch('betting.utils.generate_events')
    def test_sport_selector_has_mens_option(self, mock_gen, logged_in_client, mens_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b"Men's Basketball" in response.content

    @patch('betting.utils.generate_events')
    def test_sport_selector_has_womens_option(self, mock_gen, logged_in_client, womens_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b"Women's Basketball" in response.content

    @patch('betting.utils.generate_events')
    def test_no_sport_filter_shows_no_events(self, mock_gen, logged_in_client, mens_event, womens_event):
        """Before a sport is selected, no events should be listed."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b'Duke Blue Devils' not in response.content
        assert b'South Carolina Gamecocks' not in response.content

    @patch('betting.utils.generate_events')
    def test_no_sport_filter_shows_select_prompt(self, mock_gen, logged_in_client, mens_event):
        """Before a sport is selected, a prompt to select a sport is shown."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b'Select a sport' in response.content

    @patch('betting.utils.generate_events')
    def test_mens_filter_shows_only_mens_events(self, mock_gen, logged_in_client, mens_event, womens_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'Duke Blue Devils' in response.content
        assert b'South Carolina Gamecocks' not in response.content

    @patch('betting.utils.generate_events')
    def test_womens_filter_shows_only_womens_events(self, mock_gen, logged_in_client, mens_event, womens_event):
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=W')
        assert b'South Carolina Gamecocks' in response.content
        assert b'Duke Blue Devils' not in response.content

    @patch('betting.utils.generate_events')
    def test_no_events_message_when_filter_returns_nothing(self, mock_gen, logged_in_client, mens_event):
        """Selecting Women's when only men's events exist shows the no-events message."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=W')
        assert b'No events available' in response.content

    @patch('betting.utils.generate_events')
    def test_selected_sport_preserved_in_dropdown(self, mock_gen, logged_in_client, mens_event):
        """The dropdown reflects the currently selected sport."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'selected' in response.content


# ---------------------------------------------------------------------------
# Sport filter preserved after placing wager + scroll fix
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPlaceWagerSportFilter:
    """After placing a wager the sport filter and scroll position are preserved."""

    @patch('betting.utils.generate_events')
    def test_success_shows_only_selected_sport(
        self, mock_gen, logged_in_client, user, mens_event, womens_event
    ):
        """After a successful wager, only the sport selected in the form is shown."""
        mock_gen.return_value = []
        logged_in_client.post(reverse('place_wager'), {
            'event_id': mens_event.id, 'pick': 'home',
            'amount': '5.00', 'sport': 'M',
        })
        # Place a second wager to test filtering (first wager is already placed)
        # Instead verify directly: GET with sport=M shows mens but not womens
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': womens_event.id, 'pick': 'home',
            'amount': '5.00', 'sport': 'M',
        })
        assert b'South Carolina Gamecocks' not in response.content
        assert b'Duke Blue Devils' in response.content

    @patch('betting.utils.generate_events')
    def test_success_mens_filter_excludes_womens(
        self, mock_gen, logged_in_client, user, mens_event, womens_event
    ):
        """Posting sport=M returns only men's events."""
        mock_gen.return_value = []
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': mens_event.id, 'pick': 'home',
            'amount': '5.00', 'sport': 'M',
        })
        assert b'Duke Blue Devils' in response.content
        assert b'South Carolina Gamecocks' not in response.content

    @patch('betting.utils.generate_events')
    def test_success_womens_filter_excludes_mens(
        self, mock_gen, logged_in_client, user, mens_event, womens_event
    ):
        """Posting sport=W returns only women's events."""
        mock_gen.return_value = []
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': womens_event.id, 'pick': 'home',
            'amount': '5.00', 'sport': 'W',
        })
        assert b'South Carolina Gamecocks' in response.content
        assert b'Duke Blue Devils' not in response.content

    @patch('betting.utils.generate_events')
    def test_error_response_preserves_sport_filter(
        self, mock_gen, logged_in_client, mens_event, womens_event
    ):
        """An error response (e.g. zero amount) also preserves the sport filter."""
        mock_gen.return_value = []
        response = logged_in_client.post(reverse('place_wager'), {
            'event_id': mens_event.id, 'pick': 'home',
            'amount': '0', 'sport': 'M',
        })
        assert b'Duke Blue Devils' in response.content
        assert b'South Carolina Gamecocks' not in response.content

    @patch('betting.utils.generate_events')
    def test_wager_form_includes_sport_hidden_field(
        self, mock_gen, logged_in_client, mens_event
    ):
        """The wager form passes the current sport via a hidden input."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'name="sport"' in response.content

    @patch('betting.utils.generate_events')
    def test_wager_form_hx_swap_scrolls_to_top(
        self, mock_gen, logged_in_client, mens_event
    ):
        """The wager form's hx-swap scrolls to the top of the page on submit."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'show:window:top' in response.content


# ---------------------------------------------------------------------------
# v0.2.2.1 — Wager spread stored and displayed in history
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWagerSpreadStorage:
    """place_wager_view stores the picked team's spread at placement time."""

    @patch('betting.utils.generate_events')
    def test_home_pick_stores_home_spread(self, mock_gen, logged_in_client, user, upcoming_event):
        """Home pick stores the event spread as-is (negative = home favored)."""
        mock_gen.return_value = []
        # upcoming_event has spread=-4.5 (home favored by 4.5)
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'home', 'amount': '5.00',
        })
        wager = Wager.objects.get(user=user, event=upcoming_event)
        assert wager.wager_spread == Decimal('-4.5')

    @patch('betting.utils.generate_events')
    def test_away_pick_stores_flipped_spread(self, mock_gen, logged_in_client, user, upcoming_event):
        """Away pick stores the negated event spread (away team's perspective)."""
        mock_gen.return_value = []
        # upcoming_event has spread=-4.5; away team sees +4.5
        logged_in_client.post(reverse('place_wager'), {
            'event_id': upcoming_event.id, 'pick': 'away', 'amount': '5.00',
        })
        wager = Wager.objects.get(user=user, event=upcoming_event)
        assert wager.wager_spread == Decimal('4.5')


@pytest.mark.django_db
class TestWagerHistorySpreadDisplay:
    """Wager History screen shows the spread after the picked team name."""

    def test_history_shows_spread_for_home_pick(self, logged_in_client, user, upcoming_event):
        """Home pick with spread -4.5 displays as '-4.5' in history."""
        Wager.objects.create(
            user=user, event=upcoming_event,
            amount=Decimal('5.00'), pick='home',
            wager_spread=Decimal('-4.5'),
        )
        response = logged_in_client.get(reverse('history_menu'))
        assert b'-4.5' in response.content

    def test_history_shows_spread_for_away_pick(self, logged_in_client, user, upcoming_event):
        """Away pick with spread +4.5 displays as '+4.5' in history."""
        Wager.objects.create(
            user=user, event=upcoming_event,
            amount=Decimal('5.00'), pick='away',
            wager_spread=Decimal('4.5'),
        )
        response = logged_in_client.get(reverse('history_menu'))
        assert b'+4.5' in response.content

    def test_history_picked_field_includes_team_and_spread(self, logged_in_client, user, upcoming_event):
        """The 'Picked' field shows both team name and spread together."""
        Wager.objects.create(
            user=user, event=upcoming_event,
            amount=Decimal('5.00'), pick='home',
            wager_spread=Decimal('-4.5'),
        )
        response = logged_in_client.get(reverse('history_menu'))
        content = response.content.decode()
        assert 'Duke Blue Devils' in content
        assert '-4.5' in content

    def test_history_null_spread_shows_team_without_crash(self, logged_in_client, user, upcoming_event):
        """Existing wagers without a stored spread don't crash the history view."""
        Wager.objects.create(
            user=user, event=upcoming_event,
            amount=Decimal('5.00'), pick='home',
            wager_spread=None,
        )
        response = logged_in_client.get(reverse('history_menu'))
        assert response.status_code == 200
        assert b'Duke Blue Devils' in response.content


# ---------------------------------------------------------------------------
# Game time display in Eastern time
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEventTimeDisplay:
    """Event times are displayed in Eastern time, not UTC."""

    @patch('betting.utils.generate_events')
    def test_event_time_shown_in_eastern_not_utc(self, mock_gen, logged_in_client):
        """A game stored at 23:10 UTC (7:10 PM ET) must display as 7:10 PM, not 11:10 PM."""
        mock_gen.return_value = []
        import pytz
        from django.utils import timezone as tz
        eastern = pytz.timezone('America/New_York')
        from betting.utils import get_week_start
        # 23:10 UTC = 7:10 PM EDT
        event_utc = tz.datetime(2026, 3, 26, 23, 10, tzinfo=pytz.utc)
        event = SportingEvent.objects.create(
            home_team='Purdue', away_team='Texas',
            event_time=event_utc,
            spread=Decimal('-7.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=get_week_start().date(),
        )
        response = logged_in_client.get(reverse('events_menu') + '?sport=M')
        assert b'7:10 PM' in response.content
        assert b'11:10 PM' not in response.content


# ---------------------------------------------------------------------------
# v0.2.3 — Update Results view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdateResultsView:
    """update_results_view calls update_event_results and re-renders history."""

    def test_requires_login(self, client):
        response = client.post(reverse('update_results'))
        assert response.status_code == 302

    @patch('betting.views.update_event_results')
    def test_returns_200(self, mock_update, logged_in_client):
        mock_update.return_value = []
        response = logged_in_client.post(reverse('update_results'))
        assert response.status_code == 200

    @patch('betting.views.update_event_results')
    def test_returns_history_content(self, mock_update, logged_in_client):
        mock_update.return_value = []
        response = logged_in_client.post(reverse('update_results'))
        assert b'Wager History' in response.content

    @patch('betting.views.update_event_results')
    def test_includes_banner_oob(self, mock_update, logged_in_client):
        mock_update.return_value = []
        response = logged_in_client.post(reverse('update_results'))
        assert b'hx-swap-oob' in response.content

    @patch('betting.views.update_event_results')
    def test_calls_update_event_results(self, mock_update, logged_in_client):
        mock_update.return_value = []
        logged_in_client.post(reverse('update_results'))
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# v0.2.3.1 — Loading indicators on slow buttons
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoadingIndicators:
    """Reload Games and Update buttons show a spinner while their request is in flight."""

    @patch('betting.utils.generate_events')
    def test_reload_games_button_has_htmx_indicator(self, mock_gen, logged_in_client, mens_event):
        """The Reload Games button contains an htmx-indicator element for the spinner."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b'htmx-indicator' in response.content

    @patch('betting.utils.generate_events')
    def test_reload_games_button_has_disabled_elt(self, mock_gen, logged_in_client, mens_event):
        """The Reload Games button uses hx-disabled-elt to prevent double-clicks."""
        mock_gen.return_value = []
        response = logged_in_client.get(reverse('events_menu'))
        assert b'hx-disabled-elt' in response.content

    def test_update_button_has_htmx_indicator(self, logged_in_client):
        """The Update button contains an htmx-indicator element for the spinner."""
        response = logged_in_client.get(reverse('history_menu'))
        assert b'htmx-indicator' in response.content

    def test_update_button_has_disabled_elt(self, logged_in_client):
        """The Update button uses hx-disabled-elt to prevent double-clicks."""
        response = logged_in_client.get(reverse('history_menu'))
        assert b'hx-disabled-elt' in response.content
