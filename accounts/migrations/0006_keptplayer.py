from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_userprofile_stripe_customer_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='KeptPlayer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_key', models.CharField(max_length=100)),
                ('player_key', models.CharField(max_length=100)),
                ('kept_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kept_players', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'team_key', 'player_key')},
            },
        ),
    ]
