"""Einstellungen → Veröffentlichen, and the sites this server hosts under /p/<slug>/ (docs/publish.md)."""
import mimetypes
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST, require_safe

from archive import plan
from core import audit, fieldrules
from core.models import Role, role_choices
from core.permissions import require
from tables import services

from . import service, snapshot, targets
from .models import Publication
from .render import MANIFEST

LAYOUTS = ("list", "gallery")
TYPES = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8",
         "svg": "image/svg+xml", "webp": "image/webp", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8",
         "json": "application/json"}
SITE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _targets():
    """The targets this database offers: a computer hosts nothing, a server has no folder of its own to pick."""
    names = {Publication.ZIP: _("ZIP-Datei"), Publication.FOLDER: _("Ordner auf diesem Computer"),
             Publication.SERVER: _("Auf diesem Server (/p/…)"), Publication.WEBDAV: _("WebDAV"), Publication.FTP: _("FTP / FTPS")}
    hidden = {Publication.SERVER} if plan.is_client() else {Publication.FOLDER}
    return [(key, names[key]) for key in Publication.TARGETS if key not in hidden]


def _roles():
    return [(value, label) for value, label in role_choices()]


def _default_role():
    keys = [value for value, _label in _roles()]
    for key in ("gast", Role.SCHULLEITUNG):
        if key in keys:
            return key
    return next((k for k in keys if k != Role.IT), Role.IT)


def _languages():
    from core.views_language import allowed_languages

    return allowed_languages()


def _unique_slug(text, exclude=None):
    base = slugify(services._slug(text, 40).replace("_", "-")) or "seite"
    taken = set(Publication.objects.exclude(pk=exclude).values_list("slug", flat=True))
    slug, n = base[:40], 1
    while slug in taken:
        n += 1
        slug = f"{base[:36]}-{n}"
    return slug


def _offer(publication):
    """Tables and fields the role may see, with what is chosen: what the Inhalt section offers."""
    base = SimpleNamespace(role=publication.role, is_authenticated=True)
    chosen = publication.tables()
    out = []
    for table in services.readable(base):
        spec = chosen.get(table.key) or {}
        entity = fieldrules.table_entity(table)
        locked = fieldrules.locked(entity)
        fields = [f for f in services.fields_of(table, user=base) if f.type != "secret"]
        from tables import look

        out.append({
            "table": table, "chosen": table.key in chosen, "layout": spec.get("layout") or (table.look or {}).get("layout") or "list",
            "files": bool(spec.get("files")), "gallery": look.resolve(table, base, fields)["cover"] is not None,
            "fields": [{"field": f, "chosen": f.key in set(spec.get("fields") or []) or f.key in locked, "locked": f.key in locked} for f in fields],
            "any_files": any(f.type == "file" for f in fields),
        })
    return out


def _choice_from(post, publication):
    base = SimpleNamespace(role=publication.role, is_authenticated=True)
    choice = {}
    for table in services.readable(base):
        if not post.get(f"t:{table.key}"):
            continue
        seen = {f.key for f in services.fields_of(table, user=base) if f.type != "secret"}
        fields = [k for k in post.getlist(f"f:{table.key}") if k in seen]
        locked = fieldrules.locked(fieldrules.table_entity(table))
        layout = post.get(f"layout:{table.key}")
        choice[table.key] = {"fields": sorted(set(fields) | (locked & seen)), "layout": layout if layout in LAYOUTS else "list",
                             "files": bool(post.get(f"files:{table.key}"))}
    return choice


def _pick(request, pk):
    return get_object_or_404(Publication, pk=pk)


# ---------------------------------------------------------------- settings pages

@require("publish.manage")
def publication_list(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:100]
        if not name:
            messages.error(request, _("Bitte einen Namen eingeben."))
            return redirect("publication_list")
        publication = Publication.objects.create(name=name, slug=_unique_slug(name), role=_default_role(),
                                                 language=request.LANGUAGE_CODE if request.LANGUAGE_CODE in dict(_languages()) else _languages()[0][0],
                                                 target=_targets()[0][0])
        audit.log("publish", f"Veröffentlichung angelegt: {name}", request=request, target=publication.pk)
        return redirect("publication_detail", pk=publication.pk)
    return render(request, "publish/list.html", {"publications": Publication.objects.all(), "active_nav": "settings_home"})


