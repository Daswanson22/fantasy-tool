import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SelectedLeague',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_key', models.CharField(max_length=100)),
                ('team_name', models.CharField(max_length=200)),
                ('league_key', models.CharField(max_length=100)),
                ('selected_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='selected_leagues',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['selected_at'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='selectedleague',
            unique_together={('user', 'team_key')},
        ),
    ]
