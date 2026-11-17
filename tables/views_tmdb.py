"""TMDB in own tables (tables/tmdb.py): the key under Einstellungen → Tabellen, search and import from a table's
list, and on a record page an update from TMDB and the quick "watched" form."""
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from core import audit
from core.fieldtypes import parse_date
from core.permissions import require

from . import blueprints, services, tmdb
from .models import Record
from .views_records import _record, _table


# ---------------------------------------------------------------- the key

@require("tables.design")
def tmdb_settings(request):
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "remove":
            tmdb.save_key("")
            audit.log("settings", "TMDB-Schlüssel entfernt", request=request)
            messages.success(request, _("Der TMDB-Schlüssel wurde entfernt."))
            return redirect("tables_tmdb")
        try:
            key = tmdb.clean_key(request.POST.get("key", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("tables_tmdb")
        accepted, rejected, problem = tmdb.test_key(key)
        if rejected:
            messages.error(request, problem)
            return redirect("tables_tmdb")
        tmdb.save_key(key)
        audit.log("settings", "TMDB-Schlüssel gespeichert", request=request)
        if not accepted:
            messages.warning(request, _("Gespeichert, aber noch nicht geprüft: %(problem)s") % {"problem": problem})
        else:
            messages.success(request, _("Der Schlüssel funktioniert und ist gespeichert."))
        return redirect("tables_tmdb")
    key = tmdb.stored_key()
    tables = [t for t in services.readable(request.user) if tmdb.supports(t)]
    return render(request, "tables/tmdb.html", {
        "has_key": bool(key), "key_kind": tmdb.kind_of_key(key) if key else "", "key_end": key[-4:] if key else "",
        "settings_url": tmdb.SETTINGS_URL, "tables": tables, "active_nav": "settings_home",
        "templates_offered": blueprints.any_offered(),
    })


@require("tables.design")
@require_POST
def tmdb_test(request):
    """Tests the typed key, or the stored one when nothing is typed (htmx)."""
    typed = request.POST.get("key", "").strip()
    if typed:
        try:
            key = tmdb.clean_key(typed)
        except ValidationError as exc:
            return render(request, "tables/_tmdb_test.html", {"ok": False, "message": " ".join(exc.messages)})
    else:
        key = tmdb.stored_key()
        if not key:
            return render(request, "tables/_tmdb_test.html", {"ok": False, "message": _("Bitte zuerst einen Schlüssel einfügen.")})
    accepted, _rejected, problem = tmdb.test_key(key)
    return render(request, "tables/_tmdb_test.html", {
        "ok": accepted, "message": problem or (_("Der Lesezugriffs-Token funktioniert.") if tmdb.kind_of_key(key) == "token" else _("Der API-Schlüssel funktioniert.")),
    })


# ---------------------------------------------------------------- search and import

def _tmdb_table(request, key):
    table = _table(request, key, edit=True)
    if not tmdb.supports(table):
        raise Http404
    return table


@require_GET
def tmdb_search(request, key):
    table = _tmdb_table(request, key)
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("art") if request.GET.get("art") in tmdb.KINDS else ""
    context = {"table": table, "q": query, "results": [], "problem": "", "may_set_key": request.user.can("tables.design"),
               "has_key": bool(tmdb.stored_key())}
    if query and context["has_key"]:
        try:
            results = tmdb.search(query, kind)
        except tmdb.TmdbError as exc:
            context["problem"] = str(exc)
        else:
            refs = [tmdb.tmdb_ref(r["kind"], r["id"]) for r in results]
            id_key = (tmdb.config_of(table).get("fields") or {}).get("tmdb_id")
            known = {}
            if id_key:
                for record in Record.objects.filter(table=table, **{f"values__{id_key}__in": refs}):
                    known[record.values.get(id_key)] = reverse("record_detail", args=[table.key, record.pk])
            for result in results:
                result["record_url"] = known.get(tmdb.tmdb_ref(result["kind"], result["id"]), "")
                result["thumb"] = reverse("tmdb_thumb", args=[table.key, "w92", result["poster"].lstrip("/")]) if result["poster"] else ""
            context["results"] = results
    return render(request, "tables/_tmdb_results.html", context)


@require_GET
def tmdb_thumb(request, key, size, path):
    """A small poster for the search, fetched by the server: the page itself never talks to TMDB."""
    _tmdb_table(request, key)
    if size not in tmdb.THUMB_SIZES:
        raise Http404
    try:
        body, extension = tmdb.image(size, "/" + path)
    except tmdb.TmdbError:
        raise Http404
    response = HttpResponse(body, content_type=f"image/{'jpeg' if extension == 'jpg' else extension}")
    response["Cache-Control"] = "private, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _wanted(request):
    kind = request.POST.get("kind")
    try:
        tmdb_id = int(request.POST.get("id", ""))
    except ValueError:
        tmdb_id = 0
    if kind not in tmdb.KINDS or tmdb_id <= 0:
        raise Http404
    return kind, tmdb_id


def _report(request, problems):
    if problems:
        messages.warning(request, _("Nicht alles kam von TMDB an: %(problems)s") % {"problems": " ".join(problems[:3])})


@require_POST
def tmdb_import(request, key):
    table = _tmdb_table(request, key)
    kind, tmdb_id = _wanted(request)
    status = request.POST.get("status", "")
    try:
        record, created, problems = tmdb.create(table, kind, tmdb_id, request.user, status=status)
    except tmdb.TmdbError as exc:
        messages.error(request, str(exc))
        return redirect("table_records", key=table.key)
    if created:
        audit.log("table", f"Aus TMDB übernommen: {record.title} ({tmdb.tmdb_ref(kind, tmdb_id)})", request=request, target=record.pk)
        if status == "watched" and request.POST.get("viewing"):
            tmdb.add_viewing(record, request.user, date.today(), request.POST.get("how", ""))
        messages.success(request, _("%(title)s wurde aus TMDB übernommen.") % {"title": record.title})
    else:
        messages.info(request, _("%(title)s ist schon in der Tabelle.") % {"title": record.title})
    _report(request, problems)
    return redirect("record_detail", key=table.key, pk=record.pk)


@require_POST
def tmdb_refresh(request, key, pk):
    """Update a record from TMDB: by its TMDB id, or with the title chosen in the search (kind and id posted)."""
    table = _tmdb_table(request, key)
    record = _record(table, pk)
    if request.POST.get("id"):
        kind, tmdb_id = _wanted(request)
        other = tmdb.find_record(table, kind, tmdb_id)
        if other is not None and other.pk != record.pk:
            messages.info(request, _("Dieser Titel ist schon als %(title)s in der Tabelle.") % {"title": other.title})
            return redirect("record_detail", key=table.key, pk=other.pk)
    else:
        id_key = (tmdb.config_of(table).get("fields") or {}).get("tmdb_id")
        found = tmdb.parse_ref((record.values or {}).get(id_key or "", ""))
        if found is None:
            raise Http404
        kind, tmdb_id = found
    try:
        problems = tmdb.fill(record, kind, tmdb.details(kind, tmdb_id), request.user, lang=tmdb.language())
    except tmdb.TmdbError as exc:
        messages.error(request, str(exc))
        return redirect("record_detail", key=table.key, pk=record.pk)
    audit.log("table", f"Mit TMDB abgeglichen: {record.title}", request=request, target=record.pk)
    messages.success(request, _("%(title)s wurde mit TMDB abgeglichen.") % {"title": record.title})
    _report(request, problems)
    return redirect("record_detail", key=table.key, pk=record.pk)


@require_POST
def record_watched(request, key, pk):
    """"Gesehen ✓": a viewing on the chosen day (today by default) and the status "watched"."""
    table = _table(request, key, edit=True)
    if not (tmdb.config_of(table).get("watch") or {}):
        raise Http404
    record = _record(table, pk)
    try:
        day = parse_date(request.POST.get("date", "")) or date.today()
    except ValidationError:
        day = date.today()
    how = request.POST.get("how", "")
    if how and how not in dict(tmdb.how_choices(table)):
        how = ""
    try:
        viewing = tmdb.add_viewing(record, request.user, day, how)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, _("Als gesehen eingetragen.") if viewing is not None else _("Als gesehen markiert."))
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/tabellen/") else reverse("record_detail", args=[table.key, record.pk]))
