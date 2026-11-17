from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tables', '0003_table_section'),
    ]

    operations = [
        migrations.AddField(
            model_name='table',
            name='look',
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
    ]
