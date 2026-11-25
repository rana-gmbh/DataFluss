"""Targets that are services on the web (docs/publish.md "Ziele"): S3-compatible storage, Netlify, GitHub Pages,
and a DataFluss hosting (another DataFluss server, or seiten.datafluss.de; publish/hosting.py). Python's
standard library only, like publish/targets.py: the desktop app's sidecar brings nothing else.

S3 and DataFluss hosting take file by file. Netlify and GitHub take one whole state at a time, so they have
`deploy(site, changed, gone, manifest)`, which publish/targets.py send() calls instead of its loop.
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .targets import SAFE_PATH, TIMEOUT, Target, _tls, safe

TYPES = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8",
         "svg": "image/svg+xml", "webp": "image/webp", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8",
         "json": "application/json"}


def content_type(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def cache_control(path):
    return "public, max-age=31536000, immutable" if path.startswith("_media/") else "public, no-cache"


def _http(method, url, data=None, headers=None, ok=(200, 201, 204), name="HTTP"):
    """(status, body); a status outside `ok` raises ValidationError with the service's answer."""
    request = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "DataFluss", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_tls() if url.startswith("https") else None) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if exc.code in ok:
            return exc.code, body
        if exc.code in (401, 403):
            raise ValidationError(_("%(name)s: Zugang abgelehnt (%(code)s). Bitte Token oder Schlüssel prüfen.") % {"name": name, "code": exc.code})
        detail = body[:200].decode("utf-8", "replace").strip()
        raise ValidationError(_("%(name)s antwortet mit %(code)s: %(detail)s") % {"name": name, "code": exc.code, "detail": detail})
    except (urllib.error.URLError, OSError) as exc:
        raise ValidationError(_("%(name)s nicht erreichbar: %(error)s") % {"name": name, "error": getattr(exc, "reason", exc)})


