from django.urls import path
from . import views

urlpatterns = [
    path('', views.main_view, name='main'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('banner/', views.banner_view, name='banner'),
    path('deposit/', views.deposit_menu_view, name='deposit_menu'),
    path('deposit/process/', views.process_deposit_view, name='process_deposit'),
    path('events/', views.events_menu_view, name='events_menu'),
    path('events/wager/', views.place_wager_view, name='place_wager'),
    path('history/', views.history_menu_view, name='history_menu'),
]
