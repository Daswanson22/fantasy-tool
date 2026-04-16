from django.urls import path
from . import views

app_name = 'home'

urlpatterns = [
    path('', views.index, name='index'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('teams/', views.teams, name='teams'),
    path('select-league/', views.select_league, name='select_league'),
    path('waiver-players/', views.waiver_players_api, name='waiver_players_api'),
    path('available-sp/', views.available_sp_api, name='available_sp_api'),
    path('toggle-keeper/', views.toggle_keeper, name='toggle_keeper'),
    path('save-ai-config/', views.save_ai_config, name='save_ai_config'),
]