@require("publish.manage")
def publication_detail(request, pk):
    publication = _pick(request, pk)
    if request.method == "POST":
        try:
            _save(request, publication)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Gespeichert."))
        return redirect("publication_detail", pk=publication.pk)
    return render(request, "publish/detail.html", {
        "p": publication, "offer": _offer(publication), "roles": _roles(), "languages": _languages(), "targets": _targets(),
        "has_password": bool(publication.target_secret), "config": publication.target_config or {},
        "site_url": request.build_absolute_uri(f"/p/{publication.slug}/") if publication.target == Publication.SERVER else "",
        "zip_ready": publication.target == Publication.ZIP and default_storage.exists(service.zip_name(publication)),
        "preview_ready": default_storage.exists(f"{service.preview_folder(publication)}/index.html"),
        "on_computer": plan.is_client(), "needs_confirmation": service.needs_confirmation(publication), "active_nav": "settings_home",
    })


def _save(request, publication):
    post = request.POST
    section = post.get("section")
    old_target = (publication.target, publication.slug, publication.manifest)
    if section == "settings":
        publication.name = (post.get("name") or publication.name).strip()[:100] or publication.name
        slug = slugify(post.get("slug") or "")[:40]
        if slug and slug != publication.slug:
            if Publication.objects.exclude(pk=publication.pk).filter(slug=slug).exists():
                raise ValidationError(_("Diese Adresse ist schon vergeben."))
            publication.slug = slug
        if post.get("language") in dict(_languages()):
            publication.language = post["language"]
        if post.get("role") in dict(_roles()):
            publication.role = post["role"]
        publication.indexable = bool(post.get("indexable"))
    elif section == "content":
        publication.choice = _choice_from(post, publication)
    elif section == "target":
        kind = post.get("target")
        if kind not in dict(_targets()):
            raise ValidationError(_("Unbekanntes Ziel."))
        publication.target = kind
        config = {}
        if kind == Publication.FOLDER:
            config = {"path": (post.get("path") or "").strip()[:500]}
        elif kind == Publication.WEBDAV:
            config = {"url": (post.get("url") or "").strip()[:500], "user": (post.get("user") or "").strip()[:200]}
        elif kind == Publication.FTP:
            port = (post.get("port") or "").strip()
            if port and not port.isdigit():
                raise ValidationError(_("Der Port ist eine Zahl."))
            config = {"host": (post.get("host") or "").strip()[:200], "port": int(port) if port else 21,
                      "user": (post.get("user") or "").strip()[:200], "path": (post.get("path") or "").strip()[:500],
                      "tls": not post.get("plain")}
        publication.target_config = config
        if post.get("password"):
            targets.set_password(publication, post["password"])
        elif post.get("forget"):
            publication.target_secret = ""
    publication.save()
    # A site on this server that moves away (another target or address) is taken off its old place.
    if old_target[0] == Publication.SERVER and (publication.target != Publication.SERVER or publication.slug != old_target[1]):
        target = targets.StorageTarget(f"published/{old_target[1]}")
        target.remove_all({**targets.manifest_at(target), **((old_target[2] or {}).get("files") or {})})
    audit.log("publish", f"Veröffentlichung {publication.name} geändert ({section})", request=request, target=publication.pk)


@require("publish.manage")
@require_POST
def publication_test(request, pk):
    publication = _pick(request, pk)
    try:
        targets.target_for(publication).test()
    except ValidationError as exc:
        return render(request, "publish/_test.html", {"ok": False, "message": " ".join(exc.messages)})
    return render(request, "publish/_test.html", {"ok": True, "message": _("Verbindung klappt, Schreiben geht.")})


