from django.contrib import admin
from .models import Account, Deposit, SportingEvent, Wager


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['user', 'balance']
    search_fields = ['user__username']


@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username']


@admin.register(SportingEvent)
class SportingEventAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'gender', 'event_time', 'spread', 'status', 'home_score', 'away_score', 'week_start']
    list_editable = ['status', 'home_score', 'away_score']
    list_filter = ['status', 'gender', 'week_start']
    search_fields = ['home_team', 'away_team']

    fieldsets = (
        ('Game Info', {'fields': ('home_team', 'away_team', 'event_time', 'gender', 'week_start')}),
        ('Odds', {'fields': ('spread', 'home_odds', 'away_odds')}),
        ('Result', {'fields': ('status', 'home_score', 'away_score')}),
    )


@admin.register(Wager)
class WagerAdmin(admin.ModelAdmin):
    list_display = ['user', 'event', 'amount', 'pick', 'status', 'payout', 'created_at']
    list_filter = ['status', 'pick']
    search_fields = ['user__username']
    readonly_fields = ['created_at', 'settled_at']
