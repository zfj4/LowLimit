from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Account(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f"{self.user.username} — ${self.balance}"


class Deposit(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='deposits')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} deposited ${self.amount} at {self.created_at}"


class SportingEvent(models.Model):
    GENDER_CHOICES = [('M', "Men's"), ('W', "Women's")]
    STATUS_CHOICES = [('upcoming', 'Upcoming'), ('final', 'Final')]

    home_team = models.CharField(max_length=100)
    away_team = models.CharField(max_length=100)
    event_time = models.DateTimeField()
    # Negative spread = home favored (e.g. -4.5 means home gives 4.5 points)
    spread = models.DecimalField(max_digits=5, decimal_places=1)
    home_odds = models.IntegerField(default=-110)
    away_odds = models.IntegerField(default=-110)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='M')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='upcoming')
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    week_start = models.DateField()
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['event_time']

    def __str__(self):
        return f"{self.away_team} @ {self.home_team} ({self.event_time.date()})"

    def spread_display(self):
        """Return (home_spread_str, away_spread_str) for display."""
        s = float(self.spread)
        if s < 0:
            return str(s), f"+{abs(s)}"
        elif s > 0:
            return f"+{s}", str(-s)
        else:
            return 'PK', 'PK'


class Wager(models.Model):
    PICK_CHOICES = [('home', 'Home'), ('away', 'Away')]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('won', 'Won'),
        ('lost', 'Lost'),
        ('push', 'Push'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wagers')
    event = models.ForeignKey(SportingEvent, on_delete=models.CASCADE, related_name='wagers')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    pick = models.CharField(max_length=4, choices=PICK_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    payout = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wager_spread = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} — ${self.amount} on {self.pick} ({self.event})"

    def picked_team(self):
        return self.event.home_team if self.pick == 'home' else self.event.away_team

    def wager_spread_display(self):
        """Return the stored spread formatted for display (e.g. '+3.5', '-2.5', 'PK')."""
        if self.wager_spread is None:
            return ''
        s = float(self.wager_spread)
        if s > 0:
            return f'+{s:g}'
        elif s < 0:
            return f'{s:g}'
        return 'PK'

    def net_change(self):
        """Net change to balance from this wager (profit on win, -amount on loss, 0 on push)."""
        if self.status == 'won':
            return self.payout
        elif self.status == 'lost':
            return -self.amount
        elif self.status == 'push':
            return Decimal('0.00')
        return None  # pending