@require("publish.manage")
def publication_confirm(request, pk):
    """What goes public, before the first publish and after the choice or role changed; then the run."""
    publication = _pick(request, pk)
    if request.method == "POST":
        kind = request.POST.get("kind", service.PUBLISH)
        if kind == service.PUBLISH and service.needs_confirmation(publication) and not request.POST.get("confirm"):
            return redirect("publication_confirm", pk=publication.pk)
        if kind == service.PUBLISH and not publication.choice:
            messages.error(request, _("Es ist noch keine Tabelle ausgewählt."))
            return redirect("publication_detail", pk=publication.pk)
        try:
            service.start(publication, kind if kind in (service.PUBLISH, service.PREVIEW) else service.PUBLISH, request.user, request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("publication_detail", pk=publication.pk)
    listed = snapshot.published_fields(publication)
    return render(request, "publish/confirm.html", {
        "p": publication, "listed": listed, "school": "schulverwaltung" in _active(), "active_nav": "settings_home",
        "role_name": dict(_roles()).get(publication.role, publication.role),
        "records": sum(t.records.count() for t, _fields in listed),
    })


def _active():
    from core import modules

    return modules.active()


@require("publish.manage")
@require_GET
def publication_status(request, pk):
    publication = _pick(request, pk)
    if request.GET.get("running") and publication.status != Publication.RUNNING:
        response = HttpResponse("")
        response["HX-Refresh"] = "true"  # the buttons (ZIP, preview, address) follow the new state
        return response
    return render(request, "publish/_status.html", {"p": publication})


@require("publish.manage")
@require_POST
def publication_withdraw(request, pk):
    publication = _pick(request, pk)
    service.withdraw(publication, request)
    messages.success(request, _("Zurückgezogen."))
    return redirect("publication_detail", pk=publication.pk)


@require("publish.manage")
@require_POST
def publication_delete(request, pk):
    publication = _pick(request, pk)
    service.withdraw(publication, request)
    audit.log("publish", f"Veröffentlichung gelöscht: {publication.name}", request=request, target=publication.pk)
    publication.delete()
    messages.success(request, _("Gelöscht."))
    return redirect("publication_list")


@require("publish.manage")
def publication_zip(request, pk):
    publication = _pick(request, pk)
    name = service.zip_name(publication)
    if not default_storage.exists(name):
        raise Http404
    audit.log("download", f"Veröffentlichung {publication.name} als ZIP", request=request, target=publication.pk)
    return FileResponse(default_storage.open(name, "rb"), as_attachment=True, filename=f"{publication.slug}.zip")


@require("publish.manage")
def publication_preview(request, pk, path=""):
    """The preview of a site inside the app (published/.preview/<id>/)."""
    publication = _pick(request, pk)
    return _serve(service.preview_folder(publication), path, indexable=False, private=True)


# ---------------------------------------------------------------- the sites of this server

@require_safe
def site_file(request, slug, path=""):
    """A file of a site this server hosts: public, read-only, for anyone (core/middleware.py PUBLIC_NAMES)."""
    publication = Publication.objects.filter(slug=slug, target=Publication.SERVER).exclude(published_at=None).first()
    if publication is None or plan.is_client():
        raise Http404
    return _serve(targets.server_folder(publication), path, indexable=publication.indexable, private=False)


def _serve(folder, path, indexable, private):
    if path == "" or path.endswith("/"):
        path += "index.html"
    if path == MANIFEST:
        raise Http404
    try:
        name = f"{folder}/{targets.safe(path)}"
    except ValidationError:
        raise Http404
    if not default_storage.exists(name):
        raise Http404
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    content_type = TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    response = FileResponse(default_storage.open(name, "rb"), content_type=content_type)
    if ext not in TYPES:  # files of records download, they never open in the site's origin
        response["Content-Disposition"] = "attachment"
    response["Content-Security-Policy"] = SITE_CSP
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    if not indexable:
        response["X-Robots-Tag"] = "noindex, nofollow"
    if private:
        response["Cache-Control"] = "private, no-store"
    elif path.startswith("_media/"):
        response["Cache-Control"] = "public, max-age=31536000, immutable"  # named after their content
    else:
        response["Cache-Control"] = "public, no-cache"  # pages change with each publish; pictures keep their names
    return response

