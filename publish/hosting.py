"""DataFluss hosting v1: a site that another computer publishes to with a token (docs/publish.md "Hosting").

Used by DataFluss servers (publish/hosting_api.py, sites under /p/<name>/) and by the template site
datafluss.de (web/seiten/, sites under seiten.datafluss.de/<name>/), so it imports nothing of the platform but
Django. The other side is publish/cloud.py HostingTarget.

    Authorization: Bearer <site token>
    GET    /api/hosting/v1/site            {"name", "url", "quota", "used"}
    GET    /api/hosting/v1/files/<path>    the file, or 404
    PUT    /api/hosting/v1/files/<path>    the body is the file
    DELETE /api/hosting/v1/files/<path>

Whatever arrives may be anything, so: paths are plain and relative, files have a size limit, a site has a quota,
and every file is served with a sandbox Content-Security-Policy. The page runs in an origin of its own, so its
scripts reach nothing of the server that serves it (no cookies, no requests in the name of whoever is signed in).
Types that are not part of a website download instead of opening.
"""
import hashlib
import mimetypes
import os
import re
import secrets
import tempfile
from pathlib import Path

from django.http import FileResponse, Http404, JsonResponse

SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")
MANIFEST = "datafluss-manifest.json"
MAX_FILE = 50 * 1024 * 1024
INLINE = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8",
          "json": "application/json", "txt": "text/plain; charset=utf-8", "svg": "image/svg+xml", "webp": "image/webp",
          "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "ico": "image/x-icon",
          "woff2": "font/woff2", "pdf": "application/pdf"}
SANDBOX_CSP = ("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads; default-src 'self'; "
               "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; "
               "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def bearer(request):
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""


def safe(path):
    if not SAFE_PATH.match(path or "") or ".." in path.split("/") or "//" in path or len(path) > 300:
        raise Refused(400, "invalid path")
    return path


def file_of(root, path):
    root = Path(root).resolve()
    target = (root / safe(path)).resolve()
    if not target.is_relative_to(root) or target == root:
        raise Refused(400, "invalid path")
    return target


def usage(root):
    root = Path(root)
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.is_dir() else 0


def store(root, path, data, quota):
    """Write one file atomically; refuses what is too large or would pass the site's quota (bytes)."""
    if len(data) > MAX_FILE:
        raise Refused(413, "file too large")
    target = file_of(root, path)
    before = target.stat().st_size if target.is_file() else 0
    if quota and usage(root) - before + len(data) > quota:
        raise Refused(507, "quota exceeded")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, prefix=".upload-") as handle:
        handle.write(data)
    os.replace(handle.name, target)


def remove(root, path):
    target = file_of(root, path)
    if target.is_file():
        target.unlink()
    parent, base = target.parent, Path(root).resolve()
    while parent != base and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def remove_all(root):
    import shutil

    shutil.rmtree(root, ignore_errors=True)


def api(request, root, path, quota, info):
    """The hosting API for one site that the caller already found by its token. `path` None is the site itself."""
    try:
        if path is None:
            if request.method != "GET":
                raise Refused(405, "method not allowed")
            return JsonResponse({**info, "quota": quota, "used": usage(root)})
        if request.method == "GET":
            target = file_of(root, path)
            if not target.is_file():
                raise Refused(404, "not found")
            return FileResponse(target.open("rb"), content_type="application/octet-stream")
        if request.method == "PUT":
            store(root, path, request.body, quota)
            return JsonResponse({"ok": True}, status=201)
        if request.method == "DELETE":
            remove(root, path)
            return JsonResponse({"ok": True})
        raise Refused(405, "method not allowed")
    except Refused as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)


def serve(root, path, indexable=True):
    """A file of a hosted site, for anyone, in a sandbox."""
    if path == "" or path.endswith("/"):
        path += "index.html"
    if path == MANIFEST or path.split("/")[-1].startswith(".upload-"):
        raise Http404
    try:
        target = file_of(root, path)
    except Refused:
        raise Http404
    if not target.is_file():
        raise Http404
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    response = FileResponse(target.open("rb"), content_type=INLINE.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream")
    if ext not in INLINE:
        response["Content-Disposition"] = "attachment"
    response["Content-Security-Policy"] = SANDBOX_CSP
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("_media/") else "public, no-cache"
    if not indexable:
        response["X-Robots-Tag"] = "noindex, nofollow"
    return response

