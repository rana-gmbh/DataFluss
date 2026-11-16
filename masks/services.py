from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from people.models import ClassGroup, Person, SchoolYear
from sync import journal
from vault import crypto

from core import fieldtypes, modules

from . import i18n
from .models import FieldDefinition, Mask, MaskVersion
from .registry import BLOCKS

MASKED = fieldtypes.MASKED
PERSON_COLUMNS = {f.name for f in Person._meta.concrete_fields}


def _unclaimed_section_flags():
    """Person flags of module sections (DaZ-Fachkraft, say) whose module is not loaded: their fields stay hidden."""
    from core import modules

    return set(Person.SECTION_FLAGS) - set(modules.merged("staff_sections"))


def fields_for(entity, include_archived=False):
    qs = FieldDefinition.objects.filter(entity=entity)
    if hidden := _unclaimed_section_flags():
        qs = qs.exclude(key__in=hidden)
    if not include_archived:
        qs = qs.filter(archived=False)
    return list(qs)


def field_map(entity, include_archived=False):
    return {f.key: f for f in fields_for(entity, include_archived)}


parse_date = fieldtypes.parse_date  # devices, people and the importer read dates through here


def coerce(fd, raw):
    """Turn form input into the stored value, or raise ValidationError in German."""
    return fieldtypes.clean(fd, raw)


def get_value(person, fd):
    if fd.key == "klasse":
        return person.current_class
    if fd.is_core and fd.key in PERSON_COLUMNS:
        return getattr(person, fd.key)
    return person.custom.get(fd.key)


def form_value(person, fd):
    value = get_value(person, fd)
    if fd.type == "class":
        return str(value.pk) if value else ""
    return fieldtypes.form(fd, value)


def display_value(person, fd, localized=False):
    """The value as text. Rules and mask conditions compare the stored, language-free text;
    screens pass localized=True for Ja/Nein and choices in the language on screen."""
    value = get_value(person, fd)
    if fd.type == "class":
        return value.name if value else ""
    return fieldtypes.display(fd, value, localized)


def secret_context(person, key):
    return f"person:{person.pk}:{key}"


def reveal_custom(person, fd):
    return crypto.decrypt(person.custom.get(fd.key, ""), secret_context(person, fd.key))


def set_value(person, fd, raw):
    """Save one field and return (saved value as text, previous value as text)."""
    previous = form_value(person, fd)
    if fd.read_only:
        raise ValidationError(_("%(field)s kann nicht geändert werden.") % {"field": fd.display_label})
    if fd.type == "class":
        group = ClassGroup.objects.filter(pk=raw).first() if raw else None
        if raw and group is None:
            raise ValidationError(_("Diese Klasse gibt es nicht."))
        person.move_to_class(group)
        return raw or "", previous
    value = coerce(fd, raw)
    if fd.is_core and fd.key in PERSON_COLUMNS:
        setattr(person, fd.key, value)
    elif fd.type == "secret":
        if value:
            person.custom[fd.key] = crypto.encrypt(value, secret_context(person, fd.key))
        else:
            person.custom.pop(fd.key, None)
        previous = ""
    else:
        if isinstance(value, date):
            value = value.isoformat()
        person.custom[fd.key] = value
    if fd.key == "archived" and value and person.exit_date is None:
        person.exit_date = timezone.localdate()
    person.save()
    return form_value(person, fd), previous


def condition_met(item, person, fields):
    rule = item.get("show_if")
    if not rule or not rule.get("field"):
        return True
    fd = fields.get(rule["field"])
    if fd is None:
        return True
    current = str(display_value(person, fd))
    target = str(rule.get("value", ""))
    return current != target if rule.get("op") == "ne" else current == target


def condition_fields(tree):
    keys = set()
    for tab in tree.get("tabs", []):
        for section in tab.get("sections", []):
            for item in section.get("items", []):
                rule = item.get("show_if")
                if rule and rule.get("field"):
                    keys.add(rule["field"])
    return keys


