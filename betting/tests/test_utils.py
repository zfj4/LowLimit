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
    scrape_espn_schedule,
    scrape_espn_scores,
    update_event_results,
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
# v0.2.2 — ESPN schedule HTML scraping (real games + real odds, no AI)
# ---------------------------------------------------------------------------

# Minimal HTML fixture matching ESPN's schedule page structure
_MENS_GAME_ROW = '''
<tr>
  <td class="events__col Table__TD"><div class="matchTeams">
    <span class="Table__Team away">
      <a href="/mens-college-basketball/team/_/id/251/texas-longhorns"></a>
      <a href="/mens-college-basketball/team/_/id/251/texas-longhorns">Texas</a>
    </span>
  </div></td>
  <td class="colspan__col Table__TD"><div class="local flex items-center">
    <span class="at">  v  </span>
    <span class="Table__Team">
      <a href="/mens-college-basketball/team/_/id/2509/purdue-boilermakers"></a>
      <a href="/mens-college-basketball/team/_/id/2509/purdue-boilermakers">Purdue</a>
    </span>
  </div></td>
  <td class="Table__TD">7:10 PM</td>
  <td class="Table__TD">CBS</td>
  <td class="Table__TD">Tickets</td>
  <td class="Table__TD">SAP Center</td>
  <td class="Table__TD"><a data-testid="OddsFragmentPointSpread">Line: PUR -7.5</a> O/U: 147.5</td>
</tr>'''

_WOMENS_GAME_ROW = '''
<tr>
  <td class="events__col Table__TD"><div class="matchTeams">
    <span class="Table__Team away">
      <a href="/womens-college-basketball/team/_/id/2579/south-carolina-gamecocks"></a>
      <a href="/womens-college-basketball/team/_/id/2579/south-carolina-gamecocks">South Carolina</a>
    </span>
  </div></td>
  <td class="colspan__col Table__TD"><div class="local flex items-center">
    <span class="at">  @  </span>
    <span class="Table__Team">
      <a href="/womens-college-basketball/team/_/id/99/lsu-tigers"></a>
      <a href="/womens-college-basketball/team/_/id/99/lsu-tigers">LSU</a>
    </span>
  </div></td>
  <td class="Table__TD">8:00 PM</td>
  <td class="Table__TD">ESPN</td>
  <td class="Table__TD">Tickets</td>
  <td class="Table__TD">Smoothie King</td>
  <td class="Table__TD"><a data-testid="OddsFragmentPointSpread">Line: LSU -3.5</a> O/U: 141.0</td>
</tr>'''

_GAME_NO_LINE_ROW = '''
<tr>
  <td class="events__col Table__TD"><div class="matchTeams">
    <span class="Table__Team away">
      <a href="/mens-college-basketball/team/_/id/130/harvard-crimson"></a>
      <a href="/mens-college-basketball/team/_/id/130/harvard-crimson">Harvard</a>
    </span>
  </div></td>
  <td class="colspan__col Table__TD"><div class="local flex items-center">
    <span class="at">  @  </span>
    <span class="Table__Team">
      <a href="/mens-college-basketball/team/_/id/275/wisconsin-badgers"></a>
      <a href="/mens-college-basketball/team/_/id/275/wisconsin-badgers">Wisconsin</a>
    </span>
  </div></td>
  <td class="Table__TD">7:30 PM</td>
  <td class="Table__TD"></td>
  <td class="Table__TD">Tickets</td>
  <td class="Table__TD">Kohl Center</td>
  <td class="Table__TD"></td>
</tr>'''

def _make_html(rows):
    return f'<html><body><table><tbody>{rows}</tbody></table></body></html>'


