from django.db import migrations, models


def ship_names(apps, schema_editor):
    """English and Chinese names for built-ins whose German name is still the shipped one."""
    from masks.builtin_names import fill_options, fill_tree, names_for
    from masks.defaults import COMMON, PUPIL, STAFF

    FieldDefinition = apps.get_model("masks", "FieldDefinition")
    Mask = apps.get_model("masks", "Mask")
    MaskVersion = apps.get_model("masks", "MaskVersion")
    shipped = {key: label for key, label, *_ in COMMON + PUPIL + STAFF}
    for fd in FieldDefinition.objects.filter(is_core=True):
        changed = []
        if not fd.labels and shipped.get(fd.key) == fd.label and names_for(fd.label):
            fd.labels = names_for(fd.label)
            changed.append("labels")
        options = fill_options(fd.options)
        if options != fd.options:
            fd.options = options
            changed.append("options")
        if changed:
            FieldDefinition.objects.filter(pk=fd.pk).update(**{name: getattr(fd, name) for name in changed})
    for mask in Mask.objects.filter(is_default=True):
        if not mask.names and mask.name in ("Schüler", "Personal"):
            Mask.objects.filter(pk=mask.pk).update(names=names_for(mask.name))
    for version in MaskVersion.objects.filter(mask__is_default=True):
        tree = version.tree or {}
        if fill_tree(tree):
            MaskVersion.objects.filter(pk=version.pk).update(tree=tree)


class Migration(migrations.Migration):
    dependencies = [("masks", "0001_initial")]

    operations = [
        migrations.AddField(model_name="fielddefinition", name="labels", field=models.JSONField(blank=True, db_default={}, default=dict)),
        migrations.AddField(model_name="mask", name="names", field=models.JSONField(blank=True, db_default={}, default=dict)),
        migrations.RunPython(ship_names, migrations.RunPython.noop),
    ]
