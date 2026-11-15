"""Staff marked in Personal for a module's section (DaZ-Fachkraft, Kindergarten-Fachkraft) stand in that section.

Each section keeps its own entry (daz.Teacher, kindergarten.Educator), because reports and half-years
point to it and an entry also covers people who are not in Personal. A marked person gets an entry
whose name and email follow Personal; unmarking, archiving or deleting the person hides it, and old
reports keep it. This runs when a person is saved here, never for changes that sync brings in:
the node where the change was made sends the entry along.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction


def _sections():
    """{Person flag: the section's staff model}, from the loaded modules (core/modules.py staff_sections)."""
    from core import modules

    return {flag: model() for flag, model in modules.merged("staff_sections").items()}


def entry_id(flag, person):
    """The same id on every device, so a person marked on two devices offline still gets one entry."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"schulverwaltung:{flag}:{person.pk}")


def wanted(flag, person):
    return person.kind == person.STAFF and getattr(person, flag) and not person.archived and person.deleted_at is None


def _follow(entry, person, active):
    changed = False
    for name, value in (("person_id", person.pk), ("name", person.display_name[:120]), ("email", person.email.strip().lower()[:200]), ("active", active)):
        if getattr(entry, name) != value:
            setattr(entry, name, value)
            changed = True
    if changed:
        entry.save()


def _unlinked_match(model, person):
    """An older entry for the same person: same email, else same name."""
    loose = model.objects.filter(person=None)
    email = person.email.strip().lower()
    if email:
        found = loose.filter(email__iexact=email).first()
        if found:
            return found
    return loose.filter(name__iexact=person.display_name.strip()).first()


@transaction.atomic
def follow_person(person):
    if person.kind != person.STAFF:
        return
    for flag, model in _sections().items():
        entry = model.objects.filter(person=person).first()
        if wanted(flag, person):
            entry = entry or _unlinked_match(model, person) or model(pk=entry_id(flag, person))
            _follow(entry, person, True)
        elif entry and (entry.active or entry.name != person.display_name[:120]):
            _follow(entry, person, False)


@transaction.atomic
def connect(entry, person, flag):
    """Links an older entry to a person. If the person already has an entry, its reports and choices move over to this one."""
    model = type(entry)
    other = model.objects.filter(person=person).exclude(pk=entry.pk).first()
    if other:
        # Hidden relations too: the half-year teacher fields have no reverse name.
        for relation in model._meta.get_fields(include_hidden=True):
            if relation.auto_created and not relation.concrete and relation.one_to_many:
                for record in relation.related_model._base_manager.filter(**{relation.field.name: other}):
                    setattr(record, relation.field.name, entry)
                    record.save()
        entry.user = entry.user or other.user
        other.delete()
    _follow(entry, person, wanted(flag, person))


def staff_for(flag):
    """People who can be linked on a section page: staff marked for it."""
    from .models import Person

    return Person.objects.filter(kind=Person.STAFF, archived=False, **{flag: True})


def matches(query, flag):
    """Staff whose name fits what was typed, for adding them on a section page: [(person, already in the section)]."""
    from django.db.models import Q

    from .models import Person

    words = query.split()
    if len("".join(words)) < 2:
        return None
    found = Person.objects.filter(kind=Person.STAFF, archived=False)
    for word in words:
        found = found.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word))
    return [(person, wanted(flag, person)) for person in found[:10]]


def add(raw_pk, flag):
    """Marks a staff member for the section and returns their entry, or None if there is no such person."""
    from .models import Person

    try:
        person = Person.objects.filter(kind=Person.STAFF, archived=False, pk=raw_pk).first()
    except (ValueError, TypeError, ValidationError):
        return None
    if person is None:
        return None
    if not getattr(person, flag):
        setattr(person, flag, True)
        person.save()  # follow_person creates or links the entry
    return _sections()[flag].objects.filter(person=person).first()
