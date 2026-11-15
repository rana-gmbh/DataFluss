"""For IT on the server: the connected Macs and the conflicts sync could not settle alone."""
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.http import require_POST

from core import audit
from core.permissions import require

from . import codec
from .models import Conflict, Device

SECRET = gettext_lazy("[verschlüsselt]")
KINDS = {"people.person": gettext_lazy("Person"), "people.guardian": gettext_lazy("Eltern"), "people.classgroup": gettext_lazy("Klasse"),
         "vault.credential": gettext_lazy("Zugang"), "devices.asset": gettext_lazy("Gerät"), "devices.supportcase": gettext_lazy("Schaden"),
         "daz.profile": gettext_lazy("DaZ"), "daz.report": gettext_lazy("DaZ-Bericht"), "kindergarten.child": gettext_lazy("Kind"),
         "kindergarten.report": gettext_lazy("Kindergarten-Bericht")}


def _label(conflict):
    """Where the conflict is, in words: the record and the field."""
    try:
        model = codec.model_of(conflict.entity)
    except LookupError:
        return conflict.entity, conflict.key, None
    record = model._base_manager.filter(pk=conflict.record_id).first()
    name, sub = codec.split(conflict.key)
    try:
        field = str(model._meta.get_field(name).verbose_name)
    except Exception:
        field = name
    if sub:
        from masks.models import FieldDefinition

        definition = FieldDefinition.objects.filter(key=sub).first() if name == "custom" else None
        field = definition.label if definition else f"{field}: {sub}"
    kind = KINDS.get(conflict.entity) or capfirst(str(model._meta.verbose_name))
    if record is not None and conflict.entity == "people.person":
        kind = record.get_kind_display()
    return f"{kind}: {record}" if record else _("%(kind)s (gelöscht)") % {"kind": kind}, field, record


def _shown(conflict, value):
    name, sub = codec.split(conflict.key)
    secret = name in ("secrets", "totp_secret", "product_key_enc") or (isinstance(value, str) and value.startswith("v1:"))
    if secret:
        return str(SECRET)
    if value is None or value == codec.ABSENT:
        return _("(leer)")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _origin(node):
    device = Device.objects.filter(node=node).select_related("user").first()
    return f"{device.name} ({device.user.display_name})" if device else _("Server")


@require("admin.users")
def overview(request):
    rows = []
    for conflict in Conflict.objects.filter(resolved_at=None).select_related("device")[:200]:
        where, field, record = _label(conflict)
        rows.append({
            "c": conflict, "where": where, "field": field, "exists": record is not None and conflict.reason != "deleted",
            "kept": _shown(conflict, conflict.kept_value), "lost": _shown(conflict, conflict.lost_value),
            "kept_by": _origin(conflict.kept_origin), "lost_by": _origin(conflict.lost_origin),
        })
    return render(request, "sync/overview.html", {
        "devices": Device.objects.select_related("user").order_by("revoked_at", "-last_seen"),
        "conflicts": rows,
        "resolved": Conflict.objects.exclude(resolved_at=None).count(),
        "active_nav": "settings_home",
    })


@require("admin.users")
@require_POST
def revoke(request, pk):
    device = get_object_or_404(Device, pk=pk, revoked_at=None)
    device.revoked_at = timezone.now()
    device.save(update_fields=["revoked_at"])
    audit.log("device", f"Mac getrennt: {device.name} ({device.user.username})", request=request, target=device.pk)
    messages.success(request, _("%(name)s ist getrennt. Beim nächsten Kontakt löscht der Mac seine Kopie.") % {"name": device.name})
    return redirect("sync_overview")


@require("admin.users")
@require_POST
def resolve(request, pk):
    conflict = get_object_or_404(Conflict, pk=pk, resolved_at=None)
    if request.POST.get("take") == "other":
        model = codec.model_of(conflict.entity)
        record = model._base_manager.filter(pk=conflict.record_id).first()
        if record is None:
            messages.error(request, _("Der Datensatz ist gelöscht, die andere Fassung lässt sich nicht mehr übernehmen."))
            return redirect("sync_overview")
        name, sub = codec.split(conflict.key)
        field = model._meta.get_field(name)
        if sub is None:
            setattr(record, field.attname, codec.decode_value(field, conflict.lost_value))
        else:
            value = dict(getattr(record, field.attname) or {})
            if conflict.lost_value == codec.ABSENT:
                value.pop(sub, None)
            else:
                value[sub] = conflict.lost_value
            setattr(record, field.attname, value)
        record.save()  # a normal change, so every Mac gets it with the next sync
        conflict.resolution = "other"
    else:
        conflict.resolution = "kept"
    conflict.resolved_at, conflict.resolved_by = timezone.now(), request.user
    conflict.save(update_fields=["resolution", "resolved_at", "resolved_by"])
    audit.log("conflict", f"Abgleich entschieden: {conflict.entity} {conflict.key} ({conflict.resolution})", request=request, target=conflict.record_id)
    return redirect("sync_overview")
