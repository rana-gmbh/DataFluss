"""Update button: asks Coolify to deploy the newest version (a release, or the newest commit: UPDATE_SOURCE).

The app holds a Coolify token limited to `read` and `deploy`, plus an optional read-only GitHub
token to list what is new. Build logs stay in Coolify: reading them needs `read:sensitive`. It has no Docker access. Coolify builds the new version and only replaces the
running one after the new container passes its health check.
"""
import contextlib
import gzip
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_noop

from archive.container import BLOCK, ContainerError, Reader, Writer
from vault import crypto

from . import audit
from .models import UpdateState

log = logging.getLogger(__name__)
CHECK_EVERY = timedelta(hours=1)
KEEP_BACKUPS = 5
RUNNING = {"queued", "in_progress"}
FOLLOWED = RUNNING | {"unknown"}
TIMEOUT = 15
DUMP_EXTENSION = ".sql.gz.enc"
TOOL_TIMEOUT = 300
# An older dump would take back real work, so from here on IT decides.
REPAIR_WINDOW = timedelta(minutes=15)
GIVE_UP = timedelta(hours=1)
WATCH_EVERY = 10
# Removes what newer migrations added too, which a list of the dump's own tables would leave behind.
CLEAR_TABLES = (
    b"BEGIN;\nDO $$ DECLARE t text; BEGIN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP "
    b"EXECUTE format('DROP TABLE public.%I CASCADE', t); END LOOP; END $$;\n"
)
# Written last, because psql commits an open --single-transaction at end of input, however the input ended.
COMMIT = b"COMMIT;\n"


class UpdateError(Exception):
    pass


def enabled():
    return bool(settings.COOLIFY_API_URL and settings.COOLIFY_API_TOKEN and settings.COOLIFY_APP_UUID)


