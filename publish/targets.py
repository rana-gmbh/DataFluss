"""Where a publication goes (docs/publish.md "Ziele"), with Python's standard library only: the desktop
app's sidecar brings nothing else.

Every target but the ZIP is brought up to date with `send`: it uploads what is new or changed (pictures and
files first, the pages after them, the start page last, so no page ever points to a missing file), writes
the manifest, and then deletes what the previous publish put there and is no longer part of the site.
Only paths of its own manifest are ever deleted, never anything else at the target.
"""
import ftplib
import io
import json
import posixpath
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from base64 import b64encode
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.translation import gettext as _

from vault import crypto

from .models import Publication
from .render import MANIFEST, manifest_bytes

SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")
TIMEOUT = 60


def safe(path):
    """A path of the site: relative, no "..", nothing but plain characters."""
    if not SAFE_PATH.match(path or "") or ".." in path.split("/") or "//" in path:
        raise ValidationError(_("Ungültiger Pfad: %(path)s") % {"path": path})
    return path


def password(publication):
    try:
        return crypto.decrypt(publication.target_secret, f"publish:{publication.pk}") if publication.target_secret else ""
    except Exception:  # noqa: BLE001 - a restored backup made with another vault key
        return ""


def set_password(publication, plain):
    publication.target_secret = crypto.encrypt(plain, f"publish:{publication.pk}") if plain else ""


def server_folder(publication):
    return f"published/{publication.slug}"


# ---------------------------------------------------------------- targets


class Target:
    """put / delete / read one file of the site; `start` and `close` around a publish."""

    def start(self):
        pass

    def close(self):
        pass

    def read(self, path):
        raise NotImplementedError

    def put(self, path, data):
        raise NotImplementedError

    def delete(self, path):
        raise NotImplementedError

    def test(self):
        """Raise ValidationError when the target cannot be reached or written."""
        self.start()
        try:
            self.put(".datafluss-test", b"ok")
            self.delete(".datafluss-test")
        finally:
            self.close()


