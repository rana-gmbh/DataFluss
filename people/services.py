from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from sync import journal

from .models import ClassGroup, Person, SchoolYear


class RolloverRefused(Exception):
    """The message is shown to the user."""


def parse_card_input(text, input_format):
    """Readers deliver hex in the current system; the stored card number is decimal."""
    text = (text or "").strip().replace(" ", "")
    if not text:
        return ""
    if input_format == "dec":
        return text if text.isdigit() else ""
    try:
        return str(int(text, 16))
    except ValueError:
        return ""


def next_class_name(group):
    """5a becomes 6a. The last class has no successor."""
    if group.kind != ClassGroup.KLASSE or not group.level:
        return None
    suffix = group.name[len(str(group.level)):]
    return f"{group.level + 1}{suffix}"


def rollover_preview(new_name, last_level=12):
    current = SchoolYear.current()
    rows = []
    for group in ClassGroup.objects.filter(school_year=current, kind=ClassGroup.KLASSE):
        count = group.members.filter(archived=False).count()
        leaving = bool(group.level and group.level >= last_level)
        rows.append({"group": group, "count": count, "target": "Austritt" if leaving else next_class_name(group)})
    groups = ClassGroup.objects.filter(school_year=current, kind=ClassGroup.GRUPPE)
    for group in groups:
        rows.append({"group": group, "count": group.members.filter(archived=False).count(), "target": group.name})
    return rows


@transaction.atomic
def rollover_commit(name, start, end, last_level=12):
    """Create the new year and move every pupil. History stays in the enrollments."""
    list(SchoolYear.objects.select_for_update().filter(is_current=True))  # A second click waits here, then finds the new year.
    current = SchoolYear.current()
    today = timezone.localdate()
    known = SchoolYear.objects.filter(name=name).first()
    if known and (known.is_current or known.classes.exists()):
        raise RolloverRefused(_("Das Schuljahr %(name)s gibt es bereits. Der Wechsel wurde nicht noch einmal ausgeführt.") % {"name": name})
    if current and current.start > today:
        raise RolloverRefused(_("Das Schuljahr %(name)s hat noch nicht begonnen. Der Wechsel wurde nicht noch einmal ausgeführt.") % {"name": current.name})
    new_year, _created = SchoolYear.objects.get_or_create(name=name, defaults={"start": start, "end": end})
    moved = left = 0
    exit_group, _created = ClassGroup.objects.get_or_create(
        name=f"Austritt {today.year}", school_year=None, defaults={"kind": ClassGroup.AUSTRITT}
    )
    for group in ClassGroup.objects.filter(school_year=current).exclude(kind=ClassGroup.AUSTRITT):
        if group.kind == ClassGroup.KLASSE and group.level and group.level >= last_level:
            for person in group.members.filter(archived=False):
                person.move_to_class(exit_group, start)
                person.archived, person.exit_date = True, person.exit_date or today
                person.save()
                left += 1
            continue
        target_name = next_class_name(group) or group.name
        target, _created = ClassGroup.objects.get_or_create(
            name=target_name, school_year=new_year,
            defaults={"kind": group.kind, "level": (group.level + 1) if group.level else None, "color": group.color},
        )
        for person in group.members.filter(archived=False):
            person.move_to_class(target, start)
            moved += 1
    journal.update(SchoolYear.objects.all(), is_current=False)
    new_year.is_current = True
    new_year.save()
    return moved, left
