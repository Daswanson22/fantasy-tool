from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_aimanagerconfig_execution_state_aitransactionlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='aimanagerconfig',
            name='auto_promote_starters',
            field=models.BooleanField(default=False),
        ),
    ]
