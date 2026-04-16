from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_aimanagerconfig'),
    ]

    operations = [
        migrations.CreateModel(
            name='LeagueSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('league_key', models.CharField(max_length=100, unique=True)),
                ('name', models.CharField(blank=True, default='', max_length=200)),
                ('season', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('num_teams', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('scoring_type', models.CharField(blank=True, default='', max_length=50)),
                ('draft_type', models.CharField(blank=True, default='', max_length=50)),
                ('uses_faab', models.BooleanField(default=False)),
                ('max_weekly_adds', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('trade_end_date', models.DateField(blank=True, null=True)),
                ('current_week', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('start_week', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('end_week', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('fetched_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'league settings',
            },
        ),
    ]
