from django.db import migrations


def purge(apps, schema_editor):
    """Whole import files, possibly with passwords, were written to the change log until now."""
    apps.get_model("core", "ChangeLog").objects.filter(
        entity="transfer.importsession", field__in=["headers", "rows", "report", "undo"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_alter_user_role"),
        ("transfer", "0001_initial"),
    ]

    operations = [migrations.RunPython(purge, migrations.RunPython.noop)]