class TestScrapeEspnSchedule:
    """scrape_espn_schedule() returns real games with real odds from ESPN schedule pages."""

    def _mock_resp(self, html):
        m = MagicMock()
        m.text = html
        m.raise_for_status.return_value = None
        return m

    @patch('requests.get')
    def test_returns_list_of_games(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert isinstance(games, list)
        assert len(games) == 1

    @patch('requests.get')
    def test_game_has_required_fields(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        game = games[0]
        assert 'home_team' in game
        assert 'away_team' in game
        assert 'event_datetime' in game
        assert 'gender' in game
        assert 'spread' in game
        assert 'home_odds' in game
        assert 'away_odds' in game

    @patch('requests.get')
    def test_correct_home_away_teams(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['home_team'] == 'Purdue'
        assert games[0]['away_team'] == 'Texas'

    @patch('requests.get')
    def test_spread_parsed_home_favored(self, mock_get):
        """PUR -7.5 with Purdue at home → spread = -7.5."""
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['spread'] == -7.5

    @patch('requests.get')
    def test_spread_parsed_away_favored(self, mock_get):
        """If the away team is favored, home spread is positive."""
        away_fav_row = _MENS_GAME_ROW.replace('Line: PUR -7.5', 'Line: TEX -3.5')
        mock_get.side_effect = [
            self._mock_resp(_make_html(away_fav_row)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['spread'] == 3.5

    @patch('requests.get')
    def test_default_spread_when_no_line(self, mock_get):
        """Games with no betting line get spread=0 and -110 odds."""
        mock_get.side_effect = [
            self._mock_resp(_make_html(_GAME_NO_LINE_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['spread'] == 0
        assert games[0]['home_odds'] == -110
        assert games[0]['away_odds'] == -110

    @patch('requests.get')
    def test_mens_games_have_gender_M(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['gender'] == 'M'

    @patch('requests.get')
    def test_womens_games_have_gender_W(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html('')),
            self._mock_resp(_make_html(_WOMENS_GAME_ROW)),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['gender'] == 'W'

    @patch('requests.get')
    def test_handles_empty_page(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html('')),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games == []

    @patch('requests.get')
    def test_handles_request_error_gracefully(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.RequestException("Network error")
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games == []

    @patch('requests.get')
    def test_both_genders_combined(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html(_WOMENS_GAME_ROW)),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert len(games) == 2
        genders = {g['gender'] for g in games}
        assert genders == {'M', 'W'}

    @patch('requests.get')
    def test_spread_parsed_via_initials_abbreviation(self, mock_get):
        """ISU matches Iowa State (initials I+S plus U for University)."""
        isu_row = _MENS_GAME_ROW \
            .replace('Line: PUR -7.5', 'Line: ISU -3.5') \
            .replace('>Texas<', '>Tennessee<') \
            .replace('texas-longhorns', 'tennessee-volunteers') \
            .replace('>Purdue<', '>Iowa State<') \
            .replace('purdue-boilermakers', 'iowa-state-cyclones')
        mock_get.side_effect = [
            self._mock_resp(_make_html(isu_row)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        games = scrape_espn_schedule(date(2025, 3, 25), date(2025, 3, 25))
        assert games[0]['home_team'] == 'Iowa State'
        assert games[0]['spread'] == -3.5


class TestGenerateEvents:
    """generate_events() uses ESPN schedule HTML scraping."""

    @patch('betting.utils.scrape_espn_schedule')
    def test_calls_scrape_schedule(self, mock_scrape):
        mock_scrape.return_value = []
        generate_events()
        assert mock_scrape.called

    @patch('betting.utils.scrape_espn_schedule')
    def test_returns_scraped_games(self, mock_scrape):
        games = [{'home_team': 'Purdue', 'away_team': 'Texas',
                  'event_datetime': '2025-03-25T23:10:00+00:00',
                  'gender': 'M', 'spread': -7.5, 'home_odds': -110, 'away_odds': -110}]
        mock_scrape.return_value = games
        result = generate_events()
        assert result == games


@pytest.mark.django_db
class TestGetOrGenerateEvents:
    """get_or_generate_events() saves events without creating duplicates."""

    SAMPLE_GAME = {
        'home_team': 'Purdue', 'away_team': 'Texas',
        'event_datetime': '2025-03-25T23:10:00+00:00',
        'gender': 'M', 'spread': -7.5, 'home_odds': -110, 'away_odds': -110,
    }

    @patch('betting.utils.generate_events')
    def test_repeated_calls_do_not_create_duplicates(self, mock_gen):
        """Calling get_or_generate_events twice must not double the events."""
        from betting.utils import get_or_generate_events
        mock_gen.return_value = [self.SAMPLE_GAME]
        get_or_generate_events()
        get_or_generate_events()
        assert SportingEvent.objects.count() == 1

    @patch('betting.utils.generate_events')
    def test_force_refresh_does_not_create_duplicates(self, mock_gen):
        """force_refresh=True must not duplicate events already in the DB."""
        from betting.utils import get_or_generate_events
        mock_gen.return_value = [self.SAMPLE_GAME]
        get_or_generate_events()
        get_or_generate_events(force_refresh=True)
        assert SportingEvent.objects.count() == 1

    @patch('betting.utils.generate_events')
    def test_force_refresh_adds_newly_available_events(self, mock_gen):
        """force_refresh=True adds new events ESPN has since made available."""
        from betting.utils import get_or_generate_events
        second_game = {
            'home_team': 'Arizona', 'away_team': 'Arkansas',
            'event_datetime': '2025-03-26T01:45:00+00:00',
            'gender': 'M', 'spread': -7.5, 'home_odds': -110, 'away_odds': -110,
        }
        mock_gen.return_value = [self.SAMPLE_GAME]
        get_or_generate_events()
        mock_gen.return_value = [self.SAMPLE_GAME, second_game]
        get_or_generate_events(force_refresh=True)
        assert SportingEvent.objects.count() == 2

    @patch('betting.utils.generate_events')
    def test_force_refresh_does_not_duplicate_wagered_events(self, mock_gen):
        """force_refresh=True must not duplicate an event that already has a wager."""
        from betting.utils import get_or_generate_events
        from django.contrib.auth.models import User
        mock_gen.return_value = [self.SAMPLE_GAME]
        get_or_generate_events()
        event = SportingEvent.objects.first()
        user = User.objects.create_user(username='duptest', password='testpass123')
        Wager.objects.create(user=user, event=event, amount=Decimal('5.00'), pick='home')
        # Now force_refresh — the wagered event cannot be deleted, must not be duplicated
        get_or_generate_events(force_refresh=True)
        assert SportingEvent.objects.count() == 1


# ---------------------------------------------------------------------------
# v0.2.3 — ESPN score scraping and event result updates
# ---------------------------------------------------------------------------

_COMPLETED_GAME_ROW = '''
<tr>
  <td class="events__col Table__TD"><div class="matchTeams">
    <span class="Table__Team away">
      <a href="/mens-college-basketball/team/_/id/251/texas-longhorns"></a>
      <a href="/mens-college-basketball/team/_/id/251/texas-longhorns">Texas</a>
    </span>
  </div></td>
  <td class="colspan__col Table__TD"><div class="local">
    <span class="Table__Team">
      <a href="/mens-college-basketball/team/_/id/2509/purdue-boilermakers"></a>
      <a href="/mens-college-basketball/team/_/id/2509/purdue-boilermakers">Purdue</a>
    </span>
  </div></td>
  <td class="teams__col Table__TD"><a href="/mens-college-basketball/game/_/gameId/123/texas-purdue">PUR 79, TEX 77</a></td>
  <td class="Table__TD"></td>
  <td class="Table__TD"></td>
  <td class="Table__TD"></td>
  <td class="Table__TD"></td>
</tr>'''


class TestScrapeEspnScores:
    """scrape_espn_scores() returns completed game scores from ESPN schedule pages."""

    def _mock_resp(self, html):
        m = MagicMock()
        m.text = html
        m.raise_for_status.return_value = None
        return m

    @patch('requests.get')
    def test_returns_list_of_scores(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_COMPLETED_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        assert isinstance(scores, list)
        assert len(scores) == 1

    @patch('requests.get')
    def test_score_has_required_fields(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_COMPLETED_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        score = scores[0]
        assert 'home_team' in score
        assert 'away_team' in score
        assert 'home_score' in score
        assert 'away_score' in score

    @patch('requests.get')
    def test_correct_team_names(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_COMPLETED_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        assert scores[0]['away_team'] == 'Texas'
        assert scores[0]['home_team'] == 'Purdue'

    @patch('requests.get')
    def test_correct_scores(self, mock_get):
        mock_get.side_effect = [
            self._mock_resp(_make_html(_COMPLETED_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        # PUR 79, TEX 77 → Purdue (home) 79, Texas (away) 77
        assert scores[0]['home_score'] == 79
        assert scores[0]['away_score'] == 77

    @patch('requests.get')
    def test_skips_upcoming_games(self, mock_get):
        """Rows with a time string (not 'Final') are skipped."""
        mock_get.side_effect = [
            self._mock_resp(_make_html(_MENS_GAME_ROW)),
            self._mock_resp(_make_html('')),
        ]
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        assert scores == []

    @patch('requests.get')
    def test_handles_request_error_gracefully(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.RequestException("Network error")
        from datetime import date
        scores = scrape_espn_scores([date(2025, 3, 25)])
        assert scores == []


@pytest.mark.django_db
class TestUpdateEventResults:
    """update_event_results() finds past events, scrapes scores, settles wagers."""

    def setup_method(self):
        self.user = User.objects.create_user(username='updatetest', password='testpass123')
        self.user.account.balance = Decimal('0.00')
        self.user.account.save()

    def _make_past_event(self, home='Home FC', away='Away FC'):
        return SportingEvent.objects.create(
            home_team=home,
            away_team=away,
            event_time=timezone.now() - timezone.timedelta(hours=3),
            spread=Decimal('-5.5'),
            home_odds=-110,
            away_odds=-110,
            gender='M',
            week_start=timezone.now().date(),
            status='upcoming',
        )

    def test_returns_empty_when_no_past_events(self):
        result = update_event_results()
        assert result == []

    def test_skips_future_events(self):
        SportingEvent.objects.create(
            home_team='Future Home', away_team='Future Away',
            event_time=timezone.now() + timezone.timedelta(hours=3),
            spread=Decimal('0.0'), home_odds=-110, away_odds=-110,
            gender='M', week_start=timezone.now().date(), status='upcoming',
        )
        result = update_event_results()
        assert result == []

    def test_skips_already_final_events(self):
        event = self._make_past_event()
        event.status = 'final'
        event.home_score = 80
        event.away_score = 70
        event.save()
        with patch('betting.utils.scrape_espn_scores') as mock_scrape:
            mock_scrape.return_value = [
                {'home_team': event.home_team, 'away_team': event.away_team,
                 'home_score': 80, 'away_score': 70}
            ]
            result = update_event_results()
        assert result == []
        mock_scrape.assert_not_called()

    @patch('betting.utils.scrape_espn_scores')
    def test_updates_event_scores(self, mock_scrape):
        event = self._make_past_event(home='Purdue', away='Texas')
        mock_scrape.return_value = [
            {'home_team': 'Purdue', 'away_team': 'Texas', 'home_score': 68, 'away_score': 72}
        ]
        update_event_results()
        event.refresh_from_db()
        assert event.home_score == 68
        assert event.away_score == 72
        assert event.status == 'final'

    @patch('betting.utils.scrape_espn_scores')
    def test_settles_pending_wagers(self, mock_scrape):
        event = self._make_past_event(home='Purdue', away='Texas')
        wager = Wager.objects.create(
            user=self.user, event=event, amount=Decimal('10.00'), pick='home'
        )
        mock_scrape.return_value = [
            {'home_team': 'Purdue', 'away_team': 'Texas', 'home_score': 80, 'away_score': 70}
        ]
        update_event_results()
        wager.refresh_from_db()
        assert wager.status != 'pending'

    @patch('betting.utils.scrape_espn_scores')
    def test_returns_settled_wagers(self, mock_scrape):
        event = self._make_past_event(home='Purdue', away='Texas')
        wager = Wager.objects.create(
            user=self.user, event=event, amount=Decimal('10.00'), pick='home'
        )
        mock_scrape.return_value = [
            {'home_team': 'Purdue', 'away_team': 'Texas', 'home_score': 80, 'away_score': 70}
        ]
        result = update_event_results()
        assert len(result) == 1
        assert result[0].pk == wager.pk

    @patch('betting.utils.scrape_espn_scores')
    def test_uses_eastern_date_not_utc_for_scraping(self, mock_scrape):
        """Event at 01:45 UTC (21:45 Eastern previous day) must scrape the Eastern date."""
        import pytz
        from datetime import datetime, date
        # 2026-03-28 01:45 UTC = 2026-03-27 21:45 EDT — ESPN page is 20260327
        event_utc = datetime(2026, 3, 28, 1, 45, tzinfo=pytz.utc)
        SportingEvent.objects.create(
            home_team='Iowa State', away_team='Tennessee',
            event_time=event_utc,
            spread=Decimal('-3.5'), home_odds=-110, away_odds=-110,
            gender='M', week_start=date(2026, 3, 23), status='upcoming',
        )
        mock_scrape.return_value = []
        update_event_results()
        called_dates = mock_scrape.call_args[0][0]
        assert date(2026, 3, 27) in called_dates      # Eastern date
        assert date(2026, 3, 28) not in called_dates  # UTC date — wrong

    @patch('betting.utils.scrape_espn_scores')
    def test_settles_wagers_when_duplicate_events_exist(self, mock_scrape):
        """If two upcoming past events share home/away names, both are settled."""
        event1 = self._make_past_event(home='Purdue', away='Texas')
        event2 = self._make_past_event(home='Purdue', away='Texas')
        wager1 = Wager.objects.create(
            user=self.user, event=event1, amount=Decimal('5.00'), pick='home'
        )
        wager2 = Wager.objects.create(
            user=self.user, event=event2, amount=Decimal('5.00'), pick='away'
        )
        mock_scrape.return_value = [
            {'home_team': 'Purdue', 'away_team': 'Texas', 'home_score': 79, 'away_score': 77}
        ]
        update_event_results()
        wager1.refresh_from_db()
        wager2.refresh_from_db()
        assert wager1.status != 'pending'
        assert wager2.status != 'pending'