def resolve_mask(person, user=None):
    """Most specific published mask for this record and user."""
    masks = list(Mask.objects.filter(entity=person.kind))
    fields = field_map(person.kind, include_archived=True)

    def applies(mask):
        if mask.roles and user is not None and user.role not in mask.roles:
            return False
        if mask.match_field:
            fd = fields.get(mask.match_field)
            return fd is not None and str(display_value(person, fd)) == mask.match_value
        return True

    ranked = sorted(masks, key=lambda m: (not bool(m.match_field), not bool(m.roles), not m.is_default))
    for mask in ranked:
        if applies(mask) and mask.published:
            return mask, mask.published
    return None, None


def class_choices():
    year = SchoolYear.current()
    qs = ClassGroup.objects.all()
    if year:
        from django.db.models import Q

        qs = qs.filter(Q(school_year=year) | Q(school_year__isnull=True))
    return list(qs)


def build_view(tree, person, user, readonly=False):
    """Resolve a mask tree into what the template renders."""
    fields = field_map(person.kind)
    can_edit = user.can("person.edit") and not readonly
    classes = None
    tabs = []
    for tab in tree.get("tabs", []):
        sections = []
        for section in tab.get("sections", []):
            items = []
            for item in section.get("items", []):
                if not condition_met(item, person, fields):
                    continue
                entry = {"w": item.get("w", 12), "type": item.get("type")}
                if item.get("type") == "field":
                    fd = fields.get(item.get("field"))
                    if fd is None:
                        continue
                    if fd.type == "secret" and not user.can("vault.view"):
                        continue
                    if fd.type == "class" and classes is None:
                        classes = class_choices()
                    entry.update(
                        fd=fd, value=form_value(person, fd), display=display_value(person, fd, localized=True),
                        editable=can_edit and not fd.read_only,
                        label=i18n.pick(item.get("label", ""), item.get("labels")) or fd.display_label, help=item.get("help") or fd.help_text,
                        choices=fd.option_choices if fd.type in ("choice", "multichoice") else None,
                        classes=classes if fd.type == "class" else None,
                    )
                elif item.get("type") == "block":
                    name = item.get("block")
                    if name not in BLOCKS or not modules.block_active(name):
                        continue
                    if name == "logins" and not user.can("vault.view"):
                        continue
                    entry.update(block=name, title=BLOCKS[name][0])
                elif item.get("type") == "note":
                    entry.update(text=item.get("text", ""))
                else:
                    continue
                items.append(entry)
            title = i18n.pick(section.get("title", ""), section.get("titles"))
            if items or title:
                sections.append({"title": title, "items": items})
        if sections:
            tabs.append({"id": tab.get("id"), "title": i18n.pick(tab.get("title", ""), tab.get("titles")), "sections": sections})
    return tabs


def publish_tree(mask, tree, user):
    journal.delete(mask.versions.filter(status=MaskVersion.DRAFT))
    latest = mask.versions.order_by("-number").first()
    number = (latest.number + 1) if latest else 1
    journal.update(mask.versions.filter(status=MaskVersion.PUBLISHED), status=MaskVersion.RETIRED)
    return MaskVersion.objects.create(mask=mask, number=number, status=MaskVersion.PUBLISHED, tree=tree, author=user)


def usage_of(entity, key):
    """Where a field is in use, shown before it can be archived."""
    from core.models import SavedView
    from prints.models import PrintTemplate

    found = []
    for version in MaskVersion.objects.filter(mask__entity=entity, status=MaskVersion.PUBLISHED).select_related("mask"):
        if f'"{key}"' in str(version.tree).replace("'", '"'):
            found.append((_("Maske"), version.mask.display_name))
    for template in PrintTemplate.objects.filter(source__in=[entity, "class"], archived=False):
        if key in str(template.tree) or key in template.code_html:
            found.append((_("Bericht"), template.name))
    for view in SavedView.objects.filter(entity=entity):
        if key in str(view.config):
            found.append((_("Ansicht"), view.name))
    return found
