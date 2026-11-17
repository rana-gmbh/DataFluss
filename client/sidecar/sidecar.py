"""Runs the Django app for one database of the desktop client.

The shell starts one sidecar per open database and talks to it over stdin and stdout, so there is
no port or socket that another program on the computer could reach. Every message is a frame:

    [4 bytes header length][header JSON][4 bytes body length][body]

Request header:  {"id", "method", "path", "query", "headers": [[name, value], ...]}
Response header: {"id", "status", "headers": [[name, value], ...]}
The first frame the sidecar sends is {"ready": true, ...} once Django can answer.

A new database starts with a task (SIDECAR_TASK=create, demo or restore). The shell then sends one
control frame {"control": task} whose body is the task's JSON, so passwords never travel in the
environment. The sidecar answers with {"progress": {...}} frames and one {"task": "done", ...} or
{"task": "failed", "error": "..."}; after "done" it serves as usual, after "failed" it exits.

Django sees every request as https://localhost, so the production security settings apply
unchanged. Cookies stay in the shell, never in the WebView.

Environment, set by the shell:
    SCHULE_DB_DIR   the database folder (db.sqlite, media/, backups/)
    SQLCIPHER_KEY   64 hex characters, from the Keychain
    VAULT_KEY       base64, from the Keychain (local databases only)
    SECRET_KEY      from the Keychain
    SYNC_TOKEN      the device token, from the Keychain (databases connected to a server only)
    MEDIA_KEY       base64, from the Keychain, for photos and attachments
    SCHULE_CLIENT_DB_NAME   the database's name, shown in the sidebar's switcher
"""
import json
import os
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote_to_bytes

STARTED = time.perf_counter()
HOST = "localhost"
ORIGIN = f"https://{HOST}"


def _as_sidecar_origin(value):
    """app://<database>/x (macOS) or http://app.<database>/x (Windows) becomes https://localhost/x."""
    for scheme in ("app://", "http://app.", "https://app."):
        if value.startswith(scheme):
            rest = value[len(scheme):]
            return ORIGIN + (rest[rest.index("/"):] if "/" in rest else "")
    return value


def _read_exact(stream, size):
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def read_frame(stream):
    (length,) = struct.unpack(">I", _read_exact(stream, 4))
    header = json.loads(_read_exact(stream, length))
    (length,) = struct.unpack(">I", _read_exact(stream, 4))
    return header, _read_exact(stream, length)


class Writer:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, header, body=b""):
        raw = json.dumps(header).encode()
        with self.lock:
            self.stream.write(struct.pack(">I", len(raw)) + raw + struct.pack(">I", len(body)) + body)
            self.stream.flush()


def _environment(db_dir):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "schule.settings")
    os.environ["DATABASE_URL"] = "sqlcipher://"
    os.environ["SQLITE_PATH"] = str(db_dir / "db.sqlite")
    os.environ["MEDIA_ROOT"] = str(db_dir / "media")
    os.environ["BACKUP_ROOT"] = str(db_dir / "backups")
    os.environ["ALLOWED_HOSTS"] = HOST
    os.environ["CSRF_TRUSTED_ORIGINS"] = ORIGIN
    os.environ.setdefault("XDG_CACHE_HOME", str(db_dir / "cache"))  # fontconfig writes its cache here
    if getattr(sys, "frozen", False):
        # Pango and fontconfig inside the bundle, not from Homebrew or MSYS2.
        bundle = Path(sys._MEIPASS)
        conf = "fonts.windows.conf" if sys.platform == "win32" else "fonts.conf"
        os.environ.setdefault("FONTCONFIG_PATH", str(bundle / "fontconfig"))
        os.environ.setdefault("FONTCONFIG_FILE", str(bundle / "fontconfig" / conf))
    for name in ("media", "backups", "cache"):
        (db_dir / name).mkdir(parents=True, exist_ok=True)


def _bundled_libraries():
    """WeasyPrint opens Pango by bare name. The hardened runtime ignores DYLD_* paths, so point it at the bundle."""
    if not getattr(sys, "frozen", False):
        return
    import cffi

    bundle = Path(sys._MEIPASS)
    original = cffi.FFI.dlopen

    def dlopen(self, name, flags=0):
        if isinstance(name, str) and "/" not in name and "\\" not in name:
            # WeasyPrint asks for libpango-1.0.dylib; the bundle holds the real file, libpango-1.0.0.dylib.
            for candidate in (name, f"{name}.dylib", f"{name}.dll", name.replace(".dylib", ".0.dylib")):
                if (bundle / candidate).is_file():
                    return original(self, str(bundle / candidate), flags)
        return original(self, name, flags)

    cffi.FFI.dlopen = dlopen


