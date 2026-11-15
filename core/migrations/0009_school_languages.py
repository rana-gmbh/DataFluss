from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0008_user_language")]

    operations = [
        migrations.AddField(
            model_name="schoolsettings", name="languages",
            field=models.JSONField(blank=True, db_default=[], default=list, verbose_name="Sprachen"),
        ),
    ]
