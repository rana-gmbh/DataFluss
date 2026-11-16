"""Sections of a record page that hold related data, placed by the mask."""
from django.core.exceptions import PermissionDenied

from core.models import ChangeLog
from devices.models import Place, SupportCase
from vault.models import Credential, Platform


def photo(request, person):
    return {}


def card(request, person):
    return {"cards": list(person.cards.all())}


def enrollments(request, person):
    return {"enrollments": person.enrollments.select_related("class_group")}


def guardians(request, person):
    rows = []
    platforms = Platform.active_for(Platform.GUARDIAN)
    for link in person.guardianships.select_related("guardian"):
        logins = []
        if request.user.can("vault.view"):
            existing = {c.platform_id: c for c in Credential.objects.filter(guardian=link.guardian).select_related("platform")}
            for platform in platforms:
                logins.append({"platform": platform, "credential": existing.get(platform.pk)})
        rows.append({"link": link, "logins": logins})
    return {"links": rows}


def devices(request, person):
    assignments = list(person.assignments.select_related("asset", "asset__category"))
    return {"current": [a for a in assignments if a.returned_at is None], "past": [a for a in assignments if a.returned_at]}


def cases(request, person):
    return {"cases": person.support_cases.select_related("asset")}


def logins(request, person):
    if not request.user.can("vault.view"):
        raise PermissionDenied
    platforms = Platform.active_for(person.kind)
    existing = {c.platform_id: c for c in person.credentials.select_related("platform")}
    rows = []
    for platform in platforms:
        credential = existing.get(platform.pk)
        fields = credential.read_all() if credential else [
            {"key": f["key"], "label": f["label"], "secret": bool(f.get("secret")), "value": ""} for f in platform.fields
        ]
        rows.append({"platform": platform, "credential": credential, "fields": fields})
    return {"rows": rows}


def attachments(request, person):
    return {"attachments": person.attachments.all()}


def comments(request, person):
    return {"comments": person.comments.select_related("author")}


def history(request, person):
    """The latest changes. Form fields in custom are logged per key (custom.<key>) and named like on the form;
    rows of the whole custom dict, from before that, stay out."""
    from masks.services import field_map

    from core import fieldrules

    fields = {f.name: f.verbose_name for f in person._meta.concrete_fields}
    fields.update({f"custom.{key}": fd.display_label for key, fd in field_map(person.kind, include_archived=True).items()})
    # Changes of fields the role does not see stay out (a column's change is named by the column, a form
    # field's by custom.<key>; the class field is the column current_class).
    unseen = fieldrules.hidden(getattr(request, "user", None), person.kind)
    unseen = unseen | {f"custom.{key}" for key in unseen} | ({"current_class"} if "klasse" in unseen else set())
    rows = ChangeLog.objects.filter(entity="people.person", record_id=person.pk).exclude(field="custom").exclude(field__in=unseen)
    rows = rows.select_related("user")[:25]
    return {"changes": [{"row": r, "label": fields.get(r.field, r.field.removeprefix("custom."))} for r in rows]}


BLOCK_VIEWS = {
    "photo": photo, "card": card, "enrollments": enrollments, "guardians": guardians, "devices": devices,
    "cases": cases, "logins": logins, "attachments": attachments, "comments": comments, "history": history,
}  # modules add theirs (core/modules.py add_block)