class TaskError(Exception):
    """A problem the person can fix. The message is shown on the start screen."""


def _choose_modules(data):
    """The optional modules a new database uses (SchoolSettings.modules). Missing from the task: all loaded ones."""
    from core import modules
    from core.models import SchoolSettings

    without = [name for name in (data.get("without") or []) if name == "schulverwaltung"]  # the card "Leer"
    if without:
        from core.permissions import ensure_roles

        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(modules_off=without)
        ensure_roles()  # Team and Gast instead of the school's roles
    if data.get("modules") is None:
        return
    offered = {module_id for module_id, _title in modules.optional_modules()}
    unknown = set(data["modules"]) - offered
    if unknown:
        raise TaskError(f"Dieses Modul fehlt in dieser DataFluss-Version: {', '.join(sorted(unknown))}")
    SchoolSettings.load()
    SchoolSettings.objects.filter(pk=1).update(modules=sorted(data["modules"]))


def _task_create(data, progress, db_dir):
    """A new, empty school database with its first IT account. The account sets up 2FA at first sign-in."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.core.management import call_command

    from core.models import Role, SchoolSettings, User

    school, username, password = (data.get("school") or "").strip(), (data.get("username") or "").strip(), data.get("password") or ""
    if not school or not username:
        raise TaskError("Bitte den Namen der Schule und einen Benutzernamen angeben.")
    try:
        validate_password(password, User(username=username))
    except ValidationError as exc:
        raise TaskError(" ".join(exc.messages))
    progress("Datenbank wird eingerichtet")
    _choose_modules(data)
    call_command("setup_school", verbosity=0)
    SchoolSettings.objects.filter(pk=1).update(name=school, short_name=(data.get("short_name") or school)[:60])
    user = User.objects.create_user(username, password=password, role=Role.IT)
    _apply_templates(data, progress, user, demo=False)
    return {}


def _apply_templates(data, progress, user, demo):
    """The module templates chosen in the wizard (tables/blueprints.py), with their sample records for a demo."""
    from tables import blueprints

    wanted = [t for t in (data.get("templates") or []) if isinstance(t, str)]
    if not wanted:
        return
    found = blueprints.available()
    for template_id in wanted:
        template = found.get(template_id)
        if template is None:
            raise TaskError(f"Diese Vorlage fehlt in dieser DataFluss-Version: {template_id}")
        progress("Vorlage wird angewendet")
        try:
            blueprints.apply(template, user, demo=demo)
        except blueprints.BlueprintError as exc:
            raise TaskError(str(exc))


def _task_demo(data, progress, db_dir):
    """Invented people to try the app with. Five accounts without a second factor."""
    from django.conf import settings
    from django.core.management import call_command

    from core.models import SchoolSettings

    progress("Beispieldaten werden angelegt")
    _choose_modules(data)
    call_command("setup_school", verbosity=0)
    SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
    if "schulverwaltung" in (data.get("without") or []):
        # No invented people without Schulverwaltung: one IT account, and the templates' sample records.
        import secrets

        from core.models import Role, User

        password = "Demo-" + secrets.token_urlsafe(9)  # as seed_demo makes them
        user = User.objects.create_user("it", password=password, role=Role.IT)
        _apply_templates(data, progress, user, demo=True)
        return {"logins": [{"username": "it", "password": password}]}
    settings.BASE_DIR = db_dir  # seed_demo writes the logins to BASE_DIR/.secrets
    call_command("seed_demo", verbosity=0)
    from core.models import Role, User

    _apply_templates(data, progress, User.objects.filter(role=Role.IT).first(), demo=True)
    logins = (db_dir / ".secrets" / "demo-logins.txt").read_text().splitlines()
    return {"logins": [dict(zip(("username", "password"), line.split("\t", 1))) for line in logins if "\t" in line]}


def _task_restore(data, progress, db_dir):
    """A backup (.svbackup) from a server or another client becomes this database."""
    from archive import snapshot
    from archive.container import ContainerError
    from core.models import SchoolSettings

    path, password = Path(data.get("path") or ""), data.get("password") or ""
    if not path.is_file():
        raise TaskError("Die Sicherungsdatei wurde nicht gefunden.")
    try:
        manifest = snapshot.read_manifest(path, password)
    except ContainerError as exc:
        raise TaskError(str(exc))
    problem = snapshot.incompatible(manifest)
    if problem:
        raise TaskError(problem)

    def step(text, done=None, total=None):
        progress(text, done, total)

    try:
        snapshot.restore(path, password, manifest, progress=step)
    except ContainerError as exc:
        raise TaskError(str(exc))
    if not data.get("keep_secrets"):
        progress("Zugangsdaten werden entfernt")
        _drop_secrets()
    school = SchoolSettings.load()
    return {"school": school.name, "people": manifest["rows"].get("people.person", 0), "made": manifest.get("created_at", "")}


def _drop_secrets():
    """Without the logins a stolen laptop gives away no passwords. 2FA secrets stay, so people can sign in."""
    from devices.models import Asset
    from masks.models import FieldDefinition
    from people.models import Person
    from vault.models import Credential

    Credential.objects.update(secrets={})
    Asset._base_manager.exclude(product_key_enc="").update(product_key_enc="")
    keys = set(FieldDefinition.objects.filter(type="secret").values_list("key", flat=True))
    for person in Person._base_manager.exclude(custom={}).only("pk", "custom").iterator():
        if keys & set(person.custom):
            Person._base_manager.filter(pk=person.pk).update(custom={k: v for k, v in person.custom.items() if k not in keys})


def _task_connect(data, progress, db_dir):
    """Connects a new, empty database to a server: the user's own login and code, then a first copy."""
    import socket

    from django.conf import settings

    from sync import client
    from vault import crypto

    url, username, password = (data.get("url") or "").strip(), (data.get("username") or "").strip(), data.get("password") or ""
    if not url or not username or not password:
        raise TaskError("Bitte Adresse des Servers, Benutzername und Passwort angeben.")
    progress("Verbindung zum Server")
    try:
        answer = client.register(url, username, password, data.get("code") or "", data.get("name") or socket.gethostname())
    except client.SyncError as exc:
        raise TaskError(str(exc))
    # From now on this database reads and writes passwords with the server's vault key.
    settings.VAULT_KEY = answer["vault_key"]
    crypto.reset_key_cache()
    try:
        client.sync_once(answer["token"], progress)
    except client.SyncError as exc:
        raise TaskError(f"Verbunden, aber die erste Übertragung ist fehlgeschlagen: {exc}")
    os.environ["SYNC_TOKEN"] = answer["token"]
    state = client.ClientState.load()
    return {"token": answer["token"], "vault_key": answer["vault_key"], "school": answer["school"],
            "server": answer["server"], "url": state.server_url}


