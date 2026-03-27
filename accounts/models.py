from django.db import models
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

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

    def get_league_limit(self):
        """Return max leagues allowed, or None for unlimited."""
        return self.LEAGUE_LIMITS.get(self.tier, 1)

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


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)
