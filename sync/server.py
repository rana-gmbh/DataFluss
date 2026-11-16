"""The server's side of sync: what a device pulls, and how its pushes are applied.

Pull: the device gets its scope's new records in full, changes after its cursor per key, deletions,
and evictions of records that left its scope. A page counts as received only when the next pull
confirms it, so a device that crashes while applying gets the same page again.

Push: every change carries the version it was based on. If nobody changed that key here meanwhile,
it applies. Otherwise the newer change applies and a Conflict waits for a person, and a device whose
value lost gets the record again in full.
"""
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max

from core.models import SchoolSettings, User

from . import apply, codec
from .models import CREATED, DELETED, Conflict, Device, FieldVersion, Holding, Journal, PushBatch
from .scope import Scope

MAX_RECORDS = 400
MAX_CHANGES = 2000

USER_FIELDS = ["username", "first_name", "last_name", "email", "role", "is_active", "password", "totp_secret",
               "totp_confirmed", "must_change_password", "oidc_subject"]


def _holdings(device):
    held = {}
    for entity, pk in Holding.objects.filter(device=device).values_list("entity", "record_id"):
        held.setdefault(entity, set()).add(pk)
    return held


# Records whose content depends on the role's field rules (sync/scope.py hide): sent again in full after a change.
RULED = ("people.person", "tables.record")


def resend_ruled(devices):
    """After a role's rights or an account's role changed: the devices get their people and table records
    again, so values that became hidden leave them and values that became visible arrive. Records that left
    the role's reach are evicted on the next pull anyway."""
    for device in devices:
        known = {tuple(r) for r in device.resend}
        add = [[entity, str(pk)] for entity, pks in _holdings(device).items() if entity in RULED for pk in pks
               if (entity, str(pk)) not in known]
        if add:
            device.resend = device.resend + add
            device.save(update_fields=["resend"])


def _versions(entity, pk):
    return dict(FieldVersion.objects.filter(entity=entity, record_id=pk).exclude(key=DELETED).values_list("key", "hlc"))


def _commit(device):
    """The device confirmed the last page: it now holds those records and has that cursor."""
    page = device.pending
    Holding.objects.bulk_create(
        [Holding(device=device, entity=e, record_id=uuid.UUID(i)) for e, i in page.get("add", [])], ignore_conflicts=True
    )
    for entity, pk in page.get("remove", []):
        Holding.objects.filter(device=device, entity=entity, record_id=uuid.UUID(pk)).delete()
    device.cursor = page.get("cursor", device.cursor)
    sent = {tuple(r) for r in page.get("resent", [])}
    device.resend = [r for r in device.resend if tuple(r) not in sent]


def _user_payload(scope):
    rows = []
    for user in User.objects.filter(pk__in=scope.users()):
        if user.pk == scope.user.pk:
            data = {name: getattr(user, name) for name in USER_FIELDS}
        else:
            # Others only as names, so the records that mention them resolve. They cannot sign in.
            data = {"username": user.username, "first_name": user.first_name, "last_name": user.last_name,
                    "role": user.role, "is_active": False, "password": "!", "totp_secret": "", "totp_confirmed": False,
                    "email": "", "must_change_password": False, "oidc_subject": ""}
        rows.append({"id": str(user.pk), **data})
    return rows


def _school_payload():
    school = SchoolSettings.load()
    return {"name": school.name, "short_name": school.short_name, "logo": school.logo.name or "",
            "confidential_footer": school.confidential_footer, "email_domain": school.email_domain,
            "require_2fa": school.require_2fa, "key_check": school.key_check,
            "languages": school.languages, "modules": school.modules}


def _files(model, data):
    names = []
    for field in model._meta.concrete_fields:
        if apply.is_file_field(model, field.name) and data.get(field.name):
            names.append(data[field.name])
    return names


