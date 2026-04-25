from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_aimanagerconfig_auto_promote_starters'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PlayerTrendingSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_key', models.CharField(max_length=100)),
                ('player_key', models.CharField(max_length=100)),
                ('player_name', models.CharField(max_length=200)),
                ('date', models.DateField()),
                ('lastweek_pts', models.FloatField(blank=True, null=True)),
                ('lastmonth_pts', models.FloatField(blank=True, null=True)),
                ('trending_delta', models.FloatField(blank=True, null=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='trending_snapshots',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-date', 'player_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='playertrendingsnapshot',
            constraint=models.UniqueConstraint(
                fields=['user', 'team_key', 'player_key', 'date'],
                name='unique_player_trending_per_day',
            ),
        ),
        migrations.AddIndex(
            model_name='playertrendingsnapshot',
            index=models.Index(fields=['user', 'team_key', 'date'], name='pts_user_team_date_idx'),
        ),
        migrations.AddIndex(
            model_name='playertrendingsnapshot',
            index=models.Index(fields=['player_key', 'date'], name='pts_player_date_idx'),
        ),
    ]
