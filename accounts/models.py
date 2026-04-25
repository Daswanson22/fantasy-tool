import secrets

from django.db import models
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta

User = get_user_model()


class UserProfile(models.Model):
    TIER_FREE  = 'free'
    TIER_PRO   = 'pro'
    TIER_ELITE = 'elite'
    TIER_CHOICES = [
        (TIER_FREE,  'Free'),
        (TIER_PRO,   'Pro'),
        (TIER_ELITE, 'Elite'),
    ]
    LEAGUE_LIMITS = {
        TIER_FREE:  1,
        TIER_PRO:   3,
        TIER_ELITE: None,  # unlimited
    }

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='profile'
    )
    tier = models.CharField(
        max_length=10, choices=TIER_CHOICES, default=TIER_FREE
    )
    email_notifications = models.BooleanField(default=True)
    stripe_customer_id    = models.CharField(max_length=64, blank=True, default='')
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default='')

    # Features available on paid tiers only
    PAID_TIERS = {TIER_PRO, TIER_ELITE}

    def get_league_limit(self):
        """Return max leagues allowed, or None for unlimited."""
        return self.LEAGUE_LIMITS.get(self.tier, 1)

    @property
    def can_access_available_sp(self):
        return self.tier in self.PAID_TIERS

    @property
    def can_access_matchups(self):
        return self.tier in self.PAID_TIERS

    @property
    def can_access_ai_gm(self):
        return self.tier in self.PAID_TIERS

    @property
    def can_access_league_analytics(self):
        return self.tier == self.TIER_ELITE

    def __str__(self):
        return f'{self.user.username} ({self.tier})'


