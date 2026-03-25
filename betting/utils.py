import json
import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytz
import requests
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

WEEKLY_LIMIT = Decimal(settings.WEEKLY_DEPOSIT_LIMIT)
PACIFIC = pytz.timezone('America/Los_Angeles')


def get_week_start():
    """Return start of the current betting week: Monday 12:01 AM Pacific, as UTC datetime."""
    now = datetime.now(PACIFIC)
    days_since_monday = now.weekday()  # Monday == 0
    monday = now - timedelta(days=days_since_monday)
    week_start = monday.replace(hour=0, minute=1, second=0, microsecond=0)
    # Guard: if we are before Monday 12:01 AM, use the previous week's Monday
    if now < week_start:
        week_start -= timedelta(weeks=1)
    return week_start.astimezone(pytz.utc)


def get_weekly_deposited(user):
    """Return total amount deposited by *user* since the current week started."""
    from .models import Deposit
    result = Deposit.objects.filter(
        user=user,
        created_at__gte=get_week_start(),
    ).aggregate(total=Sum('amount'))
    return result['total'] or Decimal('0.00')


def get_weekly_remaining(user):
    """Return how much more the user may deposit this week."""
    remaining = WEEKLY_LIMIT - get_weekly_deposited(user)
    return max(Decimal('0.00'), remaining)


def get_banner_context(user):
    """Return dict with all data needed to render the account banner."""
    from .models import Wager
    pending_wagers = Wager.objects.filter(user=user, status='pending')
    wagered_amount = pending_wagers.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    weekly_deposited = get_weekly_deposited(user)
    return {
        'balance': user.account.balance,
        'wagered_amount': wagered_amount,
        'wager_count': pending_wagers.count(),
        'weekly_deposited': weekly_deposited,
        'weekly_remaining': WEEKLY_LIMIT - weekly_deposited,
        'weekly_limit': WEEKLY_LIMIT,
    }


def settle_wager(wager):
    """Settle a single wager based on the event outcome already stored in the DB."""
    event = wager.event
    if event.home_score is None or event.away_score is None:
        return

    actual_margin = event.home_score - event.away_score
    # spread < 0  →  home favored.  Home "covers" when actual_margin > abs(spread).
    # Equivalently: adjusted = actual_margin + spread > 0  →  home covered.
    adjusted = actual_margin + float(event.spread)

    account = wager.user.account

    if abs(adjusted) < 0.01:  # push (rare with half-point spreads)
        wager.status = 'push'
        wager.payout = Decimal('0.00')
        account.balance += wager.amount
        account.save()
    elif (adjusted > 0 and wager.pick == 'home') or (adjusted < 0 and wager.pick == 'away'):
        odds = event.home_odds if wager.pick == 'home' else event.away_odds
        if odds < 0:
            profit = wager.amount * Decimal(100) / Decimal(abs(odds))
        else:
            profit = wager.amount * Decimal(odds) / Decimal(100)
        profit = profit.quantize(Decimal('0.01'))
        wager.status = 'won'
        wager.payout = profit
        account.balance += wager.amount + profit
        account.save()
    else:
        wager.status = 'lost'
        wager.payout = Decimal('0.00')

    wager.settled_at = timezone.now()
    wager.save()


def fetch_espn_schedule(start_date, end_date):
    """Fetch real NCAA basketball game schedules from ESPN's public API."""
    games = []
    endpoints = [
        ('mens-college-basketball', 'M'),
        ('womens-college-basketball', 'W'),
    ]

    # ESPN only accepts one date at a time — iterate through each day in the range
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)

    for sport_slug, gender in endpoints:
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/basketball"
            f"/{sport_slug}/scoreboard"
        )
        for query_date in dates:
            try:
                resp = requests.get(
                    url,
                    params={'dates': query_date.strftime('%Y%m%d'), 'limit': 100},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            for event in data.get('events', []):
                try:
                    competition = event['competitions'][0]
                    status_name = (
                        competition.get('status', {})
                        .get('type', {})
                        .get('name', '')
                    )
                    if status_name != 'STATUS_SCHEDULED':
                        continue

                    home_team = None
                    away_team = None
                    for comp in competition['competitors']:
                        if comp['homeAway'] == 'home':
                            home_team = comp['team']['displayName']
                        else:
                            away_team = comp['team']['displayName']

                    if not home_team or not away_team:
                        continue
                    if home_team == 'TBD' or away_team == 'TBD':
                        continue

                    games.append({
                        'home_team': home_team,
                        'away_team': away_team,
                        'event_datetime': event['date'],
                        'gender': gender,
                    })
                except (KeyError, IndexError):
                    continue

    return games


def generate_odds_for_games(games):
    """Use Gemini to generate spreads and odds for a list of real game dicts."""
    import google.genai as genai

    if not games:
        return []

    client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    game_list = '\n'.join(
        f"- {g['away_team']} @ {g['home_team']} ({g['gender']})"
        for g in games
    )
    prompt = (
        "For each NCAA basketball game below, provide a realistic point spread and American odds. "
        "Return ONLY a JSON array — no markdown, no explanation. "
        "The array must have exactly the same number of elements as games, in the same order. "
        "Each object must have:\n"
        "- spread: number (negative = home favored, e.g. -4.5)\n"
        "- home_odds: integer (e.g. -110)\n"
        "- away_odds: integer (e.g. -110)\n\n"
        f"Games:\n{game_list}"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        text = response.text.strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            odds_list = json.loads(match.group(0))
        else:
            odds_list = []
    except Exception:
        odds_list = []

    result = []
    for i, game in enumerate(games):
        if i < len(odds_list):
            odds = odds_list[i]
            result.append(dict(
                game,
                spread=odds.get('spread', 0),
                home_odds=int(odds.get('home_odds', -110)),
                away_odds=int(odds.get('away_odds', -110)),
            ))
        else:
            result.append(dict(game, spread=0, home_odds=-110, away_odds=-110))

    return result


def generate_events():
    """Fetch real NCAA games from ESPN and add AI-generated odds."""
    from datetime import date

    today = date.today()
    week_end = today + timedelta(days=6 - today.weekday())
    games = fetch_espn_schedule(today, week_end)
    return generate_odds_for_games(games)


def get_or_generate_events(force_refresh=False):
    """Return this week's SportingEvents, generating via AI if none exist yet."""
    from .models import SportingEvent
    from datetime import date

    week_start = get_week_start().date()
    events = SportingEvent.objects.filter(week_start=week_start)

    if events.exists() and not force_refresh:
        return events

    if force_refresh:
        # Remove events that have no attached wagers so they can be regenerated cleanly
        events.filter(wagers__isnull=True).delete()

    eastern = pytz.timezone('America/New_York')
    games = generate_events()

    for game in games:
        try:
            dt_str = game['event_datetime']
            parsed_dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            if parsed_dt.tzinfo is None:
                eastern = pytz.timezone('America/New_York')
                parsed_dt = eastern.localize(parsed_dt)
            utc_dt = parsed_dt.astimezone(pytz.utc)
            SportingEvent.objects.create(
                home_team=game['home_team'],
                away_team=game['away_team'],
                event_time=utc_dt,
                spread=Decimal(str(game['spread'])),
                home_odds=int(game['home_odds']),
                away_odds=int(game['away_odds']),
                gender=game.get('gender', 'M'),
                week_start=week_start,
            )
        except Exception:
            continue

    return SportingEvent.objects.filter(week_start=week_start)
