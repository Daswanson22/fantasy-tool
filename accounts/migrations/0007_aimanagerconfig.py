from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_keptplayer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AIManagerConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_key', models.CharField(max_length=100)),
                ('max_hitter_moves', models.PositiveSmallIntegerField(default=0)),
                ('max_pitcher_moves', models.PositiveSmallIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_manager_configs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'team_key')},
            },
        ),
    ]
