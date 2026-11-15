from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_security_fields")]

    operations = [
        migrations.AddField(
            model_name="user", name="language",
            field=models.CharField(blank=True, db_default="", default="", max_length=10, verbose_name="Sprache"),
        ),
        migrations.AddField(
            model_name="user", name="name_scope",
            field=models.CharField(blank=True, db_default="", default="", max_length=5),
        ),
    ]
