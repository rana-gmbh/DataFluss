from django.db import migrations

NEW = ["daz_staff", "kiga_staff"]


def place_fields(apps, schema_editor):
    """Existing schools get the two marks in their published Personal mask, after "Archiviert".

    The field definitions themselves come from setup_school, which runs after every migrate."""
    MaskVersion = apps.get_model("masks", "MaskVersion")
    for version in MaskVersion.objects.filter(mask__entity="staff", mask__is_default=True, status="published"):
        tree = version.tree or {}
        sections = [section for tab in tree.get("tabs", []) for section in tab.get("sections", [])]
        present = {item.get("field") for section in sections for item in section.get("items", [])}
        missing = [key for key in NEW if key not in present]
        if not missing or not sections:
            continue
        target = next((s for s in sections if any(i.get("field") == "archived" for i in s.get("items", []))), sections[0])
        items = target.setdefault("items", [])
        at = next((n + 1 for n, item in enumerate(items) if item.get("field") == "archived"), len(items))
        items[at:at] = [{"type": "field", "field": key, "w": 3} for key in missing]
        MaskVersion.objects.filter(pk=version.pk).update(tree=tree)


class Migration(migrations.Migration):
    dependencies = [("masks", "0002_names_per_language"), ("people", "0004_section_staff")]

    operations = [migrations.RunPython(place_fields, migrations.RunPython.noop)]
