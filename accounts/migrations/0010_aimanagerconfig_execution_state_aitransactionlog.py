from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_aimanagerconfig_max_total_moves_leaguesettings_max_season_adds'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- New fields on AIManagerConfig ---
        migrations.AddField(
            model_name='aimanagerconfig',
            name='is_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='aimanagerconfig',
            name='adds_used_this_week',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='aimanagerconfig',
            name='last_known_week',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='aimanagerconfig',
            name='last_ai_run_date',
            field=models.DateField(blank=True, null=True),
        ),

        # --- New AITransactionLog model ---
        migrations.CreateModel(
            name='AITransactionLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_key', models.CharField(max_length=100)),
                ('action', models.CharField(
                    choices=[('add', 'Add'), ('drop', 'Drop')],
                    max_length=10,
                )),
                ('player_key', models.CharField(max_length=100)),
                ('player_name', models.CharField(max_length=200)),
                ('reason', models.TextField(blank=True, default='')),
                ('dry_run', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ai_transactions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='aitransactionlog',
            index=models.Index(
                fields=['user', 'team_key', 'created_at'],
                name='accounts_ai_user_id_team_created_idx',
            ),
        ),
    ]
