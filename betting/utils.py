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


def _abbr_matches(abbr, team_name):
    """Return True if ESPN abbreviation matches the team name.

    Handles standard prefix matches (PUR→Purdue), containment (CONN→UConn),
    and initials-based abbreviations (ISU→Iowa State, MSU→Michigan State).
    """
    clean = team_name.upper().replace(' ', '').replace('.', '').replace("'", '')
    if clean.startswith(abbr) or abbr in clean:
        return True
    # Initials match: first letter of each word, optionally followed by 'U'
    initials = ''.join(w[0] for w in team_name.upper().split() if w)
    return abbr.startswith(initials)


def scrape_espn_schedule(start_date, end_date):
    """Scrape real NCAA basketball games with betting lines from ESPN schedule pages."""
    from bs4 import BeautifulSoup

    games = []
    endpoints = [
        ('mens-college-basketball', 'M'),
        ('womens-college-basketball', 'W'),
    ]
    eastern = pytz.timezone('America/New_York')

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)

    for sport_slug, gender in endpoints:
        for query_date in dates:
            url = (
                f"https://www.espn.com/{sport_slug}/schedule"
                f"/_/date/{query_date.strftime('%Y%m%d')}"
            )
            try:
                resp = requests.get(
                    url,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                resp.raise_for_status()
                html = resp.text
            except Exception:
                continue

            soup = BeautifulSoup(html, 'html.parser')
            for row in soup.find_all('tr'):
                tds = row.find_all('td')
                if len(tds) < 3:
                    continue
                try:
                    away_links = [
                        a.get_text(strip=True) for a in tds[0].find_all('a')
                        if a.get_text(strip=True)
                    ]
                    home_links = [
                        a.get_text(strip=True) for a in tds[1].find_all('a')
                        if a.get_text(strip=True)
                    ]
                    if not away_links or not home_links:
                        continue
                    away_team = away_links[0]
                    home_team = home_links[0]
                    if away_team == 'TBD' or home_team == 'TBD':
                        continue

                    time_str = tds[2].get_text(strip=True)
                    naive_dt = datetime.strptime(
                        f"{query_date} {time_str}", "%Y-%m-%d %I:%M %p"
                    )
                    utc_dt = eastern.localize(naive_dt).astimezone(pytz.utc)

                    spread = 0.0
                    home_odds = -110
                    away_odds = -110

                    if len(tds) > 6:
                        odds_text = tds[6].get_text(' ', strip=True)
                        m = re.search(
                            r'Line:\s*([A-Z]+)\s+([+-]?\d+\.?\d*)', odds_text
                        )
                        if m:
                            fav_abbr = m.group(1)
                            fav_value = float(m.group(2))
                            if _abbr_matches(fav_abbr, home_team):
                                spread = fav_value
                            elif _abbr_matches(fav_abbr, away_team):
                                spread = -fav_value

                    games.append({
                        'home_team': home_team,
                        'away_team': away_team,
                        'event_datetime': utc_dt.isoformat(),
                        'gender': gender,
                        'spread': spread,
                        'home_odds': home_odds,
                        'away_odds': away_odds,
                    })
                except Exception:
                    continue

    return games



def scrape_espn_scores(dates):
    """Scrape completed game scores from ESPN schedule pages for the given dates.

    Completed game rows have a score summary in tds[2] like 'PUR 79, TEX 77'.
    Abbreviations are matched to home/away teams using _abbr_matches().
    """
    from bs4 import BeautifulSoup

    scores = []
    endpoints = [
        ('mens-college-basketball', 'M'),
        ('womens-college-basketball', 'W'),
    ]

    for sport_slug, gender in endpoints:
        for query_date in dates:
            url = (
                f"https://www.espn.com/{sport_slug}/schedule"
                f"/_/date/{query_date.strftime('%Y%m%d')}"
            )
            try:
                resp = requests.get(
                    url,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                resp.raise_for_status()
                html = resp.text
            except Exception:
                continue

            soup = BeautifulSoup(html, 'html.parser')
            for row in soup.find_all('tr'):
                tds = row.find_all('td')
                if len(tds) < 3:
                    continue
                try:
                    score_text = tds[2].get_text(strip=True)
                    m = re.search(r'([A-Z]+)\s+(\d+),\s*([A-Z]+)\s+(\d+)', score_text)
                    if not m:
                        continue

                    away_links = [
                        a.get_text(strip=True) for a in tds[0].find_all('a')
                        if a.get_text(strip=True)
                    ]
                    home_links = [
                        a.get_text(strip=True) for a in tds[1].find_all('a')
                        if a.get_text(strip=True)
                    ]
                    if not away_links or not home_links:
                        continue
                    away_team = away_links[0]
                    home_team = home_links[0]

                    abbr1, s1 = m.group(1), int(m.group(2))
                    abbr2, s2 = m.group(3), int(m.group(4))

                    if _abbr_matches(abbr1, home_team):
                        home_score, away_score = s1, s2
                    elif _abbr_matches(abbr2, home_team):
                        home_score, away_score = s2, s1
                    elif _abbr_matches(abbr1, away_team):
                        home_score, away_score = s2, s1
                    elif _abbr_matches(abbr2, away_team):
                        home_score, away_score = s1, s2
                    else:
                        continue

                    scores.append({
                        'home_team': home_team,
                        'away_team': away_team,
                        'home_score': home_score,
                        'away_score': away_score,
                    })
                except Exception:
                    continue

    return scores


def update_event_results():
    """Find past upcoming events, scrape scores, update them, and settle wagers."""
    from .models import SportingEvent, Wager

    now = timezone.now()
    past_events = SportingEvent.objects.filter(status='upcoming', event_time__lt=now)
    if not past_events.exists():
        return []

    eastern = pytz.timezone('America/New_York')
    dates = list({e.event_time.astimezone(eastern).date() for e in past_events})
    scores = scrape_espn_scores(dates)

    settled = []
    for score in scores:
        matching = past_events.filter(
            home_team=score['home_team'],
            away_team=score['away_team'],
        )
        for event in matching:
            try:
                pending_wagers = list(Wager.objects.filter(event=event, status='pending'))
                event.home_score = score['home_score']
                event.away_score = score['away_score']
                event.status = 'final'
                event.save()
                for wager in pending_wagers:
                    wager.refresh_from_db()
                    settled.append(wager)
            except Exception:
                continue

    return settled


def generate_events():
    """Scrape real NCAA games with real betting lines from ESPN schedule pages."""
    from datetime import date

    today = date.today()
    week_end = today + timedelta(days=6 - today.weekday())
    return scrape_espn_schedule(today, week_end)


def get_or_generate_events(force_refresh=False):
    """Return this week's SportingEvents, scraping ESPN if none exist or refresh requested."""
    from .models import SportingEvent

    week_start = get_week_start().date()
    events = SportingEvent.objects.filter(week_start=week_start)

    if events.exists() and not force_refresh:
        return events

    games = generate_events()

    for game in games:
        try:
            dt_str = game['event_datetime']
            parsed_dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            if parsed_dt.tzinfo is None:
                parsed_dt = pytz.timezone('America/New_York').localize(parsed_dt)
            utc_dt = parsed_dt.astimezone(pytz.utc)
            SportingEvent.objects.update_or_create(
                home_team=game['home_team'],
                away_team=game['away_team'],
                event_time=utc_dt,
                defaults={
                    'spread': Decimal(str(game['spread'])),
                    'home_odds': int(game['home_odds']),
                    'away_odds': int(game['away_odds']),
                    'gender': game.get('gender', 'M'),
                    'week_start': week_start,
                },
            )
        except Exception:
            continue

    return SportingEvent.objects.filter(week_start=week_start)
