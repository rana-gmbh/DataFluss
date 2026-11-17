"""Bring a film collection from another program into a table with TMDB (tables/tmdb.py), such as "Filme & Serien".

MyMovies exports its collection as XML. The browser reads that file (static/js/collection.js: often larger than
a request may be) and sends a few values per title:

    {"media": "Movie" | "TV Serie" | …, "local": "…", "original": "…", "year": "1986", "imdb": "tt0092005",
     "rating": "8" (1–10, -1 for none), "added": "03/07/2011 11:59:15 PM"}

group() turns them into one entry per film or series: copies of the same film (two editions) become one title
with a viewing for each, the seasons of a series one title with one viewing and the average of their ratings.
Each title is found at TMDB by its IMDb id (by name when there is none) and taken over with everything TMDB has;
the stars and viewings come from the collection. What TMDB does not know becomes a record with the collection's
title only. Running it again adds nothing twice: titles are found by their TMDB id, viewings by their day.

The import runs in steps (step()), each a short request, so a long collection shows its progress and can go on
after an interruption; its state is an ImportSession with the entity "collection:<table key>".
"""
import re
import time
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from . import tmdb

ENTITY = "collection:"
MAX_TITLES = 20000
STEP_SECONDS = 6
BUSY_SECONDS = 90
IMDB = re.compile(r"tt\d{7,10}")
TV_MEDIA = {"tv serie", "tv series", "tv show", "serie", "series"}
# Words that name a season or a box, not the series: "… : The Complete First Season", "… - Staffel 2", "Seizoen 7".
SEASON = re.compile(r"\s*[:\-–]\s*(the\s+)?(complete\s+)?(die\s+komplette\s+)?([\w.]+\s+)?(season|staffel|seizoen|saison|series|collection)\b.*$", re.I)


def _text(value, limit=300):
    return " ".join(str(value or "").split())[:limit]


def _imdb(value):
    value = _text(value, 20)
    return value if IMDB.fullmatch(value) and value != "tt0000000" else ""


def _stars(value):
    """MyMovies rates 1–10; the field has five stars in halves. -1 and anything else: no rating."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return Decimal(number) / 2 if 1 <= number <= 10 else None


def _day(value):
    for pattern in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(_text(value, 40), pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def series_name(title):
    """The series in a season's title: "Vikings: The Complete First Season" -> "Vikings"."""
    return SEASON.sub("", title or "").strip() or (title or "").strip()


def group(titles):
    """One entry per film or series: [{"imdb", "title", "original", "year", "tv", "stars": [..], "days": [..]}]."""
    if not isinstance(titles, list) or len(titles) > MAX_TITLES:
        raise ValidationError(_("Die Datei enthält keine lesbare Sammlung."))
    groups = {}
    for item in titles:
        if not isinstance(item, dict):
            continue
        local, original = _text(item.get("local")), _text(item.get("original"))
        if not (local or original):
            continue
        imdb, year = _imdb(item.get("imdb")), _text(item.get("year"), 4)
        tv = _text(item.get("media"), 40).casefold() in TV_MEDIA
        key = ("imdb", imdb) if imdb else ("title", series_name(original or local).casefold(), "" if tv else year)
        entry = groups.setdefault(key, {"imdb": imdb, "title": local or original, "original": original, "year": year,
                                        "tv": False, "stars": [], "days": []})
        entry["tv"] = entry["tv"] or tv
        if year and (not entry["year"] or year < entry["year"]):
            entry["year"] = year
        stars = _stars(item.get("rating"))
        if stars is not None:
            entry["stars"].append(str(stars))
        day = _day(item.get("added"))
        if day:
            entry["days"].append(day)
    return list(groups.values())


def summary(groups, count):
    return {"entries": count, "titles": len(groups), "series": sum(1 for g in groups if g["tv"]),
            "merged": sum(1 for g in groups if len(g["days"]) > 1), "without_imdb": sum(1 for g in groups if not g["imdb"])}


# ---------------------------------------------------------------- one title