TASKS = {"create": _task_create, "demo": _task_demo, "restore": _task_restore, "connect": _task_connect}


def _run_task(task, stdin, writer, db_dir):
    """Returns True when the database is ready to serve."""
    header, body = read_frame(stdin)
    if header.get("control") != task:
        writer.send({"task": "failed", "error": "Unerwarteter Auftrag."})
        return False

    def progress(text, done=None, total=None):
        writer.send({"progress": {"text": text, "done": done, "total": total}})

    try:
        result = TASKS[task](json.loads(body or b"{}"), progress, db_dir)
    except TaskError as exc:
        writer.send({"task": "failed", "error": str(exc)})
        return False
    except Exception:
        import traceback

        traceback.print_exc()
        writer.send({"task": "failed", "error": "Das hat nicht geklappt. Details stehen im Protokoll der Datenbank."})
        return False
    writer.send({"task": "done", **result})
    return True


def _environ(header, body):
    path = header.get("path") or "/"
    environ = {
        "REQUEST_METHOD": header.get("method", "GET"),
        "SCRIPT_NAME": "",
        "PATH_INFO": unquote_to_bytes(path).decode("latin-1"),  # WSGI wants it decoded, as bytes in latin-1
        "QUERY_STRING": header.get("query", ""),
        "SERVER_NAME": HOST,
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "127.0.0.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body)),
    }
    for name, value in header.get("headers", []):
        key = name.upper().replace("-", "_")
        if key in ("ORIGIN", "REFERER"):
            value = _as_sidecar_origin(value)
        if key == "HOST":
            value = HOST
        if key == "CONTENT_TYPE":
            environ["CONTENT_TYPE"] = value
        elif key != "CONTENT_LENGTH":
            environ[f"HTTP_{key}"] = value
    environ.setdefault("HTTP_HOST", HOST)
    return environ


