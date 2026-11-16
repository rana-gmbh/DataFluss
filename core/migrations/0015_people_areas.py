"""Roles: the area "Schüler, Personal und Eltern" becomes three areas with the same right."""
from django.db import migrations

SPLIT = ("pupils", "staff", "guardians")
EXTRAS = {"people.archive": ["pupils.archive", "staff.archive"], "people.classes": ["pupils.classes"]}


def split(apps, schema_editor):
    for role in apps.get_model("core", "UserRole").objects.all():
        if "people" not in (role.areas or {}) and not set(role.extras or []) & set(EXTRAS):
            continue
        level = (role.areas or {}).pop("people", "none")
        role.areas = {**role.areas, **{area: level for area in SPLIT}}
        extras = []
        for key in role.extras or []:
            extras.extend(EXTRAS.get(key, [key]))
        role.extras = list(dict.fromkeys(extras))
        role.save(update_fields=["areas", "extras"])


class Migration(migrations.Migration):
    dependencies = [("core", "0014_user_roles")]
    operations = [migrations.RunPython(split, migrations.RunPython.noop)]
