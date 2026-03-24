import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytz
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


def generate_events():
    """Call Claude to produce a list of NCAA D1 basketball game dicts for the current week."""
    import anthropic
    from datetime import date

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    today = date.today()
    week_end = today + timedelta(days=6 - today.weekday())

    prompt = (
        f"Generate a realistic list of 12 upcoming NCAA Division I basketball games for the week of "
        f"{today.strftime('%B %d, %Y')} through {week_end.strftime('%B %d, %Y')}. "
        "Include a mix of men's and women's games from various conferences "
        "(ACC, Big Ten, SEC, Big 12, Pac-12, Big East, etc.).\n\n"
        "Return ONLY a JSON array — no markdown fences, no explanation. Each object must have:\n"
        '- home_team: string (e.g. "Duke Blue Devils")\n'
        "- away_team: string\n"
        "- event_datetime: string (ISO 8601, Eastern Time, e.g. \"2024-03-18T19:00:00\")\n"
        "- spread: number (negative = home favored, e.g. -4.5 means home gives 4.5 points)\n"
        "- home_odds: integer (American odds, typically -110)\n"
        "- away_odds: integer (American odds, typically -110)\n"
        '- gender: string ("M" for men\'s, "W" for women\'s)\n\n'
        "Use realistic team names, spreads between -15.5 and +15.5, game times 11:00–22:00 ET, "
        "spread across the week."
    )

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    # Strip accidental markdown code fences
    if text.startswith('```'):
        parts = text.split('```')
        text = parts[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text.strip())


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
            naive_dt = datetime.fromisoformat(dt_str)
            aware_dt = eastern.localize(naive_dt)
            utc_dt = aware_dt.astimezone(pytz.utc)
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
