from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_leaguesettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='aimanagerconfig',
            name='max_total_moves',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='leaguesettings',
            name='max_season_adds',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