def _handle(app, writer, header, body):
    started = time.perf_counter()
    reply = {}

    def start_response(status, headers, exc_info=None):
        reply["status"] = int(status.split(" ", 1)[0])
        reply["headers"] = [[k, v] for k, v in headers]

    try:
        result = app(_environ(header, body), start_response)
        try:
            content = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()
    except Exception as exc:  # Django has already logged it; the shell must still get an answer.
        print(f"sidecar: {header.get('path')}: {exc!r}", file=sys.stderr)
        reply, content = {"status": 500, "headers": [["Content-Type", "text/plain; charset=utf-8"]]}, b"Interner Fehler"
    ms = (time.perf_counter() - started) * 1000
    if os.environ.get("SIDECAR_LOG") == "1":
        print(f"sidecar: {header.get('method')} {header.get('path')} {reply['status']} {ms:.1f} ms", file=sys.stderr)
    writer.send({"id": header["id"], "status": reply["status"], "headers": reply["headers"], "ms": round(ms, 1)}, content)


def _fetch_missing(missing):
    """A connected database whose module this version lacks gets it from its server (schule/bundles.py).
    Returns "" when every module was fetched, else why not, in words for the start screen."""
    from sync import client

    state = client.ClientState.load()
    if state is None or state.revoked:
        return "Diese Datenbank ist nicht mehr mit ihrem Server verbunden."
    try:
        if all([client.fetch_module(state.server_url, module_id, token=os.environ["SYNC_TOKEN"]) for module_id in sorted(missing)]):
            return ""
        return "Dieser Computer hat keinen Ordner für Module."
    except client.SyncError as exc:
        print(f"sidecar: module not fetched: {exc}", file=sys.stderr)
        return str(exc)


def main():
    # Only frames may reach the shell. Anything printed by mistake goes to stderr instead.
    frames_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    writer = Writer(frames_out)
    stdin = sys.stdin.buffer

    db_dir = Path(os.environ["SCHULE_DB_DIR"])
    _environment(db_dir)
    _bundled_libraries()

    import django

    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application

    setup_ms = (time.perf_counter() - STARTED) * 1000
    from core import modules

    missing = modules.missing_with_data()
    if missing:
        # Before the migrations, so the database stays as it is. The shell shows the end of this log.
        # A connected database gets the module from its server; the shell then starts it once more (exit 4).
        reason = _fetch_missing(missing) if os.environ.get("SYNC_TOKEN") else None
        if reason == "":
            print(f"{modules.missing_message(missing)}\n\nDas Modul wurde vom Server geladen. Bitte die Datenbank noch einmal öffnen.", file=sys.stderr)
            sys.exit(4)
        if reason:
            print(f"{modules.missing_message(missing)}\n\nVom Server ließ es sich nicht laden: {reason}", file=sys.stderr)
            sys.exit(3)
        print(modules.missing_message(missing), file=sys.stderr)
        sys.exit(3)
    call_command("migrate", interactive=False, verbosity=0)
    migrate_ms = (time.perf_counter() - STARTED) * 1000 - setup_ms
    task = os.environ.get("SIDECAR_TASK", "serve")
    if task in TASKS and not _run_task(task, stdin, writer, db_dir):
        sys.exit(1)
    app = get_wsgi_application()
    # Loads the URLs, views and templates now, so the first click is as fast as every other.
    _handle(app, Writer(open(os.devnull, "wb")), {"id": 0, "method": "GET", "path": "/anmelden/"}, b"")

    if os.environ.get("SYNC_TOKEN"):
        from sync import client

        def wipe():
            writer.send({"wipe": True})  # the shell removes this database from the computer

        client.WORKER = client.Worker(os.environ["SYNC_TOKEN"], on_revoked=wipe)
        client.WORKER.start()

    from archive import plan

    if plan.applies():
        plan.Scheduler().start()  # the regular backup and the nightly work, while this database is open

    from django.db import connection

    connection.close()  # Each worker thread opens its own.
    writer.send({"ready": True, "setup_ms": round(setup_ms), "migrate_ms": round(migrate_ms),
                 "ready_ms": round((time.perf_counter() - STARTED) * 1000)})

    def run(header, body):
        try:
            _handle(app, writer, header, body)
        finally:
            from django.db import close_old_connections

            close_old_connections()

    def open_connection():
        """Each worker opens its connection once, before its first request, and keeps it."""
        from django.db import connection as own

        own.ensure_connection()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="request", initializer=open_connection) as pool:
        while True:
            try:
                header, body = read_frame(stdin)
            except EOFError:
                break  # The shell closed the pipe: the window is gone.
            pool.submit(run, header, body)


if __name__ == "__main__":
    main()