def _request(url, token="", method="GET"):
    headers = {"Accept": "application/json", "User-Agent": "schulverwaltung"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def _coolify(path, method="GET"):
    return _request(f"{settings.COOLIFY_API_URL}/api/v1/{path}", settings.COOLIFY_API_TOKEN, method)


def _short(commit):
    data = commit.get("commit", {})
    return {
        "sha": commit.get("sha", "")[:7],
        "subject": data.get("message", "").split("\n")[0][:200],
        "date": data.get("committer", {}).get("date", ""),
    }


def running_version():
    """What the update button compares and reports: the DataFluss version, or the commit (UPDATE_SOURCE)."""
    return settings.DATAFLUSS_VERSION if settings.UPDATE_SOURCE == "releases" else settings.APP_VERSION


def _version_key(text):
    try:
        return tuple(int(part) for part in text.lstrip("v").split("."))
    except ValueError:
        return ()


def _check_releases(state, base):
    """The latest release against the running DataFluss version."""
    release = _request(f"{base}/releases/latest", settings.GITHUB_TOKEN)
    version = (release.get("tag_name") or "").lstrip("v")
    state.latest = {"sha": version, "subject": release.get("name") or f"DataFluss {version}", "date": release.get("published_at") or ""}
    state.check_error = ""
    newer = _version_key(version) > _version_key(settings.DATAFLUSS_VERSION)
    state.behind, state.commits = (1, [state.latest]) if newer else (0, [])


def check_for_update():
    """Ask GitHub what is new. Without a token only public repositories can be read."""
    state = UpdateState.load()
    repo, branch = settings.GITHUB_REPOSITORY, settings.GITHUB_BRANCH
    base = f"https://api.github.com/repos/{repo}"
    try:
        if settings.UPDATE_SOURCE == "releases":
            _check_releases(state, base)
            state.checked_at = timezone.now()
            state.save()
            return state
        head = _request(f"{base}/commits/{urllib.parse.quote(branch)}", settings.GITHUB_TOKEN)
        latest = _short(head)
        state.latest, state.check_error = latest, ""
        if latest["sha"] == settings.APP_VERSION:
            state.behind, state.commits = 0, []
        else:
            compare = _request(f"{base}/compare/{settings.APP_VERSION}...{urllib.parse.quote(branch)}", settings.GITHUB_TOKEN)
            state.behind = compare.get("ahead_by", 0)
            state.commits = [_short(c) for c in reversed(compare.get("commits", []))][:20]
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and state.latest:
            # The running version is unknown to GitHub, for example after a force push.
            state.behind, state.commits = 1, []
        else:
            state.check_error = gettext_noop("GitHub-Zugriff wurde abgelehnt. Bitte GITHUB_TOKEN prüfen.") if exc.code in (401, 403, 404) else gettext_noop("GitHub antwortet nicht.")
    except (OSError, ValueError):
        state.check_error = gettext_noop("GitHub ist nicht erreichbar.")
    state.checked_at = timezone.now()
    state.save()
    return state


def _check_in_background():
    try:
        check_for_update()
    except Exception:
        log.exception("Update check failed")
    finally:
        connection.close()


def update_available(background_check=False):
    if not enabled():
        return False
    state = UpdateState.load()
    stale = state.checked_at is None or timezone.now() - state.checked_at > CHECK_EVERY
    if background_check and stale:
        threading.Thread(target=_check_in_background, daemon=True).start()
    return state.behind > 0


def _stuck(state):
    return bool(state.started_at) and timezone.now() - state.started_at > GIVE_UP


def is_running():
    return UpdateState.load().deployment_status in RUNNING


def blocks_restore():
    """A deployment that has run for this long must not stand in the way of a backup."""
    state = UpdateState.load()
    return state.deployment_status in RUNNING and not _stuck(state)


def _tool_env():
    return {"PGPASSWORD": settings.DATABASES["default"]["PASSWORD"], "PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def _connection_options():
    db = settings.DATABASES["default"]
    return ["-h", db["HOST"], "-p", db["PORT"] or "5432", "-U", db["USER"]]


def _dump_password():
    return crypto.derived_password("database-dump")


def _finish(process, errors, command):
    code = process.wait()
    if code:
        errors.seek(0)
        raise subprocess.CalledProcessError(code, command[0], stderr=errors.read())


def _write_dump(command, target):
    """Encrypted on its way to the disk, so the volume never holds the database in plain text."""
    with tempfile.TemporaryFile() as errors, target.open("wb") as raw:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, env=_tool_env())
        timer = threading.Timer(TOOL_TIMEOUT, process.kill)
        timer.start()
        try:
            sink = Writer(raw, _dump_password())
            with gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as packed:
                shutil.copyfileobj(process.stdout, packed, BLOCK)
            sink.close()
        finally:
            timer.cancel()
            process.stdout.close()
        _finish(process, errors, command)


def _open_dump(raw):
    return Reader(raw, _dump_password())


def _prove_dump(target):
    """A dump that cannot be read back must not be the reason anyone feels safe."""
    with target.open("rb") as raw:
        reader = _open_dump(raw)
        with gzip.GzipFile(fileobj=reader, mode="rb") as packed:
            while packed.read(BLOCK):
                pass
        reader.verify_end()


def backup_database(prefix="pre-update"):
    """Dump before a change that cannot be undone otherwise. Returns the file name."""
    db = settings.DATABASES["default"]
    if "postgresql" not in db["ENGINE"]:
        return ""
    root = Path(settings.BACKUP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}-{settings.APP_VERSION}-{timezone.localtime():%Y%m%d-%H%M%S}{DUMP_EXTENSION}"
    target = root / name
    partial = root / f"{name}.part"
    command = ["pg_dump", "--no-owner", "--no-privileges", *_connection_options(), db["NAME"]]
    try:
        _write_dump(command, partial)
        partial.chmod(0o600)
        _prove_dump(partial)
        partial.replace(target)
    except (OSError, EOFError, subprocess.SubprocessError, ContainerError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        log.error("Backup failed: %s", detail.decode(errors="replace")[:300] or exc)
        raise UpdateError(_("Die Datenbank konnte nicht gesichert werden. Es wurde nichts verändert."))
    finally:
        partial.unlink(missing_ok=True)
    for old in sorted(root.glob(f"{prefix}-*{DUMP_EXTENSION}"), key=lambda f: f.stat().st_mtime, reverse=True)[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)
    for unencrypted in root.glob("pre-update-*.dump"):  # Written by earlier versions.
        unencrypted.unlink(missing_ok=True)
    return name


def start_update():
    backup = backup_database()
    query = urllib.parse.urlencode({"uuid": settings.COOLIFY_APP_UUID})
    try:
        result = _coolify(f"deploy?{query}", "POST")
        deployment = result["deployments"][0]["deployment_uuid"]
    except urllib.error.HTTPError as exc:
        reason = _("Coolify lehnt das Token ab.") if exc.code in (401, 403) else _("Coolify meldet Fehler %(code)s.") % {"code": exc.code}
        raise UpdateError(reason)
    except urllib.error.URLError:
        raise UpdateError(_("Coolify ist nicht erreichbar. Es wurde nichts verändert."))
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # The request may have arrived, so nothing can be promised here.
        raise UpdateError(_("Coolify hat unerwartet geantwortet. Bitte dort unter Deployments prüfen, ob die Aktualisierung gestartet ist."))
    state = UpdateState.load()
    state.deployment_uuid, state.deployment_status = deployment, "queued"
    state.started_at, state.from_version, state.backup_name = timezone.now(), running_version(), backup
    state.deployment_url, state.restored = "", ""
    state.save()
    _watch_in_background()


def unknown_migrations():
    """Migrations in the database that this version does not have.

    Coolify keeps the old container when the new one fails, but the new one may already have
    changed the database by then. These entries show that it did.
    """
    known = MigrationLoader(None).disk_migrations
    return [key for key in MigrationRecorder(connection).applied_migrations() if key not in known]


def restore_database(name):
    """Replaces every table in one transaction, which only a complete dump gets to commit."""
    db = settings.DATABASES["default"]
    command = ["psql", "--no-psqlrc", "--quiet", "-v", "ON_ERROR_STOP=1", *_connection_options(), "-d", db["NAME"], "-f", "-"]
    with tempfile.TemporaryFile() as errors, (Path(settings.BACKUP_ROOT) / name).open("rb") as raw:
        reader = _open_dump(raw)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, env=_tool_env())
        timer = threading.Timer(TOOL_TIMEOUT, process.kill)
        timer.start()
        try:
            process.stdin.write(CLEAR_TABLES)
            with gzip.GzipFile(fileobj=reader, mode="rb") as packed:
                shutil.copyfileobj(packed, process.stdin, BLOCK)
            reader.verify_end()
            process.stdin.write(COMMIT)
            process.stdin.close()
        except BrokenPipeError:
            with contextlib.suppress(BrokenPipeError):
                process.stdin.close()  # psql stopped by itself, and _finish says why.
        except BaseException:
            process.kill()
            raise
        finally:
            timer.cancel()
        _finish(process, errors, command)
    from sync.models import Dataset

    Dataset.renew()  # the dump rewound the data, so connected computers must stop syncing (sync/models.py)


def _restore(state):
    saved = {f.name: getattr(state, f.name) for f in UpdateState._meta.concrete_fields if f.name != "id"}
    try:
        restore_database(state.backup_name)
    except (OSError, EOFError, subprocess.SubprocessError, ContainerError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        log.error("Restore failed: %s", detail.decode(errors="replace")[:300] or exc)
        return "failed"
    # The restored table is older than this update, so its progress is written back.
    UpdateState.objects.update_or_create(pk=1, defaults={**saved, "restored": "ok"})
    audit.log("update", f"Datenbank nach fehlgeschlagener Aktualisierung wiederhergestellt: {state.backup_name}")
    return "ok"


def _repair_after_failure(state):
    """Called once, by whoever saw the failed deployment first."""
    if not unknown_migrations():
        return ""
    if not state.backup_name:
        return "failed"
    if timezone.now() - state.started_at > REPAIR_WINDOW:
        return "waiting"
    UpdateState.objects.filter(pk=1).update(restored="working")
    return _restore(state)


def restore_on_request():
    """IT's answer once a failure is too old for an automatic restore."""
    if not UpdateState.objects.filter(pk=1, restored="waiting").update(restored="working"):
        raise UpdateError(_("Es gibt nichts zurückzusetzen."))
    state = UpdateState.load()
    result = _restore(state) if unknown_migrations() else ""
    UpdateState.objects.filter(pk=1).update(restored=result)


def _give_up(state):
    """Coolify no longer answers for this deployment, so nothing may wait for it."""
    if UpdateState.objects.filter(pk=1, deployment_status=state.deployment_status).update(deployment_status="unknown"):
        if state.backup_name and unknown_migrations():
            UpdateState.objects.filter(pk=1).update(restored="waiting")
    return UpdateState.load()


def refresh_deployment():
    state = UpdateState.load()
    if not state.deployment_uuid or state.deployment_status not in FOLLOWED:
        return state
    try:
        data = _coolify(f"deployments/{state.deployment_uuid}")
    except (OSError, ValueError):
        # Normal while the containers are switched over.
        return _give_up(state) if state.deployment_status in RUNNING and _stuck(state) else state
    status = data.get("status", state.deployment_status)
    path = data.get("deployment_url") or ""
    # Several workers can poll at once. Only the one that makes this change goes on.
    changed = UpdateState.objects.filter(pk=1, deployment_status=state.deployment_status).update(deployment_status=status, deployment_url=path)
    if state.deployment_status == "unknown":
        UpdateState.objects.filter(pk=1, restored="waiting").update(restored="")  # Coolify answers again and decides.
    if not changed or status in RUNNING:
        return UpdateState.load()
    state.refresh_from_db()
    if status != "finished":
        restored = _repair_after_failure(state)
        if restored:
            UpdateState.objects.filter(pk=1).update(restored=restored)
    check_for_update()
    return UpdateState.load()


def _watch():
    """Follows the deployment, so a failure is handled in time even with no page open."""
    try:
        while refresh_deployment().deployment_status in RUNNING:
            time.sleep(WATCH_EVERY)
    except Exception:
        log.exception("Following the deployment failed")
    finally:
        connection.close()


def _watch_in_background():
    threading.Thread(target=_watch, daemon=True).start()


def _log_link(path):
    """The API address may be internal, so links for the browser need their own address."""
    if settings.COOLIFY_DASHBOARD_URL and path.startswith("/"):
        return f"{settings.COOLIFY_DASHBOARD_URL}{path}"
    return ""


def _when(moment):
    moment = timezone.localtime(moment)
    return _("%(date)s, %(time)s Uhr") % {"date": moment.strftime("%d.%m.%Y"), "time": moment.strftime("%H:%M")}


def public_status():
    state = UpdateState.load()
    finished = state.deployment_status and state.deployment_status not in RUNNING
    result, message = "", ""
    if state.deployment_status == "unknown":
        result, message = "failed", _("Coolify gibt keine Auskunft über diese Aktualisierung. Bitte dort unter Deployments nachsehen.")
    elif finished and state.deployment_status == "finished":
        if state.from_version != running_version():
            result, message = "ok", _("Aktualisierung auf %(version)s abgeschlossen.") % {"version": running_version()}
        else:
            result, message = "failed", _("Coolify meldet Erfolg, aber es läuft noch die bisherige Version. Bitte das Protokoll in Coolify prüfen.")
    elif finished:
        result, message = "failed", _("Die neue Version wurde nicht übernommen. Die bisherige Version läuft weiter.")
    if state.restored == "ok":
        message += " " + _("Die Datenbank wurde auf den Stand vor der Aktualisierung zurückgesetzt.")
    elif state.restored == "failed":
        message += " " + _("Die Datenbank konnte nicht zurückgesetzt werden. Bitte die Sicherung %(name)s von Hand einspielen.") % {"name": state.backup_name}
    elif state.restored == "working":
        message += " " + _("Die Datenbank wird gerade zurückgesetzt. Steht das nach einigen Minuten noch hier, bitte die Sicherung %(name)s von Hand einspielen.") % {"name": state.backup_name}
    can_restore = state.restored == "waiting"
    if can_restore:
        message += " " + _("Die neue Version hat die Datenbank bereits verändert. Sie kann auf den Stand von %(when)s zurückgesetzt werden. Alles, was seitdem gespeichert wurde, geht dabei verloren.") % {"when": _when(state.started_at)}
    return {
        "enabled": enabled(),
        "app_version": running_version(),
        "latest": state.latest,
        "behind": state.behind,
        "commits": state.commits,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
        "check_error": _(state.check_error) if state.check_error else "",
        "state": "running" if state.deployment_status in RUNNING else "idle",
        "step": state.deployment_status,
        "result": result,
        "message": message,
        "can_restore": can_restore,
        "restore_to": _when(state.started_at) if can_restore else "",
        "deployment_url": _log_link(state.deployment_url),
    }
