"""What goes into a container, and how it replaces everything on another instance.

The rows are written with the schema of the exporting instance. An import rebuilds exactly that
schema, loads the rows and then runs the remaining migrations, so an old backup still fits a newer
version. Everything happens in one transaction, so a failed import changes nothing.
"""
import gzip
import io
import json
import os
import shutil
import tarfile
import uuid
from contextlib import nullcontext
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.color import no_style
from django.core.management.sql import emit_post_migrate_signal
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import DateField
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_noop

from core import modules, sealed
from core.models import SchoolSettings, User
from devices.models import Asset
from masks.models import FieldDefinition
from people.models import Person
from vault import crypto
from vault.models import Credential

from .container import BROKEN, ContainerError, Reader, Writer

FORMAT = 1
PART_ROWS = 5000
MANIFEST = "manifest.json"
EXTENSION = ".svbackup"
# Sessions and update state belong to one server. Types and permissions are rebuilt by the migrations.
LEFT_OUT = {
    "sessions.session",
    "contenttypes.contenttype",
    "auth.permission",
    "auth.group_permissions",
    "core.user_user_permissions",
    "core.updatestate",
}


def _no_progress(*args):
    pass


def _models():
    return [
        m
        for m in apps.get_models(include_auto_created=True)
        if m._meta.label_lower not in LEFT_OUT and m._meta.managed and not m._meta.proxy
    ]