class FolderTarget(Target):
    """A folder on this computer (the desktop app picks it)."""

    def __init__(self, folder):
        self.root = Path(folder or "").expanduser()

    def start(self):
        if not str(self.root) or not self.root.is_absolute() or not self.root.is_dir():
            raise ValidationError(_("Den Ordner gibt es nicht: %(folder)s") % {"folder": self.root})

    def _file(self, path):
        target = (self.root / safe(path)).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValidationError(_("Ungültiger Pfad: %(path)s") % {"path": path})
        return target

    def read(self, path):
        target = self._file(path)
        return target.read_bytes() if target.is_file() else None

    def put(self, path, data):
        target = self._file(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        part.replace(target)

    def delete(self, path):
        target = self._file(path)
        if target.is_file():
            target.unlink()
        parent = target.parent
        while parent != self.root.resolve() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


class StorageTarget(Target):
    """A folder of the database's own storage (sealed on a computer): a preview, or this server's /p/<slug>/."""

    def __init__(self, folder):
        self.folder = folder

    def _name(self, path):
        return f"{self.folder}/{safe(path)}"

    def read(self, path):
        name = self._name(path)
        if not default_storage.exists(name):
            return None
        with default_storage.open(name, "rb") as handle:
            return handle.read()

    def put(self, path, data):
        name = self._name(path)
        if default_storage.exists(name):
            default_storage.delete(name)
        default_storage.save(name, ContentFile(data))

    def delete(self, path):
        default_storage.delete(self._name(path))

    def remove_all(self, files):
        """Everything a manifest lists, and the manifest."""
        for path in [*(files or {}), MANIFEST]:
            try:
                self.delete(path)
            except ValidationError:
                continue


class ServerTarget(StorageTarget):
    """This server itself: /p/<slug>/ (publish/views.py site_file)."""

    def __init__(self, publication):
        super().__init__(server_folder(publication))


class WebDavTarget(Target):
    """A folder on a web space reached by WebDAV (Nextcloud, many hosting packages)."""

    def __init__(self, url, user, secret):
        url = (url or "").strip()
        parts = urllib.parse.urlsplit(url)
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValidationError(_("Bitte eine Adresse mit https:// angeben."))
        self.base = url.rstrip("/") + "/"
        self.headers = {"User-Agent": "DataFluss"}
        if user:
            self.headers["Authorization"] = "Basic " + b64encode(f"{user}:{secret}".encode()).decode()
        self.made = set()

    def _url(self, path):
        return self.base + urllib.parse.quote(safe(path))

    def _call(self, method, url, data=None, headers=None, ok=(200, 201, 204, 207)):
        request = urllib.request.Request(url, data=data, method=method, headers={**self.headers, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=_tls()) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in ok:
                return exc.code, b""
            if exc.code in (401, 403):
                raise ValidationError(_("WebDAV: Anmeldung abgelehnt (%(code)s).") % {"code": exc.code})
            return exc.code, b""
        except (urllib.error.URLError, OSError) as exc:
            raise ValidationError(_("WebDAV nicht erreichbar: %(error)s") % {"error": getattr(exc, "reason", exc)})

    def start(self):
        status, _body = self._call("PROPFIND", self.base, headers={"Depth": "0"})
        if status not in (200, 207):
            raise ValidationError(_("WebDAV: Den Ordner gibt es nicht oder er ist nicht erreichbar (%(code)s).") % {"code": status})

    def _folders(self, path):
        parts = path.split("/")[:-1]
        for n in range(1, len(parts) + 1):
            folder = "/".join(parts[:n])
            if folder not in self.made:
                status, _body = self._call("MKCOL", self._url(folder) + "/", ok=(201, 405))
                if status not in (201, 405, 200):
                    raise ValidationError(_("WebDAV: Ordner %(folder)s konnte nicht angelegt werden (%(code)s).") % {"folder": folder, "code": status})
                self.made.add(folder)

    def read(self, path):
        status, body = self._call("GET", self._url(path), ok=(200,))
        return body if status == 200 else None

    def put(self, path, data):
        self._folders(path)
        status, _body = self._call("PUT", self._url(path), data=data, headers={"Content-Type": "application/octet-stream"})
        if status not in (200, 201, 204):
            raise ValidationError(_("WebDAV: %(path)s konnte nicht gespeichert werden (%(code)s).") % {"path": path, "code": status})

    def delete(self, path):
        status, _body = self._call("DELETE", self._url(path), ok=(200, 204, 404))
        if status not in (200, 204, 404):
            raise ValidationError(_("WebDAV: %(path)s konnte nicht gelöscht werden (%(code)s).") % {"path": path, "code": status})


class FtpTarget(Target):
    """A folder on a web space reached by FTP, with TLS (FTPS) unless switched off on purpose."""

    def __init__(self, host, port, user, secret, folder, tls=True):
        self.host, self.port = (host or "").strip(), int(port or 21)
        self.user, self.secret, self.tls = user or "anonymous", secret, tls
        self.folder = "/" + (folder or "").strip().strip("/")
        if not self.host:
            raise ValidationError(_("Bitte den Server angeben."))
        self.ftp = None
        self.made = set()

    def start(self):
        try:
            self.ftp = ftplib.FTP_TLS(context=_tls(), timeout=TIMEOUT) if self.tls else ftplib.FTP(timeout=TIMEOUT)
            self.ftp.connect(self.host, self.port)
            self.ftp.login(self.user, self.secret)
            if self.tls:
                self.ftp.prot_p()
            self.ftp.cwd(self.folder)
        except ftplib.error_perm as exc:
            self.close()
            raise ValidationError(_("FTP: %(error)s") % {"error": exc})
        except (OSError, ftplib.Error, EOFError) as exc:
            self.close()
            raise ValidationError(_("FTP nicht erreichbar: %(error)s") % {"error": exc})

    def close(self):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except (OSError, ftplib.Error, EOFError):
                self.ftp.close()
            self.ftp = None

    def _path(self, path):
        return posixpath.join(self.folder, safe(path))

    def _folders(self, path):
        parts = path.split("/")[:-1]
        for n in range(1, len(parts) + 1):
            folder = "/".join(parts[:n])
            if folder not in self.made:
                try:
                    self.ftp.mkd(self._path(folder))
                except ftplib.error_perm:
                    pass  # it is there already
                self.made.add(folder)

    def read(self, path):
        out = io.BytesIO()
        try:
            self.ftp.retrbinary(f"RETR {self._path(path)}", out.write)
        except ftplib.error_perm:
            return None
        return out.getvalue()

    def put(self, path, data):
        self._folders(path)
        try:
            self.ftp.storbinary(f"STOR {self._path(path)}", io.BytesIO(data))
        except ftplib.Error as exc:
            raise ValidationError(_("FTP: %(path)s konnte nicht gespeichert werden (%(error)s).") % {"path": path, "error": exc})

    def delete(self, path):
        try:
            self.ftp.delete(self._path(path))
        except ftplib.error_perm:
            pass  # gone already


def _tls():
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def target_for(publication):
    config = publication.target_config or {}
    kind = publication.target
    if kind == Publication.FOLDER:
        return FolderTarget(config.get("path"))
    if kind == Publication.SERVER:
        return ServerTarget(publication)
    if kind == Publication.WEBDAV:
        return WebDavTarget(config.get("url"), config.get("user"), password(publication))
    if kind == Publication.FTP:
        return FtpTarget(config.get("host"), config.get("port"), config.get("user"), password(publication), config.get("path"),
                         tls=config.get("tls", True) is not False)
    raise ValidationError(_("Dieses Ziel lädt nichts hoch."))


# ---------------------------------------------------------------- publishing


def _order(path):
    """Files first, the pages after them, the start page last."""
    if path == "index.html":
        return (3, path)
    if path.endswith(".html"):
        return (2, path)
    return (1, path)


def fingerprint(publication):
    """Which target the stored manifest belongs to: a new target (or folder, address, server) starts afresh."""
    import hashlib

    config = {k: v for k, v in sorted((publication.target_config or {}).items())}
    return hashlib.sha256(json.dumps([publication.target, config, publication.slug], sort_keys=True).encode()).hexdigest()[:16]


def previous(publication, target):
    """{path: sha256} of what the last publish left at this target: the publication's own record when it is of
    this target, else the manifest found there."""
    stored = publication.manifest or {}
    if stored.get("target") == fingerprint(publication):
        return dict(stored.get("files") or {})
    return manifest_at(target)


def manifest_at(target):
    """{path: sha256} of the manifest a target holds, or {}."""
    try:
        data = target.read(MANIFEST)
        files = json.loads(data or b"{}").get("files") or {}
    except (ValueError, ValidationError, AttributeError):
        return {}
    return {p: h for p, h in files.items() if isinstance(p, str) and isinstance(h, str) and SAFE_PATH.match(p)}


def send(publication, site, target, progress=None, old=None):
    """Bring the target up to date with `site`; returns (uploaded, deleted). `old` is what is there now
    (default: what this publication last put there)."""
    say = progress or (lambda text: None)
    target.start()
    try:
        old = previous(publication, target) if old is None else old
        new = site.files
        changed = sorted((p for p in new if old.get(p) != new[p]), key=_order)
        for n, path in enumerate(changed, 1):
            if n % 20 == 0:
                say(_("%(n)s von %(total)s Dateien übertragen") % {"n": n, "total": len(changed)})
            target.put(path, site.read(path))
        target.put(MANIFEST, manifest_bytes(site))
        gone = sorted(p for p in old if p not in new and p != MANIFEST)
        for path in gone:
            try:
                target.delete(safe(path))
            except ValidationError:
                continue
        return len(changed), len(gone)
    finally:
        target.close()


def zip_file(site):
    """The whole site as a ZIP, in a file that stays small in memory until it is sent."""
    from tempfile import SpooledTemporaryFile

    out = SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(site.files):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if path.startswith("_media/") else zipfile.ZIP_DEFLATED
            archive.writestr(info, site.read(path))
        archive.writestr(zipfile.ZipInfo(MANIFEST, date_time=(2026, 1, 1, 0, 0, 0)), manifest_bytes(site))
    out.seek(0)
    return out