def pull(device, ack=""):
    with transaction.atomic():
        device = Device.objects.select_for_update().get(pk=device.pk)
        if ack and device.pending.get("page") == ack:
            _commit(device)
        scope = Scope(device.user)
        wanted = {codec.entity_of(m): pks for m, pks in scope.records().items()}
        held = _holdings(device)
        top = Journal.objects.aggregate(top=Max("id"))["top"] or 0

        evict = {e: sorted(str(p) for p in pks - wanted.get(e, set())) for e, pks in held.items()}
        evict = {e: pks for e, pks in evict.items() if pks}
        resend = {(e, uuid.UUID(i)) for e, i in device.resend}
        records, files, add, resent = [], [], [], []
        complete = True
        for model in codec.synced_models():
            entity = codec.entity_of(model)
            todo = sorted((wanted.get(entity, set()) - held.get(entity, set())) | {pk for e, pk in resend if e == entity and pk in wanted.get(entity, set())})
            hide = scope.hide(model)
            for pk in todo:
                if len(records) >= MAX_RECORDS:
                    complete = False
                    break
                instance = model._base_manager.filter(pk=pk).first()
                if instance is None:
                    continue
                data = codec.snapshot(instance, hide)
                records.append({"e": entity, "id": str(pk), "f": data, "v": _versions(entity, pk)})
                files += _files(model, data)
                add.append([entity, str(pk)])
                if (entity, pk) in resend:
                    resent.append([entity, str(pk)])

        changes, deletes, removed = {}, [], []
        cursor = device.cursor
        if complete and not held:
            cursor = top  # A first copy: the records above are the current state, history adds nothing.
        elif complete:
            just_sent = {(e, uuid.UUID(i)) for e, i in add}
            holding = {(e, pk) for e, pks in held.items() for pk in pks if str(pk) not in evict.get(e, [])}
            entries = list(Journal.objects.filter(id__gt=device.cursor, id__lte=top).exclude(origin=device.node).order_by("id")[: MAX_CHANGES + 1])
            if len(entries) > MAX_CHANGES:
                entries = entries[:MAX_CHANGES]
                complete = False
            cursor = entries[-1].id if not complete else top
            for entry in entries:
                ref = (entry.entity, entry.record_id)
                if ref not in holding or ref in just_sent:
                    continue
                if entry.key == DELETED:
                    deletes.append({"e": entry.entity, "id": str(entry.record_id), "v": entry.hlc})
                    removed.append([entry.entity, str(entry.record_id)])
                    changes = {k: v for k, v in changes.items() if k[:2] != ref}
                elif entry.key == CREATED:
                    continue  # A record the device holds already exists there.
                else:
                    changes[(entry.entity, entry.record_id, entry.key)] = entry
            rendered = []
            for (entity, pk, key), entry in changes.items():
                model = codec.model_of(entity)
                value, exists = apply.current_value(model, pk, key, scope.hide(model))
                if not exists:
                    continue
                kind, version = apply.version_of(entity, pk, key)
                rendered.append({"e": entity, "id": str(pk), "k": key, "val": value, "v": version})
                name, sub = codec.split(key)
                if sub is None and apply.is_file_field(model, name) and value:
                    files.append(value)
            changes = rendered
        else:
            changes = []

        page = uuid.uuid4().hex
        device.pending = {
            "page": page, "cursor": cursor, "add": add, "resent": resent,
            "remove": removed + [[e, i] for e, pks in evict.items() for i in pks],
        }
        device.save(update_fields=["pending", "cursor", "resend"])
        return {
            "page": page, "done": complete, "server": settings.SERVER_ID,
            "evict": evict, "records": records, "changes": changes, "deletes": deletes,
            "users": _user_payload(scope), "school": _school_payload(), "files": sorted(set(files)),
        }


# ---------------------------------------------------------------- push


def _conflict(device, entity, pk, key, kept, kept_hlc, kept_origin, lost, lost_hlc, lost_origin, reason="same-field"):
    Conflict.objects.create(entity=entity, record_id=pk, key=key, kept_value=kept, kept_hlc=kept_hlc, kept_origin=kept_origin,
                            lost_value=lost, lost_hlc=lost_hlc, lost_origin=lost_origin, reason=reason, device=device)


def _receive_key(device, model, pk, key, raw, stamp, base, outcome):
    entity = codec.entity_of(model)
    kind, current = apply.version_of(entity, pk, key)
    if kind == DELETED:
        _conflict(device, entity, pk, key, None, current, settings.SERVER_ID, raw, stamp, device.node, "deleted")
        outcome["evict"].append([entity, str(pk)])
        return
    if current == base or not current:
        if not apply.set_key(model, pk, key, raw, stamp, device.node, journal=True, base=base):
            outcome["evict"].append([entity, str(pk)])
        return
    here, exists = apply.current_value(model, pk, key)
    if not exists:
        outcome["evict"].append([entity, str(pk)])
        return
    if here == raw:
        apply._versions(entity, pk, {key: max(stamp, current)}, device.node)
        return
    origin_here = FieldVersion.objects.filter(entity=entity, record_id=pk, key=kind).values_list("origin", flat=True).first() or settings.SERVER_ID
    if stamp > current:
        apply.set_key(model, pk, key, raw, stamp, device.node, journal=True, base=base)
        _conflict(device, entity, pk, key, raw, stamp, device.node, here, current, origin_here)
    else:
        _conflict(device, entity, pk, key, here, current, origin_here, raw, stamp, device.node)
        outcome["resend"].add((entity, pk))
    outcome["conflicts"] += 1