def rating_of(entry):
    """The average of the ratings, in half stars."""
    values = [Decimal(v) for v in entry.get("stars") or []]
    if not values:
        return None
    return (sum(values) / len(values) * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2


def find(entry):
    """(kind, TMDB id) of the entry, by its IMDb id, else by its name and year; None when TMDB has nothing."""
    if entry.get("imdb"):
        try:
            answer = tmdb.call(f"/find/{entry['imdb']}", external_source="imdb_id", language=tmdb.language())
        except tmdb.TmdbError as exc:
            if not exc.missing:
                raise
            answer = {}
        for kind, key in (("movie", "movie_results"), ("tv", "tv_results")):
            if answer.get(key):
                return kind, int(answer[key][0]["id"])
        for key in ("tv_season_results", "tv_episode_results"):
            if answer.get(key) and answer[key][0].get("show_id"):
                return "tv", int(answer[key][0]["show_id"])
    for name in dict.fromkeys(filter(None, [series_name(entry.get("original")), series_name(entry.get("title"))])):
        found = tmdb.search(name, "tv" if entry.get("tv") else "movie")
        if entry.get("year") and not entry.get("tv"):
            close = [r for r in found if r["year"] and abs(int(r["year"]) - int(entry["year"])) <= 1]
            found = close or []
        if found:
            return found[0]["kind"], int(found[0]["id"])
    return None


def take_over(table, entry, user):
    """The entry as a record: ("created" | "existing" | "unmatched", record, problems)."""
    from . import services

    problems = []
    found = find(entry)
    if found is not None:
        kind, tmdb_id = found
        record, created, problems = tmdb.create(table, kind, tmdb_id, user, status="watched")
        outcome = "created" if created else "existing"
    else:
        kind, record = ("tv" if entry.get("tv") else "movie"), None
        outcome = "unmatched"
        record = _by_title(table, entry)
        if record is None:
            record = _plain(table, entry, kind)
    config = tmdb.config_of(table)
    fields = {f.key: f for f in services.fields_of(table, user=user)}
    rating = fields.get(_rating_key(table))
    stars = rating_of(entry)
    if rating is not None and stars is not None and not (record.values or {}).get(rating.key):
        try:
            services.set_value(record, rating, str(stars))
        except ValidationError as exc:
            problems.extend(exc.messages)
    days = sorted(set(entry.get("days") or []))
    if kind == "tv" and days:
        days = [days[-1]]  # the seasons of a series count as one viewing, on the day the last one came
    seen = _viewing_days(record)
    watch = config.get("watch") or {}
    try:
        for day in days:
            if day not in seen:
                tmdb.add_viewing(record, user, date.fromisoformat(day), "")
        status = fields.get(watch.get("status") or "")
        if not days and status is not None and watch.get("watched"):  # seen, but the collection says not when
            services.set_value(record, status, watch["watched"])
    except ValidationError as exc:
        problems.extend(exc.messages)
    return outcome, record, problems


def _rating_key(table):
    """The rating field the table shows with its pictures, else its first one."""
    from .models import Field

    chosen = (table.look or {}).get("rating")
    if chosen:
        return chosen
    first = Field.objects.filter(table=table, type="rating", archived=False).order_by("order").first()
    return first.key if first else ""


def _by_title(table, entry):
    from .models import Record

    title = _text(entry.get("title"))
    return Record.objects.filter(table=table, title__iexact=title[:300]).first() if title else None


def _plain(table, entry, kind):
    """A record from the collection alone, for a title TMDB does not know."""
    from . import services

    fields = (tmdb.config_of(table).get("fields") or {})
    values = {fields.get("title"): entry.get("title"), fields.get("original_title"): entry.get("original") if entry.get("original") != entry.get("title") else "",
              fields.get("year"): entry.get("year"), fields.get("kind"): (tmdb.config_of(table).get("kinds") or {}).get(kind, "")}
    return services.create_record(table, {key: value for key, value in values.items() if key and value})


def _viewing_days(record):
    """The days of the record's viewings (ISO)."""
    from . import links
    from .models import Link, Record, Table

    plan = (tmdb.config_of(record.table).get("watch") or {}).get("viewings") or {}
    target = Table.objects.filter(key=plan.get("table") or "", archived=False).first()
    if target is None or not plan.get("link") or not plan.get("date"):
        return set()
    ids = Link.objects.filter(record__table=target, field=plan["link"], target_id=record.pk, record__deleted_at=None).values_list("record_id", flat=True)
    return {str((r.values or {}).get(plan["date"]) or "") for r in Record.objects.filter(pk__in=list(ids))} - {""}


# ---------------------------------------------------------------- the import in steps

def start(table, user, titles, filename):
    from transfer.models import ImportSession

    groups = group(titles)
    if not groups:
        raise ValidationError(_("In der Datei wurden keine Titel gefunden."))
    report = {"done": 0, "created": 0, "existing": 0, "unmatched": [], "problems": [], **summary(groups, len(titles))}
    return ImportSession.objects.create(filename=_text(filename, 200) or "MyMovies", entity=f"{ENTITY}{table.key}", status=ImportSession.UPLOADED,
                                        rows=groups, report=report, user=user)


def step(session, table, user, seconds=STEP_SECONDS):
    """Take over the next titles for about `seconds`: the report, with "paused" when TMDB stopped the step."""
    from core import audit
    from transfer.models import ImportSession

    now = time.time()
    with transaction.atomic():  # one step at a time, even with the page open twice
        session = ImportSession.objects.select_for_update().get(pk=session.pk)
        if (session.report.get("busy_until") or 0) > now:
            return {**session.report, "busy": True, "finished": False, "total": session.report.get("titles", 0), "last": []}
        ImportSession.objects.filter(pk=session.pk).update(report={**session.report, "busy_until": now + BUSY_SECONDS})
    report = dict(session.report)
    rows = session.rows or []
    last = []
    try:
        while report["done"] < len(rows):
            entry = rows[report["done"]]
            try:
                outcome, record, problems = take_over(table, entry, user)
            except tmdb.TmdbError as exc:
                report["paused"] = str(exc)
                break
            report["done"] += 1
            report.pop("paused", None)
            if outcome == "unmatched":
                report["unmatched"].append(entry.get("title") or entry.get("original"))
            else:
                report[outcome] += 1
            report["problems"].extend(f"{record.title}: {p}" for p in problems[:3])
            report["problems"] = report["problems"][-200:]
            last.append(record.title)
            if time.time() - now > seconds:
                break
    finally:
        report["busy_until"] = 0
        finished = report["done"] >= len(rows)
        update = {"report": report}
        if finished and session.status != ImportSession.DONE:
            update.update(status=ImportSession.DONE, rows=[])
            audit.log("table", f"Sammlung übernommen ({session.filename}): {report['created']} neu, {report['existing']} schon da, "
                               f"{len(report['unmatched'])} ohne TMDB", user=user)
        ImportSession.objects.filter(pk=session.pk).update(**update)
    return {**report, "last": last, "finished": finished, "total": report["titles"]}
