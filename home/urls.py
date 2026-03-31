from django.urls import path
from . import views

app_name = 'home'

urlpatterns = [
    path('', views.index, name='index'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('teams/', views.teams, name='teams'),
    path('select-league/', views.select_league, name='select_league'),
    path('debug-roster/', views.debug_roster, name='debug_roster'),
]