def _apply_items(device, scope, items, refused):
    outcome = {"accepted": 0, "conflicts": 0, "rejected": [], "evict": [], "resend": set(), "touched": set(), "when": {}}
    for item in items:
        entity = item["e"]
        pk = uuid.UUID(item["id"])
        try:
            model = codec.model_of(entity)
        except LookupError:
            outcome["rejected"].append({"e": entity, "id": str(pk), "reason": "unknown"})
            continue
        if (entity, pk) in refused:
            continue
        op = item["op"]
        keys = list(item.get("f", {})) if op == "upsert" else [item.get("k", DELETED)]
        stamps = item["v"].values() if isinstance(item.get("v"), dict) else [item.get("v")]
        scope.when = outcome["when"][(model, pk)] = scope.change_day(stamps)
        if not scope.may_write(model, pk, keys):
            refused.add((entity, pk))
            continue
        ignored = scope.ignored_keys(model)
        with transaction.atomic():  # one record's change; a unique clash refuses only this one
            try:
                if op == "upsert":
                    exists = model._base_manager.filter(pk=pk).exists()
                    if exists:
                        for name, raw in item["f"].items():
                            stamp = item["v"].get(name) or item["v"].get(CREATED)
                            if name not in ignored and stamp:
                                _receive_key(device, model, pk, name, raw, stamp, "", outcome)
                    else:
                        fields = {k: v for k, v in item["f"].items() if k not in ignored}
                        apply.upsert(model, pk, fields, item["v"], device.node, journal=True)
                elif op == "set":
                    if item["k"] not in ignored:
                        _receive_key(device, model, pk, item["k"], item.get("val"), item["v"], item.get("base", ""), outcome)
                elif op == "delete":
                    if model._base_manager.filter(pk=pk).exists():
                        apply.remove(model, pk, item["v"], device.node, journal=True)
            except IntegrityError:
                refused.add((entity, pk))
                outcome["rejected"].append({"e": entity, "id": str(pk), "reason": "clash"})
                continue
        outcome["accepted"] += 1
        if op != "delete":
            outcome["touched"].add((model, pk))
    return outcome


def push(device, batch, items):
    done = PushBatch.objects.filter(device=device, batch=batch).first()
    if done:
        return done.answer
    refused = set()
    for _ in range(4):
        sid = transaction.savepoint()
        scope = Scope(device.user)
        outcome = _apply_items(device, scope, items, refused)
        # A change may not take a record out of what this device may hold (or bring in one it may not).
        after = Scope(device.user)
        outside = {(codec.entity_of(m), pk) for m, pk in outcome["touched"]
                   if not after.holds(m, pk) or not after.allows(m, pk, outcome["when"].get((m, pk)))}
        if not outside:
            transaction.savepoint_commit(sid)
            break
        transaction.savepoint_rollback(sid)
        refused |= outside
    for entity, pk in refused:
        model = codec.model_of(entity)
        if model._base_manager.filter(pk=pk).exists():
            outcome["resend"].add((entity, pk))  # the server's version replaces the device's
        else:
            outcome["evict"].append([entity, str(pk)])  # the device made it; it goes away there
        outcome["rejected"].append({"e": entity, "id": str(pk), "reason": "not-allowed"})
    if outcome["resend"]:
        known = {tuple(r) for r in device.resend}
        device.resend = device.resend + [[e, str(p)] for e, p in outcome["resend"] if (e, str(p)) not in known]
        device.save(update_fields=["resend"])
    answer = {"accepted": outcome["accepted"], "conflicts": outcome["conflicts"], "rejected": outcome["rejected"], "evict": outcome["evict"]}
    PushBatch.objects.create(device=device, batch=batch, answer=answer)
    return answer


def push_atomic(device, batch, items):
    with transaction.atomic():
        return push(Device.objects.select_for_update().get(pk=device.pk), batch, items)
