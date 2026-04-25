from django.contrib import admin
from .emails import send_test_email, send_ai_transaction_email
from .models import (
    UserProfile, SelectedLeague, AITransactionLog,
    KeptPlayer, AIManagerConfig, LeagueSettings, PlayerTrendingSnapshot,
)


# ── Shared sample data for AI transaction test emails ─────────────────────────

_SAMPLE_RESULT = {
    'team_key': '423.l.00000.t.1',
    'decision': 'executed',
    'dry_run':  False,
    'reason': (
        'Drop Sample Player (drop_score=1.23); '
        'Add Sample Starter (add_score=6.00, start in 0 day(s))'
    ),
    'drop_player': {
        'name': 'Sample Player',
        'eligible_positions': 'SP, RP',
        'drop_score': 1.23,
    },
    'add_player': {
        'name': 'Sample Starter',
        'eligible_positions': 'SP',
        'days_to_start': 0,
        'add_score': 6.00,
    },
    'roster_moves': [
        {'player_name': 'Sample Starter', 'from_position': 'BN', 'to_position': 'SP'},
    ],
}


# ── Actions ───────────────────────────────────────────────────────────────────

@admin.action(description='Send test email to selected users')
def action_send_test_email(modeladmin, request, queryset):
    sent, errors = 0, []
    for profile in queryset.select_related('user'):
        try:
            send_test_email(profile.user.email, profile.user.username)
            sent += 1
        except Exception as exc:
            errors.append(f'{profile.user.email}: {exc}')
    if sent:
        modeladmin.message_user(request, f'Test email sent to {sent} user(s).')
    for err in errors:
        modeladmin.message_user(request, err, level='error')


@admin.action(description='Send sample AI transaction email to selected users')
def action_send_sample_ai_email(modeladmin, request, queryset):
    sent, errors = 0, []
    for profile in queryset.select_related('user'):
        try:
            send_ai_transaction_email(profile.user, _SAMPLE_RESULT)
            sent += 1
        except Exception as exc:
            errors.append(f'{profile.user.email}: {exc}')
    if sent:
        modeladmin.message_user(request, f'Sample AI transaction email sent to {sent} user(s).')
    for err in errors:
        modeladmin.message_user(request, err, level='error')


@admin.action(description='Resend transaction notification for selected log entries')
def action_resend_ai_notification(modeladmin, request, queryset):
    """
    Finds the paired add+drop entries for each selected log, reconstructs the
    result dict, and re-sends the notification email to the log's owner.
    """
    from datetime import timedelta

    sent, skipped, errors = 0, 0, []

    # Deduplicate by (user_id, team_key, minute-bucket) so we send one email per transaction
    seen = set()
    for log in queryset.select_related('user').order_by('-created_at'):
        bucket_key = (log.user_id, log.team_key, log.created_at.replace(second=0, microsecond=0))
        if bucket_key in seen:
            continue
        seen.add(bucket_key)

        # Find the paired entries within a 2-minute window
        window_start = log.created_at - timedelta(minutes=2)
        window_end   = log.created_at + timedelta(minutes=2)
        pair = AITransactionLog.objects.filter(
            user=log.user,
            team_key=log.team_key,
            dry_run=False,
            created_at__range=(window_start, window_end),
        ).exclude(pk=log.pk)

        add_log  = next((e for e in [log, *pair] if e.action == AITransactionLog.ACTION_ADD),  None)
        drop_log = next((e for e in [log, *pair] if e.action == AITransactionLog.ACTION_DROP), None)

        if not add_log or not drop_log:
            skipped += 1
            continue

        result = {
            'team_key':     log.team_key,
            'decision':     'executed',
            'dry_run':      False,
            'reason':       add_log.reason,
            'add_player':   {'name': add_log.player_name,  'eligible_positions': '', 'days_to_start': None},
            'drop_player':  {'name': drop_log.player_name, 'eligible_positions': ''},
            'roster_moves': [],
        }
        try:
            send_ai_transaction_email(log.user, result)
            sent += 1
        except Exception as exc:
            errors.append(f'{log.user.email}: {exc}')

    if sent:
        modeladmin.message_user(request, f'Notification re-sent for {sent} transaction(s).')
    if skipped:
        modeladmin.message_user(request, f'{skipped} entr(ies) skipped — could not find a paired add+drop.', level='warning')
    for err in errors:
        modeladmin.message_user(request, err, level='error')


# ── ModelAdmin registrations ──────────────────────────────────────────────────

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ('user', 'tier', 'email_notifications', 'get_league_limit')
    list_filter   = ('tier', 'email_notifications')
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user',)
    actions       = [action_send_test_email, action_send_sample_ai_email]


@admin.register(SelectedLeague)
class SelectedLeagueAdmin(admin.ModelAdmin):
    list_display  = ('user', 'team_name', 'team_key', 'league_key', 'selected_at')
    list_filter   = ('selected_at',)
    search_fields = ('user__username', 'team_name', 'team_key', 'league_key')
    raw_id_fields = ('user',)
    readonly_fields = ('selected_at',)
    ordering      = ('-selected_at',)


@admin.register(AITransactionLog)
class AITransactionLogAdmin(admin.ModelAdmin):
    list_display  = ('created_at', 'user', 'team_key', 'action', 'player_name', 'dry_run')
    list_filter   = ('action', 'dry_run', 'created_at')
    search_fields = ('user__username', 'user__email', 'player_name', 'team_key')
    readonly_fields = ('user', 'team_key', 'action', 'player_key', 'player_name', 'reason', 'dry_run', 'created_at')
    ordering      = ('-created_at',)
    date_hierarchy = 'created_at'
    actions       = [action_resend_ai_notification]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(KeptPlayer)
class KeptPlayerAdmin(admin.ModelAdmin):
    list_display  = ('user', 'team_key', 'player_key', 'kept_at')
    list_filter   = ('kept_at',)
    search_fields = ('user__username', 'team_key', 'player_key')
    raw_id_fields = ('user',)
    readonly_fields = ('kept_at',)
    ordering      = ('-kept_at',)


@admin.register(AIManagerConfig)
class AIManagerConfigAdmin(admin.ModelAdmin):
    list_display  = ('user', 'team_key', 'is_enabled', 'auto_promote_starters',
                     'adds_used_this_week', 'last_ai_run_date', 'updated_at')
    list_filter   = ('is_enabled', 'auto_promote_starters')
    search_fields = ('user__username', 'team_key')
    raw_id_fields = ('user',)
    readonly_fields = ('updated_at',)
    ordering      = ('user__username', 'team_key')


@admin.register(LeagueSettings)
class LeagueSettingsAdmin(admin.ModelAdmin):
    list_display  = ('name', 'league_key', 'season', 'scoring_type', 'num_teams',
                     'current_week', 'end_week', 'uses_faab', 'fetched_at')
    list_filter   = ('scoring_type', 'uses_faab', 'season')
    search_fields = ('name', 'league_key')
    readonly_fields = ('fetched_at',)
    ordering      = ('name',)


@admin.register(PlayerTrendingSnapshot)
class PlayerTrendingSnapshotAdmin(admin.ModelAdmin):
    list_display  = ('date', 'player_name', 'user', 'team_key',
                     'lastweek_pts', 'lastmonth_pts', 'trending_delta')
    list_filter   = ('date',)
    search_fields = ('player_name', 'player_key', 'user__username', 'team_key')
    raw_id_fields = ('user',)
    readonly_fields = ('date', 'user', 'team_key', 'player_key', 'player_name',
                       'lastweek_pts', 'lastmonth_pts', 'trending_delta')
    ordering      = ('-date', 'player_name')
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
