"""Bring a film collection from another program (MyMovies) into a table with TMDB: tables/collection.py."""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from transfer.models import ImportSession

from . import collection, tmdb
from .views_tmdb import _tmdb_table


def _session(request, table, pk):
    session = get_object_or_404(ImportSession, pk=pk, entity=f"{collection.ENTITY}{table.key}")
    if session.user_id != request.user.pk:
        raise Http404
    return session


def collection_page(request, key):
    """GET: choose the file (the browser reads it, static/js/collection.js). POST: the titles it found, as JSON."""
    table = _tmdb_table(request, key)
    if request.method == "POST":
        try:
            body = json.loads(request.body or b"{}")
            session = collection.start(table, request.user, body.get("titles"), body.get("filename"))
        except (ValueError, AttributeError):
            return JsonResponse({"error": _("Die Datei enthält keine lesbare Sammlung.")}, status=400)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        return JsonResponse({"url": reverse("collection_progress", args=[table.key, session.pk])})
    earlier = ImportSession.objects.filter(entity=f"{collection.ENTITY}{table.key}", user=request.user)[:5]
    return render(request, "tables/collection.html", {"table": table, "has_key": bool(tmdb.stored_key()), "earlier": earlier,
                                                      "may_set_key": request.user.can("tables.design")})


def collection_progress(request, key, pk):
    table = _tmdb_table(request, key)
    session = _session(request, table, pk)
    if not tmdb.stored_key():
        messages.error(request, _("Für diese Datenbank ist noch kein TMDB-Schlüssel eingetragen."))
        return redirect("collection", key=table.key)
    return render(request, "tables/collection.html", {"table": table, "session": session, "report": session.report, "has_key": True,
                                                      "step_url": reverse("collection_step", args=[table.key, session.pk]),
                                                      "finished": session.status == ImportSession.DONE})


@require_POST
def collection_step(request, key, pk):
    table = _tmdb_table(request, key)
    session = _session(request, table, pk)
    if session.status == ImportSession.DONE:
        return JsonResponse({**session.report, "finished": True, "total": session.report.get("titles", 0), "last": []})
    return JsonResponse(collection.step(session, table, request.user))

