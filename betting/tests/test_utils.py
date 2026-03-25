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
    fetch_espn_schedule,
    generate_odds_for_games,
    generate_events,
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


# ---------------------------------------------------------------------------
# v0.2.1 — ESPN schedule scraping + AI odds
# ---------------------------------------------------------------------------

ESPN_MENS_RESPONSE = {
    "events": [
        {
            "id": "401234567",
            "date": "2025-03-25T23:00Z",
            "name": "Duke Blue Devils at North Carolina Tar Heels",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "North Carolina Tar Heels"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Duke Blue Devils"},
                        },
                    ],
                    "status": {"type": {"name": "STATUS_SCHEDULED", "completed": False}},
                }
            ],
        }
    ]
}

ESPN_WOMENS_RESPONSE = {
    "events": [
        {
            "id": "401234568",
            "date": "2025-03-26T01:00Z",
            "name": "South Carolina Gamecocks at LSU Tigers",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "LSU Tigers"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "South Carolina Gamecocks"},
                        },
                    ],
                    "status": {"type": {"name": "STATUS_SCHEDULED", "completed": False}},
                }
            ],
        }
    ]
}


class TestFetchEspnSchedule:
    """fetch_espn_schedule() returns real game data from ESPN's public API."""

    def _make_mock_response(self, json_data):
        mock = MagicMock()
        mock.json.return_value = json_data
        mock.raise_for_status.return_value = None
        return mock

    @patch('requests.get')
    def test_returns_list_of_games(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response(ESPN_MENS_RESPONSE),
            self._make_mock_response(ESPN_WOMENS_RESPONSE),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert isinstance(games, list)
        assert len(games) == 2

    @patch('requests.get')
    def test_game_has_required_fields(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response(ESPN_MENS_RESPONSE),
            self._make_mock_response({'events': []}),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        game = games[0]
        assert 'home_team' in game
        assert 'away_team' in game
        assert 'event_datetime' in game
        assert 'gender' in game

    @patch('requests.get')
    def test_mens_games_have_gender_M(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response(ESPN_MENS_RESPONSE),
            self._make_mock_response({'events': []}),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['gender'] == 'M'

    @patch('requests.get')
    def test_womens_games_have_gender_W(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response({'events': []}),
            self._make_mock_response(ESPN_WOMENS_RESPONSE),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['gender'] == 'W'

    @patch('requests.get')
    def test_correct_home_away_teams(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response(ESPN_MENS_RESPONSE),
            self._make_mock_response({'events': []}),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['home_team'] == 'North Carolina Tar Heels'
        assert games[0]['away_team'] == 'Duke Blue Devils'

    @patch('requests.get')
    def test_handles_empty_response(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response({'events': []}),
            self._make_mock_response({'events': []}),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games == []

    @patch('requests.get')
    def test_handles_request_error_gracefully(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.RequestException("Network error")
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games == []

    @patch('requests.get')
    def test_skips_non_scheduled_games(self, mock_get):
        """Games already in progress or finished should not be included."""
        finished_event = {
            "events": [
                {
                    "id": "401234569",
                    "date": "2025-03-24T20:00Z",
                    "name": "Team A at Team B",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "team": {"displayName": "Team B"}},
                                {"homeAway": "away", "team": {"displayName": "Team A"}},
                            ],
                            "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                        }
                    ],
                }
            ]
        }
        mock_get.side_effect = [
            self._make_mock_response(finished_event),
            self._make_mock_response({'events': []}),
        ]
        from datetime import date
        games = fetch_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games == []


class TestGenerateOddsForGames:
    """generate_odds_for_games() uses AI to add spreads/odds to real game data."""

    SAMPLE_GAMES = [
        {'home_team': 'North Carolina Tar Heels', 'away_team': 'Duke Blue Devils',
         'event_datetime': '2025-03-25T23:00Z', 'gender': 'M'},
        {'home_team': 'LSU Tigers', 'away_team': 'South Carolina Gamecocks',
         'event_datetime': '2025-03-26T01:00Z', 'gender': 'W'},
    ]

    @patch('google.genai.Client')
    def test_returns_games_with_odds(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = '[{"spread": -4.5, "home_odds": -110, "away_odds": -110}, {"spread": -3.5, "home_odds": -115, "away_odds": -105}]'
        mock_client.models.generate_content.return_value = mock_response

        result = generate_odds_for_games(self.SAMPLE_GAMES)
        assert len(result) == 2
        assert 'spread' in result[0]
        assert 'home_odds' in result[0]
        assert 'away_odds' in result[0]

    @patch('google.genai.Client')
    def test_preserves_game_fields(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = '[{"spread": -4.5, "home_odds": -110, "away_odds": -110}]'
        mock_client.models.generate_content.return_value = mock_response

        result = generate_odds_for_games(self.SAMPLE_GAMES[:1])
        assert result[0]['home_team'] == 'North Carolina Tar Heels'
        assert result[0]['away_team'] == 'Duke Blue Devils'
        assert result[0]['gender'] == 'M'

    @patch('google.genai.Client')
    def test_handles_empty_game_list(self, mock_client_cls):
        result = generate_odds_for_games([])
        assert result == []

    @patch('google.genai.Client')
    def test_uses_default_odds_on_ai_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.side_effect = Exception("API error")

        result = generate_odds_for_games(self.SAMPLE_GAMES[:1])
        assert len(result) == 1
        assert result[0]['home_odds'] == -110
        assert result[0]['away_odds'] == -110

    @patch('google.genai.Client')
    def test_uses_default_odds_on_bad_json(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = 'Sorry, I cannot provide that.'
        mock_client.models.generate_content.return_value = mock_response

        result = generate_odds_for_games(self.SAMPLE_GAMES[:1])
        assert len(result) == 1
        assert result[0]['spread'] == 0


class TestGenerateEvents:
    """generate_events() combines ESPN schedule with AI-generated odds."""

    @patch('betting.utils.generate_odds_for_games')
    @patch('betting.utils.fetch_espn_schedule')
    def test_calls_espn_schedule(self, mock_fetch, mock_odds):
        mock_fetch.return_value = []
        mock_odds.return_value = []
        generate_events()
        assert mock_fetch.called

    @patch('betting.utils.generate_odds_for_games')
    @patch('betting.utils.fetch_espn_schedule')
    def test_calls_generate_odds_with_espn_games(self, mock_fetch, mock_odds):
        espn_games = [
            {'home_team': 'Team A', 'away_team': 'Team B',
             'event_datetime': '2025-03-25T23:00Z', 'gender': 'M'},
        ]
        mock_fetch.return_value = espn_games
        mock_odds.return_value = espn_games
        generate_events()
        mock_odds.assert_called_once_with(espn_games)

    @patch('betting.utils.generate_odds_for_games')
    @patch('betting.utils.fetch_espn_schedule')
    def test_returns_combined_result(self, mock_fetch, mock_odds):
        espn_games = [
            {'home_team': 'Team A', 'away_team': 'Team B',
             'event_datetime': '2025-03-25T23:00Z', 'gender': 'M'},
        ]
        games_with_odds = [dict(espn_games[0], spread=-3.5, home_odds=-110, away_odds=-110)]
        mock_fetch.return_value = espn_games
        mock_odds.return_value = games_with_odds
        result = generate_events()
        assert result == games_with_odds