def _encode(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    raise TypeError(f"Kein Sicherungsformat für {type(value).__name__}")


# Secrets travel in plain text inside the container, because the other instance has another vault key.
# The container's password protects them there. The key of this server never leaves it.
def _secrets():
    """Model -> (field, function that sends the field's value through `convert`)."""
    person_keys = set(FieldDefinition.objects.filter(type="secret").values_list("key", flat=True))

    def person(custom, pk, convert):
        return {k: convert(v, f"person:{pk}:{k}") if k in person_keys else v for k, v in custom.items()}

    return {
        User: ("totp_secret", lambda value, pk, convert: convert(value, f"totp:{pk}")),
        Asset: ("product_key_enc", lambda value, pk, convert: convert(value, f"asset:{pk}:key")),
        Credential: ("secrets", lambda value, pk, convert: {k: convert(v, f"credential:{pk}:{k}") for k, v in value.items()}),
        Person: ("custom", person),
    }


def _rows(model, secrets):
    fields = model._meta.concrete_fields
    names = [f.attname for f in fields]
    secret_field, rewrite = secrets.get(model, (None, None))
    for values in model._base_manager.order_by().values_list(*names).iterator(chunk_size=PART_ROWS):
        row = dict(zip(names, values))
        if secret_field:
            row[secret_field] = rewrite(row[secret_field], row["id"], crypto.decrypt)
        yield json.dumps(row, default=_encode, ensure_ascii=False)


def _add_bytes(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mtime = len(data), int(timezone.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def _add_rows(tar, model, secrets):
    """Parts stay in memory, so decrypted secrets never land on the disk in plain text."""
    label, index, part = model._meta.label_lower, 0, []

    def flush():
        nonlocal index, part
        _add_bytes(tar, f"data/{label}.{index:04}.jsonl.gz", gzip.compress("\n".join(part).encode(), 6))
        index, part = index + 1, []

    for line in _rows(model, secrets):
        part.append(line)
        if len(part) == PART_ROWS:
            flush()
    if part:
        flush()


def _media_files():
    root = Path(settings.MEDIA_ROOT)
    for folder, subfolders, names in os.walk(root):
        if Path(folder) == root:
            subfolders[:] = [d for d in subfolders if not d.startswith(".")]
        for name in names:
            path = Path(folder) / name
            if path.is_file() and not path.is_symlink():
                yield path


def _add_file(tar, path, arcname):
    """Containers hold the files in plain text, so they move between an encrypted client and a plain server."""
    with path.open("rb") as handle:
        info = tar.gettarinfo(arcname=arcname, fileobj=handle)
        if not sealed.active():
            tar.addfile(info, handle)
            return
        try:
            data = sealed.unseal(handle.read())
        except sealed.Unreadable as exc:
            raise ContainerError(_("Sicherung abgebrochen bei %(name)s.") % {"name": arcname.removeprefix("media/")} + f" {exc}")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _applied_migrations():
    return sorted(MigrationRecorder(connection).applied_migrations())


def _manifest(school, models, files):
    return {
        "format": FORMAT,
        "created_at": timezone.now().isoformat(),
        "version": settings.APP_VERSION,
        "datafluss": settings.DATAFLUSS_VERSION,
        "modules": modules.manifest(),
        "school": school,
        "migrations": _applied_migrations(),
        "rows": {m._meta.label_lower: m._base_manager.count() for m in models},
        "files": len(files),
        "bytes": sum(sealed.plain_size(f.stat().st_size) for f in files),
    }


def create(target, password, progress=_no_progress):
    """Writes the container to `target` and returns its manifest. The file appears only when complete."""
    partial = target.with_name(target.name + ".part")
    models, files = _models(), list(_media_files())
    school = SchoolSettings.load().name
    try:
        with partial.open("wb") as raw:
            sink = Writer(raw, password)
            with tarfile.open(fileobj=sink, mode="w|", bufsize=1024 * 1024) as tar:
                # One snapshot, so that a change during the backup cannot leave it half old, half new.
                with transaction.atomic():
                    if connection.vendor == "postgresql":  # SQLite already reads one snapshot inside a transaction.
                        with connection.cursor() as cursor:
                            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    manifest = _manifest(school, models, files)
                    secrets = _secrets()
                    _add_bytes(tar, MANIFEST, json.dumps(manifest).encode())
                    for index, model in enumerate(models):
                        progress(gettext_noop("Datenbank wird gesichert"), index, len(models))
                        _add_rows(tar, model, secrets)
                root = Path(settings.MEDIA_ROOT)
                for index, path in enumerate(files):
                    if index % 20 == 0:
                        progress(gettext_noop("Bilder und Dokumente werden gesichert"), index, len(files))
                    try:
                        _add_file(tar, path, f"media/{path.relative_to(root).as_posix()}")
                    except FileNotFoundError:
                        pass  # Removed by someone a moment ago.
            sink.close()
            raw.flush()
            os.fsync(raw.fileno())
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return manifest


def _open(raw, password):
    reader = Reader(raw, password)
    try:
        return reader, tarfile.open(fileobj=reader, mode="r|")
    except tarfile.TarError:
        raise ContainerError(str(BROKEN))


def read_manifest(path, password):
    with path.open("rb") as raw:
        _reader, tar = _open(raw, password)
        member = tar.next()
        if member is None or member.name != MANIFEST:
            raise ContainerError(str(BROKEN))
        return json.load(tar.extractfile(member))


def verify(path, password, progress=_no_progress):
    """Reads the whole container, so damage shows now and not on the day it is needed."""
    with path.open("rb") as raw:
        reader, tar = _open(raw, password)
        try:
            for count, _member in enumerate(tar):
                if count % 20 == 0:
                    progress(gettext_noop("Sicherung wird geprüft"), count, None)
        except tarfile.TarError:
            raise ContainerError(str(BROKEN))
        reader.verify_end()


def _foreign_apps(manifest):
    """Apps in the backup that are not loaded here: a module this installation lacks."""
    return {app for app, _name in manifest["migrations"]} - modules.loaded_apps()


def _missing_modules(manifest):
    """Modules the backup has rows of but this installation lacks. Their empty tables are simply left out."""
    from schule import modules as catalog

    foreign = _foreign_apps(manifest)
    return {catalog.module_of(label.split(".")[0]) or label.split(".")[0]
            for label, count in manifest["rows"].items() if count and label.split(".")[0] in foreign}


def incompatible(manifest):
    """Returns why this instance cannot take the container, or an empty string."""
    if manifest.get("format") != FORMAT:
        return _("Diese Sicherung stammt aus einer neueren Version. Bitte zuerst diese Installation aktualisieren.")
    missing = _missing_modules(manifest)
    if missing:
        return _("Diese Sicherung enthält Daten des Moduls %(names)s, das in dieser DataFluss-Version fehlt. "
                 "Sie lässt sich nur in einer DataFluss-Version mit diesem Modul einspielen.") % {"names": modules.titles(missing)}
    known = MigrationExecutor(connection).loader.graph.nodes
    foreign = _foreign_apps(manifest)
    if any(tuple(m) not in known for m in manifest["migrations"] if m[0] not in foreign):
        return _("Diese Sicherung stammt aus einer neueren Version. Bitte zuerst diese Installation aktualisieren.")
    return ""


def _unpack_files(path, password, stage, progress):
    """Reads the whole container before anything is changed, so a damaged file stops here."""
    with path.open("rb") as raw:
        reader, tar = _open(raw, password)
        count = 0
        for member in tar:
            if member.name == MANIFEST or (member.name.startswith("data/") and member.isfile()):
                continue
            target = (stage / member.name.removeprefix("media/")).resolve()
            if not member.isfile() or not member.name.startswith("media/") or not target.is_relative_to(stage.resolve()):
                raise ContainerError(_("Diese Sicherungsdatei enthält ungültige Einträge."))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                source = tar.extractfile(member)
                if sealed.active():  # Sealed before it is written, so plain text never lands on this disk.
                    out.write(sealed.seal(source.read()))
                else:
                    shutil.copyfileobj(source, out)
            count += 1
            if count % 20 == 0:
                progress(gettext_noop("Sicherung wird geprüft"), count, None)
        reader.verify_end()


def _drop_all_tables():
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names(cursor):
            cascade = " CASCADE" if connection.vendor == "postgresql" else ""
            cursor.execute(f"DROP TABLE IF EXISTS {connection.ops.quote_name(table)}{cascade}")


def _build_schema(migrations):
    """Creates the tables as they were when the backup was made and returns the models of that time."""
    executor = MigrationExecutor(connection)
    plan, seen = [], set()
    for target in map(tuple, migrations):
        for key in executor.loader.graph.forwards_plan(target):
            if key not in seen:
                seen.add(key)
                plan.append((executor.loader.graph.nodes[key], False))
    state = executor.migrate([(m.app_label, m.name) for m, _ in plan], plan=plan)
    return state.apps


def _empty_tables(models):
    """Migrations may bring their own rows."""
    connection.check_constraints()
    tables = [m._meta.db_table for m in models]
    connection.ops.execute_sql_flush(connection.ops.sql_flush(no_style(), tables, allow_cascade=True))


def _load_rows(path, password, old_apps, progress, skip=frozenset()):
    """Loads the rows; files of the apps in `skip` (an empty module this installation lacks) are passed over."""
    loaded = {}
    with path.open("rb") as raw:
        _reader, tar = _open(raw, password)
        for member in tar:
            if member.name.startswith("media/"):
                break
            if member.name == MANIFEST:
                continue
            label = member.name.removeprefix("data/").split(".jsonl")[0].rsplit(".", 1)[0]
            if label.split(".")[0] in skip:
                continue
            try:
                model = old_apps.get_model(label)
            except (LookupError, ValueError):
                raise ContainerError(_("Diese Sicherungsdatei enthält ungültige Einträge."))
            if model not in loaded:
                progress(gettext_noop("Datenbank wird eingespielt"), len(loaded), None)
                loaded[model] = {f.attname: f for f in model._meta.concrete_fields}
                # Dates must stay as they were saved.
                for field in loaded[model].values():
                    if isinstance(field, DateField):
                        field.auto_now = field.auto_now_add = False
            fields = loaded[model]
            lines = gzip.decompress(tar.extractfile(member).read()).decode().splitlines()
            objects = [model(**{k: fields[k].to_python(v) for k, v in json.loads(line).items()}) for line in lines]
            model._base_manager.bulk_create(objects, batch_size=1000)
    return list(loaded)


def _seal_secrets():
    """Encrypts the secrets with the key of this server."""
    for model, (field, rewrite) in _secrets().items():
        for pk, value in model._base_manager.values_list("pk", field).iterator():
            sealed = rewrite(value, pk, crypto.encrypt)
            if sealed != value:
                model._base_manager.filter(pk=pk).update(**{field: sealed})


def _replace_database(path, password, manifest, progress):
    # SQLite (the desktop client) changes tables inside a transaction only with foreign keys off.
    # check_constraints() below still proves every reference before the commit.
    sqlite = connection.vendor == "sqlite"
    # A restore bypasses the sync journal on purpose: every device must resync afterwards.
    with connection.constraint_checks_disabled() if sqlite else nullcontext(), transaction.atomic():
        progress(gettext_noop("Datenbank wird eingespielt"), None, None)
        _drop_all_tables()
        foreign = _foreign_apps(manifest)  # empty, or incompatible() would have refused the backup
        old_apps = _build_schema([m for m in manifest["migrations"] if m[0] not in foreign])
        _empty_tables([old_apps.get_model(label) for label in manifest["rows"] if label.split(".")[0] not in foreign])
        models = _load_rows(path, password, old_apps, progress, skip=foreign)
        connection.check_constraints()
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(), models):
                cursor.execute(statement)
        progress(gettext_noop("Datenbank wird angepasst"), None, None)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        emit_post_migrate_signal(0, False, connection.alias)
        _seal_secrets()
        SchoolSettings.objects.update(key_check=crypto.derived_password("key-check"))
        from sync.models import Dataset

        Dataset.renew()  # connected computers stop syncing against the replaced data


def _swap_media(stage):
    root = Path(settings.MEDIA_ROOT)
    old = root / f".old-{uuid.uuid4().hex}"
    old.mkdir()
    for entry in root.iterdir():
        if not entry.name.startswith("."):
            entry.rename(old / entry.name)
    for entry in stage.iterdir():
        entry.rename(root / entry.name)
    shutil.rmtree(old, ignore_errors=True)


def restore(path, password, manifest, before=_no_progress, progress=_no_progress):
    """Replaces all data with the container. `before` runs once the container has proved to be sound."""
    problem = incompatible(manifest)
    if problem:
        raise ContainerError(problem)
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    for leftover in root.glob(".restore-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    stage = root / f".restore-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        _unpack_files(path, password, stage, progress)
        before()
        _replace_database(path, password, manifest, progress)
        _swap_media(stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