class SelectedLeague(models.Model):
    """
    Permanently stores the leagues a user has chosen to track.
    Once the tier limit is reached, no more leagues can be added
    and existing selections cannot be removed.
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='selected_leagues'
    )
    team_key = models.CharField(max_length=100)
    team_name = models.CharField(max_length=200)
    league_key = models.CharField(max_length=100)
    selected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'team_key')]
        ordering = ['selected_at']

    def __str__(self):
        return f'{self.user.username} → {self.team_name}'


class PendingEmailChange(models.Model):
    TOKEN_TTL_HOURS = 24

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='pending_email_change'
    )
    new_email = models.EmailField()
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(hours=self.TOKEN_TTL_HOURS)

    @classmethod
    def create_for_user(cls, user, new_email):
        token = secrets.token_urlsafe(32)
        obj, _ = cls.objects.update_or_create(
            user=user,
            defaults={'new_email': new_email, 'token': token, 'created_at': timezone.now()},
        )
        return obj

    def __str__(self):
        return f'{self.user.username} → {self.new_email}'


class LeagueSettings(models.Model):
    """
    Cached static settings for a Yahoo Fantasy league.
    Keyed by league_key — not per-user, since settings are shared across all members.
    Refreshed automatically when stale (> 24 hours).
    """
    league_key    = models.CharField(max_length=100, unique=True)
    name          = models.CharField(max_length=200, blank=True, default='')
    season        = models.PositiveSmallIntegerField(null=True, blank=True)
    num_teams     = models.PositiveSmallIntegerField(null=True, blank=True)
    scoring_type  = models.CharField(max_length=50, blank=True, default='')
    draft_type    = models.CharField(max_length=50, blank=True, default='')
    uses_faab     = models.BooleanField(default=False)
    # Max roster add/drop moves allowed per week (H2H) or per season (Roto)
    max_weekly_adds  = models.PositiveSmallIntegerField(null=True, blank=True)
    max_season_adds  = models.PositiveSmallIntegerField(null=True, blank=True)
    trade_end_date  = models.DateField(null=True, blank=True)
    current_week    = models.PositiveSmallIntegerField(null=True, blank=True)
    start_week      = models.PositiveSmallIntegerField(null=True, blank=True)
    end_week        = models.PositiveSmallIntegerField(null=True, blank=True)
    fetched_at      = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'league settings'

    def __str__(self):
        return f'{self.name} ({self.league_key})'

    SCORING_TYPE_LABELS = {
        'head':      'Head to Head (Categories)',
        'headone':   'Head to Head (One Win)',
        'point':     'Head to Head (Points)',
        'headpoint': 'H2H Points',
        'roto':      'Rotisserie',
        'rotoone':   'Rotisserie (One Win)',
    }
    H2H_TYPES = {'head', 'headone', 'point', 'headpoint'}

    @property
    def scoring_type_display(self):
        return self.SCORING_TYPE_LABELS.get(self.scoring_type, self.scoring_type)

    @property
    def is_h2h(self):
        return self.scoring_type in self.H2H_TYPES

    def is_stale(self, hours=24):
        from django.utils import timezone
        return (timezone.now() - self.fetched_at).total_seconds() > hours * 3600


class AIManagerConfig(models.Model):
    """Per-team AI Manager configuration."""
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='ai_manager_configs'
    )
    team_key = models.CharField(max_length=100)

    # --- User-facing toggle ---
    is_enabled = models.BooleanField(default=False)

    # --- Move budgets (set by user in Configure modal) ---
    # Roto leagues: per-position move budgets
    max_hitter_moves  = models.PositiveSmallIntegerField(default=0)
    max_pitcher_moves = models.PositiveSmallIntegerField(default=0)
    # H2H leagues: single combined weekly move budget
    max_total_moves   = models.PositiveSmallIntegerField(default=0)

    # --- Roster optimizer options ---
    # When True, the engine automatically promotes bench pitchers who are
    # confirmed starters today (and demotes non-starters to make room).
    auto_promote_starters = models.BooleanField(default=False)

    # --- Execution state (managed by AI Manager engine) ---
    # Tracks adds made this week so the engine can enforce max_*_moves budgets.
    # Reset automatically when last_known_week != league_settings.current_week.
    adds_used_this_week = models.PositiveSmallIntegerField(default=0)
    last_known_week     = models.PositiveSmallIntegerField(null=True, blank=True)
    # Idempotency guard: the engine skips a team if it already ran today.
    last_ai_run_date    = models.DateField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'team_key')]

    def __str__(self):
        return f'{self.user.username} AI config for {self.team_key}'


class AITransactionLog(models.Model):
    """Audit trail for every add/drop the AI Manager executes (or would execute in dry-run)."""
    ACTION_ADD  = 'add'
    ACTION_DROP = 'drop'
    ACTION_CHOICES = [
        (ACTION_ADD,  'Add'),
        (ACTION_DROP, 'Drop'),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_transactions')
    team_key   = models.CharField(max_length=100)
    action     = models.CharField(max_length=10, choices=ACTION_CHOICES)
    player_key = models.CharField(max_length=100)
    player_name = models.CharField(max_length=200)
    reason     = models.TextField(blank=True, default='')
    dry_run    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'team_key', 'created_at']),
        ]

    def __str__(self):
        prefix = '[DRY] ' if self.dry_run else ''
        return f'{prefix}{self.action.upper()} {self.player_name} ({self.team_key})'


class KeptPlayer(models.Model):
    """Tracks players the user has marked as keepers on a given team."""
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='kept_players'
    )
    team_key = models.CharField(max_length=100)
    player_key = models.CharField(max_length=100)
    kept_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'team_key', 'player_key')]

    def __str__(self):
        return f'{self.user.username} keeps {self.player_key} on {self.team_key}'


class PlayerTrendingSnapshot(models.Model):
    """
    Daily snapshot of a rostered player's trending value.
    One row per (user, team_key, player_key, date) — idempotent on re-runs.

    trending_delta: (lastweek_pts / 6) - (lastmonth_pts / 24)
        — fantasy points per game, last week minus last month average.
        Positive = player is outperforming their monthly baseline (hot).
        Negative = player is underperforming (cold).
    lastweek_pts / lastmonth_pts stored raw so the formula can be re-derived.
    """
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trending_snapshots')
    team_key    = models.CharField(max_length=100)
    player_key  = models.CharField(max_length=100)
    player_name = models.CharField(max_length=200)
    date        = models.DateField()

    lastweek_pts  = models.FloatField(null=True, blank=True)
    lastmonth_pts = models.FloatField(null=True, blank=True)
    trending_delta = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = [('user', 'team_key', 'player_key', 'date')]
        ordering = ['-date', 'player_name']
        indexes = [
            models.Index(fields=['user', 'team_key', 'date']),
            models.Index(fields=['player_key', 'date']),
        ]

    def __str__(self):
        delta_str = f'{self.trending_delta:+.1f}' if self.trending_delta is not None else 'N/A'
        return f'{self.player_name} | {self.team_key} | {self.date} | Δ{delta_str}'


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)
