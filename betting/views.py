from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Deposit, SportingEvent, Wager
from .utils import (
    WEEKLY_LIMIT,
    get_banner_context,
    get_or_generate_events,
    get_weekly_deposited,
    get_weekly_remaining,
    update_event_results,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('main')

    if request.method == 'POST':
        action = request.POST.get('action')
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        if action == 'register':
            if User.objects.filter(username=username).exists():
                return render(request, 'login.html', {'error': 'Username already taken.'})
            if len(password) < 8:
                return render(request, 'login.html', {'error': 'Password must be at least 8 characters.'})
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            user = User.objects.create_user(
                username=username, password=password,
                first_name=first_name, last_name=last_name,
            )
            login(request, user)
            return redirect('main')

        # Default: login
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('main')
        return render(request, 'login.html', {'error': 'Invalid username or password.'})

    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@login_required
def main_view(request):
    context = get_banner_context(request.user)
    return render(request, 'betting/main.html', context)


# ---------------------------------------------------------------------------
# Banner (returned as OOB swap after state-changing actions)
# ---------------------------------------------------------------------------

@login_required
def banner_view(request):
    context = get_banner_context(request.user)
    return render(request, 'betting/partials/banner.html', context)


# ---------------------------------------------------------------------------
# Deposit
# ---------------------------------------------------------------------------

@login_required
def deposit_menu_view(request):
    context = {
        'weekly_deposited': get_weekly_deposited(request.user),
        'weekly_remaining': get_weekly_remaining(request.user),
        'weekly_limit': WEEKLY_LIMIT,
    }
    return render(request, 'betting/partials/deposit_menu.html', context)


@login_required
@require_POST
def process_deposit_view(request):
    weekly_remaining = get_weekly_remaining(request.user)
    weekly_deposited = get_weekly_deposited(request.user)

    try:
        amount = Decimal(request.POST.get('amount', '0'))
    except InvalidOperation:
        return _deposit_menu_response(
            request, weekly_deposited, weekly_remaining,
            error='Please enter a valid amount.'
        )

    if amount <= 0:
        return _deposit_menu_response(
            request, weekly_deposited, weekly_remaining,
            error='Amount must be greater than zero.'
        )

    if amount > weekly_remaining:
        return _deposit_menu_response(
            request, weekly_deposited, weekly_remaining,
            error=f'Amount exceeds your weekly limit. You may deposit up to ${weekly_remaining:.2f} more this week.'
        )

    Deposit.objects.create(user=request.user, amount=amount)
    account = request.user.account
    account.balance += amount
    account.save()

    weekly_deposited = get_weekly_deposited(request.user)
    weekly_remaining = get_weekly_remaining(request.user)

    return _deposit_menu_response(
        request, weekly_deposited, weekly_remaining,
        success=f'${amount:.2f} deposited successfully!',
        include_banner_oob=True,
    )


def _deposit_menu_response(request, weekly_deposited, weekly_remaining,
                            error=None, success=None, include_banner_oob=False):
    ctx = {
        'weekly_deposited': weekly_deposited,
        'weekly_remaining': weekly_remaining,
        'weekly_limit': WEEKLY_LIMIT,
        'error': error,
        'success': success,
    }
    html = render(request, 'betting/partials/deposit_menu.html', ctx).content.decode()
    if include_banner_oob:
        html += _render_banner_oob(request)
    return HttpResponse(html)


# ---------------------------------------------------------------------------
# Events / Place Wager
# ---------------------------------------------------------------------------

@login_required
def events_menu_view(request):
    force_refresh = request.GET.get('refresh') == '1'
    sport = request.GET.get('sport', '')  # 'M', 'W', or '' (all)
    all_events = get_or_generate_events(force_refresh=force_refresh).filter(status='upcoming')
    events = all_events.filter(gender=sport) if sport else all_events.none()
    user_wagers = {w.event_id: w for w in Wager.objects.filter(user=request.user, event__in=all_events)}
    context = {
        'events': events,
        'user_wagers': user_wagers,
        'balance': request.user.account.balance,
        'selected_sport': sport,
    }
    return render(request, 'betting/partials/events_menu.html', context)


@login_required
@require_POST
def place_wager_view(request):
    event_id = request.POST.get('event_id')
    pick = request.POST.get('pick')
    sport = request.POST.get('sport', '')

    try:
        amount = Decimal(request.POST.get('amount', '0'))
    except InvalidOperation:
        amount = Decimal('0')

    all_events = get_or_generate_events().filter(status='upcoming')
    user_wagers = {w.event_id: w for w in Wager.objects.filter(user=request.user, event__in=all_events)}
    display_events = all_events.filter(gender=sport) if sport else all_events.none()

    def error_response(msg):
        ctx = {
            'events': display_events,
            'user_wagers': user_wagers,
            'balance': request.user.account.balance,
            'wager_error': msg,
            'selected_sport': sport,
        }
        return render(request, 'betting/partials/events_menu.html', ctx)

    if pick not in ('home', 'away'):
        return error_response('Invalid pick selection.')

    if amount <= 0:
        return error_response('Wager amount must be greater than zero.')

    try:
        event = SportingEvent.objects.get(id=event_id, status='upcoming')
    except SportingEvent.DoesNotExist:
        return error_response('Event not found or no longer available for wagering.')

    account = request.user.account
    if amount > account.balance:
        return error_response(f'Insufficient balance. Your current balance is ${account.balance:.2f}.')

    if Wager.objects.filter(user=request.user, event=event).exists():
        return error_response('You already have a wager on this event.')

    wager_spread = event.spread if pick == 'home' else -event.spread
    Wager.objects.create(user=request.user, event=event, amount=amount, pick=pick, wager_spread=wager_spread)
    account.balance -= amount
    account.save()

    # Refresh data for re-render
    all_events = get_or_generate_events().filter(status='upcoming')
    user_wagers = {w.event_id: w for w in Wager.objects.filter(user=request.user, event__in=all_events)}
    display_events = all_events.filter(gender=sport) if sport else all_events.none()
    ctx = {
        'events': display_events,
        'user_wagers': user_wagers,
        'balance': request.user.account.balance,
        'wager_success': f'Wager of ${amount:.2f} placed!',
        'selected_sport': sport,
    }
    html = render(request, 'betting/partials/events_menu.html', ctx).content.decode()
    html += _render_banner_oob(request)
    return HttpResponse(html)


# ---------------------------------------------------------------------------
# Wager History
# ---------------------------------------------------------------------------

@login_required
def history_menu_view(request):
    wager_filter = request.GET.get('filter', 'all')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    qs = Wager.objects.filter(user=request.user).select_related('event')
    if wager_filter == 'completed':
        qs = qs.filter(status__in=('won', 'lost', 'push'))
    elif wager_filter == 'pending':
        qs = qs.filter(status='pending')

    date_error = None
    if start_date or end_date:
        try:
            from datetime import date as date_type
            sd = date_type.fromisoformat(start_date) if start_date else None
            ed = date_type.fromisoformat(end_date) if end_date else None
            if sd and ed and ed < sd:
                date_error = 'End Date must be on or after Start Date.'
            else:
                if sd:
                    qs = qs.filter(event__event_time__date__gte=sd)
                if ed:
                    qs = qs.filter(event__event_time__date__lte=ed)
        except ValueError:
            pass

    wagers = qs.order_by('event__event_time')
    return render(request, 'betting/partials/history_menu.html', {
        'wagers': wagers,
        'wager_filter': wager_filter,
        'start_date': start_date,
        'end_date': end_date,
        'date_error': date_error,
    })


# ---------------------------------------------------------------------------
# Update Results
# ---------------------------------------------------------------------------

@login_required
@require_POST
def update_results_view(request):
    update_event_results()
    wager_filter = request.POST.get('filter', 'all')
    start_date = request.POST.get('start_date', '')
    end_date = request.POST.get('end_date', '')

    qs = Wager.objects.filter(user=request.user).select_related('event')
    if wager_filter == 'completed':
        qs = qs.filter(status__in=('won', 'lost', 'push'))
    elif wager_filter == 'pending':
        qs = qs.filter(status='pending')

    date_error = None
    if start_date or end_date:
        try:
            from datetime import date as date_type
            sd = date_type.fromisoformat(start_date) if start_date else None
            ed = date_type.fromisoformat(end_date) if end_date else None
            if sd and ed and ed < sd:
                date_error = 'End Date must be on or after Start Date.'
            else:
                if sd:
                    qs = qs.filter(event__event_time__date__gte=sd)
                if ed:
                    qs = qs.filter(event__event_time__date__lte=ed)
        except ValueError:
            pass

    wagers = qs.order_by('event__event_time')
    html = render(request, 'betting/partials/history_menu.html', {
        'wagers': wagers,
        'wager_filter': wager_filter,
        'start_date': start_date,
        'end_date': end_date,
        'date_error': date_error,
    }).content.decode()
    html += _render_banner_oob(request)
    return HttpResponse(html)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_banner_oob(request):
    """Render the banner partial tagged for HTMX out-of-band swap."""
    ctx = get_banner_context(request.user)
    ctx['oob'] = True
    return render(request, 'betting/partials/banner.html', ctx).content.decode()
