from django.contrib import admin
from .models import UserProfile, SelectedLeague


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'tier', 'get_league_limit')
    list_filter = ('tier',)
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user',)


@admin.register(SelectedLeague)
class SelectedLeagueAdmin(admin.ModelAdmin):
    list_display = ('user', 'team_name', 'team_key', 'league_key', 'selected_at')
    list_filter = ('selected_at',)
    search_fields = ('user__username', 'team_name', 'team_key', 'league_key')
    raw_id_fields = ('user',)
    readonly_fields = ('selected_at',)
    ordering = ('-selected_at',)