def _https_only(url, name):
    parts = urllib.parse.urlsplit(url or "")
    local = parts.hostname in ("localhost", "127.0.0.1", "::1") or (parts.hostname or "").endswith(".localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValidationError(_("%(name)s: Bitte eine Adresse mit https:// angeben.") % {"name": name})
    return url.rstrip("/")


# ---------------------------------------------------------------- S3

def sigv4(method, host, uri, query, headers, payload_hash, key_id, secret, region, amz_date, service="s3"):
    """The Authorization header of AWS Signature Version 4 (all headers given are signed; names in lower case)."""
    day = amz_date[:8]
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{str(headers[name]).strip()}\n" for name in sorted(headers))
    request = "\n".join([method, uri, query, canonical_headers, signed, payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = f"AWS4{secret}".encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"


class S3Target(Target):
    """A bucket of AWS S3 or a compatible store (Hetzner, Cloudflare R2, MinIO …), addressed path-style:
    https://<endpoint>/<bucket>/<prefix><path>. Serving it as a website is set up at the provider."""

    def __init__(self, endpoint, region, bucket, prefix, key_id, secret):
        self.endpoint = _https_only(endpoint, "S3")
        self.region = (region or "us-east-1").strip()
        self.bucket = (bucket or "").strip()
        self.prefix = (prefix or "").strip().strip("/")
        self.key_id, self.secret = (key_id or "").strip(), secret or ""
        if not self.bucket or not self.key_id or not self.secret:
            raise ValidationError(_("S3: Bitte Bucket, Zugriffsschlüssel und Geheimnis angeben."))

    def _key(self, path):
        return f"{self.prefix}/{safe(path)}" if self.prefix else safe(path)

    def _call(self, method, key="", data=b"", extra=None, ok=(200, 201, 204)):
        parts = urllib.parse.urlsplit(self.endpoint)
        uri = urllib.parse.quote(f"{parts.path}/{self.bucket}/{key}" if key else f"{parts.path}/{self.bucket}", safe="/-_.~")
        amz_date = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload = hashlib.sha256(data or b"").hexdigest()
        headers = {"host": parts.netloc, "x-amz-content-sha256": payload, "x-amz-date": amz_date, **(extra or {})}
        headers["authorization"] = sigv4(method, parts.netloc, uri, "", {k: v for k, v in headers.items() if k != "authorization"},
                                         payload, self.key_id, self.secret, self.region, amz_date)
        return _http(method, f"{parts.scheme}://{parts.netloc}{uri}", data=data if method == "PUT" else None,
                     headers={k: v for k, v in headers.items() if k != "host"}, ok=ok, name="S3")

    def start(self):
        status, _body = self._call("HEAD", ok=(200, 404))
        if status == 404:
            raise ValidationError(_("S3: Den Bucket %(bucket)s gibt es nicht.") % {"bucket": self.bucket})

    def read(self, path):
        status, body = self._call("GET", self._key(path), ok=(200, 404))
        return body if status == 200 else None

    def put(self, path, data):
        self._call("PUT", self._key(path), data, {"content-type": content_type(path), "cache-control": cache_control(path)})

    def delete(self, path):
        self._call("DELETE", self._key(path), ok=(200, 204, 404))


# ---------------------------------------------------------------- Netlify

class NetlifyTarget(Target):
    """A site at Netlify, with a personal access token. Each publish is a deploy of every file with its SHA-1;
    Netlify asks only for the files it does not have yet."""

    API = "https://api.netlify.com/api/v1"

    def __init__(self, site_id, token):
        self.site_id = (site_id or "").strip()
        self.token = token or ""
        if not self.site_id or not self.token:
            raise ValidationError(_("Netlify: Bitte Site-ID und Token angeben."))

    def _call(self, method, path, body=None, raw=None, ok=(200, 201, 204)):
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        status, answer = _http(method, f"{self.API}{path}", data=data, headers=headers, ok=ok, name="Netlify")
        return json.loads(answer) if answer and answer[:1] in (b"{", b"[") else {}

    def start(self):
        self._call("GET", f"/sites/{urllib.parse.quote(self.site_id)}")

    def read(self, path):
        return None  # Netlify keeps what it has itself: unchanged files are never sent again

    def test(self):
        self.start()  # a deploy would replace the live site

    def deploy(self, site, changed, gone, manifest):
        files = {path: site.read(path) for path in site.files}
        files["datafluss-manifest.json"] = manifest
        digests = {f"/{path}": hashlib.sha1(data).hexdigest() for path, data in files.items()}
        answer = self._call("POST", f"/sites/{urllib.parse.quote(self.site_id)}/deploys", body={"files": digests})
        required = set(answer.get("required") or [])
        for path, data in files.items():
            if digests[f"/{path}"] in required:
                self._call("PUT", f"/deploys/{answer['id']}/files/{urllib.parse.quote(path)}", raw=data)


# ---------------------------------------------------------------- GitHub Pages

class GitHubTarget(Target):
    """A branch of a GitHub repository that GitHub Pages serves. Each publish is one commit with the changed
    files and the removed ones; `.nojekyll` keeps GitHub from leaving out the folders that start with "_"."""

    API = "https://api.github.com"

    def __init__(self, repo, branch, folder, token):
        self.repo = (repo or "").strip().strip("/")
        if not SAFE_PATH.match(self.repo) or self.repo.count("/") != 1:
            raise ValidationError(_("GitHub: Bitte das Repository als „Besitzer/Name“ angeben."))
        self.branch = (branch or "gh-pages").strip() or "gh-pages"
        self.folder = (folder or "").strip().strip("/")
        if self.folder:
            safe(self.folder)
        self.token = token or ""
        if not self.token:
            raise ValidationError(_("GitHub: Bitte einen Token angeben."))
        self.head = None

    def _path(self, path):
        return f"{self.folder}/{path}" if self.folder else path

    def _call(self, method, path, body=None, ok=(200, 201), accept="application/vnd.github+json"):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        return _http(method, f"{self.API}/repos/{self.repo}{path}", data=data, headers=headers, ok=ok, name="GitHub")

    def start(self):
        status, answer = self._call("GET", "")
        if not json.loads(answer).get("permissions", {}).get("push", True):
            raise ValidationError(_("GitHub: Der Token darf in %(repo)s nicht schreiben.") % {"repo": self.repo})
        status, answer = self._call("GET", f"/git/ref/heads/{urllib.parse.quote(self.branch)}", ok=(200, 404))
        self.head = json.loads(answer)["object"]["sha"] if status == 200 else None

    def read(self, path):
        if self.head is None:
            return None
        status, body = self._call("GET", f"/contents/{urllib.parse.quote(self._path(safe(path)))}?ref={urllib.parse.quote(self.branch)}",
                                  ok=(200, 404), accept="application/vnd.github.raw")
        return body if status == 200 else None

    def deploy(self, site, changed, gone, manifest):
        if not changed and not gone and self.head is not None:
            return
        files = {path: site.read(path) for path in changed}
        files["datafluss-manifest.json"] = manifest
        files[".nojekyll"] = b""
        self.deploy_files(files, gone)

    def deploy_files(self, files, gone):
        entries = []
        for path, data in files.items():
            _status, answer = self._call("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            entries.append({"path": self._path(path), "mode": "100644", "type": "blob", "sha": json.loads(answer)["sha"]})
        if self.head is not None:
            entries += [{"path": self._path(safe(path)), "mode": "100644", "type": "blob", "sha": None} for path in gone]
        tree = {"tree": entries}
        if self.head is not None:
            _status, answer = self._call("GET", f"/git/commits/{self.head}")
            tree["base_tree"] = json.loads(answer)["tree"]["sha"]
        _status, answer = self._call("POST", "/git/trees", tree)
        commit = {"message": "DataFluss: Website aktualisiert", "tree": json.loads(answer)["sha"], "parents": [self.head] if self.head else []}
        _status, answer = self._call("POST", "/git/commits", commit)
        sha = json.loads(answer)["sha"]
        if self.head is None:
            self._call("POST", "/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": sha})
        else:
            self._call("PATCH", f"/git/refs/heads/{urllib.parse.quote(self.branch)}", {"sha": sha})
        self.head = sha

    def test(self):
        self.start()  # reading the repository with its rights is the test; a commit would change the branch


# ---------------------------------------------------------------- DataFluss hosting

class HostingTarget(Target):
    """A site on another DataFluss server or on seiten.datafluss.de, with its site token (publish/hosting.py)."""

    def __init__(self, url, token):
        self.base = _https_only(url, "DataFluss-Hosting") + "/api/hosting/v1"
        self.token = token or ""
        if not self.token:
            raise ValidationError(_("DataFluss-Hosting: Bitte den Token der Website angeben."))
        self.site = {}

    def _call(self, method, path, data=None, ok=(200, 201, 204)):
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        return _http(method, f"{self.base}{path}", data=data, headers=headers, ok=ok, name="DataFluss-Hosting")

    def start(self):
        _status, answer = self._call("GET", "/site")
        self.site = json.loads(answer or b"{}")

    def read(self, path):
        status, body = self._call("GET", f"/files/{urllib.parse.quote(safe(path))}", ok=(200, 404))
        return body if status == 200 else None

    def put(self, path, data):
        self._call("PUT", f"/files/{urllib.parse.quote(safe(path))}", data=data)

    def delete(self, path):
        self._call("DELETE", f"/files/{urllib.parse.quote(safe(path))}", ok=(200, 204, 404))
