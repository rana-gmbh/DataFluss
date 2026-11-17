"""Films and series from themoviedb.org (TMDB API v3) for own tables, such as the template "Filme & Serien".

Each database brings its own key (SchoolSettings.tmdb_key, encrypted like a secret field): the v3 "API key"
(32 hex characters, sent as api_key) or the v4 "API read access token" (a long token starting with "eyJ",
sent as a bearer token). Both reach the same v3 endpoints.

Which fields a found title fills is the table's business, in Table.look["tmdb"] (tables/look.py):

    {"fields": {"title": "f_titel", "original_title": …, "kind": …, "year": …, "runtime": …, "seasons": …,
                "episodes": …, "genres": …, "director": …, "creators": …, "cast": …, "overview": …,
                "tagline": …, "tmdb_id": …, "tmdb_url": …, "poster": …, "backdrop": …, "images": …,
                "cast_photos": …, "synced": …},           # synced: a date field, the day of the last fetch
     "kinds": {"movie": "Film", "tv": "Serie"},            # option values of the "kind" choice
     "genres": {"28": "Action", "10759": ["Action", "Abenteuer"], …},  # TMDB genre id -> option value(s)
     "watch": {"status": "f_status", "watchlist": "Merkliste", "watched": "Gesehen",
               "viewings": {"table": "fs_sichtungen", "link": "f_titel", "date": "f_datum", "how": "f_wie"}}}

Pictures are fetched once and kept as attachments of the record's image fields, so they work offline and on
connected computers. map_details is a plain function of TMDB's answer, so it can be tested without network.

TMDB's terms forbid keeping their data longer than six months. refresh_due, part of the nightly work (hourly
on a server, daily on a computer with a database of its own), fetches every title again REFRESH_AFTER days
after its last fetch, in the language it was last fetched in ("language" in the table's TMDB settings).
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language, gettext as _

API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p"
SITE = "https://www.themoviedb.org"
SETTINGS_URL = "https://www.themoviedb.org/settings/api"
TIMEOUT = 15
MAX_IMAGE = 6 * 1024 * 1024
IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]{1,80}\.(jpg|jpeg|png|webp)$")
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
THUMB_SIZES = ("w92", "w185")
KINDS = ("movie", "tv")
CAST = 10           # actors in the text
CAST_PHOTOS = 8     # of whom photos are kept
STILLS = 4          # further pictures besides the backdrop
LANGUAGES = {"de": "de-DE", "en": "en-US", "zh-hans": "zh-CN", "zh-hant": "zh-TW"}
REFRESH_AFTER = 150  # days; well inside TMDB's six months, even when a computer is rarely open
REFRESH_BATCH = 40   # titles per round, so one round never keeps TMDB (or the nightly work) busy for long


class TmdbError(Exception):
    """The message is shown to the person searching or importing. `rejected`: TMDB refused the key;
    `missing`: TMDB does not know the title (any more)."""

    def __init__(self, message, rejected=False, missing=False):
        super().__init__(message)
        self.rejected = rejected
        self.missing = missing


# ---------------------------------------------------------------- the key

def stored_key():
    from core.models import SchoolSettings
    from vault import crypto

    token = SchoolSettings.load().tmdb_key
    if not token:
        return ""
    try:
        return crypto.decrypt(token, "tmdb")
    except Exception:  # another vault key (a restored backup of another database): as if there were none
        return ""


def save_key(key):
    from core.models import SchoolSettings
    from vault import crypto

    SchoolSettings.load()
    SchoolSettings.objects.filter(pk=1).update(tmdb_key=crypto.encrypt(key, "tmdb") if key else "")


def clean_key(text):
    """The key as pasted: spaces, quotes and a leading "Bearer " are dropped. Raises ValidationError when it
    looks like neither kind of key."""
    key = re.sub(r"\s+", "", (text or "").strip().strip("\"'"))
    key = re.sub(r"^Bearer", "", key, flags=re.IGNORECASE)
    if re.fullmatch(r"[0-9a-fA-F]{32}", key) or re.fullmatch(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", key):
        return key
    raise ValidationError(_("Das sieht weder wie ein API-Schlüssel (32 Zeichen) noch wie ein Lesezugriffs-Token (beginnt mit „eyJ“) aus."))


def kind_of_key(key):
    return "token" if key.startswith("eyJ") else "api_key"


# ---------------------------------------------------------------- talking to TMDB

def _context():
    from sync.client import _context as context  # the computer's own certificate store in the app

    try:
        return context()
    except Exception:
        return ssl.create_default_context()


def _open(url, headers=None, limit=None):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DataFluss", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_context()) as response:
            body = response.read((limit or 4 * 1024 * 1024) + 1)
            if limit and len(body) > limit:
                raise TmdbError(_("TMDB hat ein zu großes Bild geschickt."))
            return body, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise TmdbError(_("TMDB lehnt den Schlüssel ab. Bitte unter Einstellungen → Tabellen → TMDB prüfen."), rejected=True)
        if exc.code == 404:
            raise TmdbError(_("Diesen Titel gibt es bei TMDB nicht (mehr)."), missing=True)
        if exc.code == 429:
            raise TmdbError(_("TMDB bittet um eine kurze Pause. Bitte gleich noch einmal versuchen."))
        raise TmdbError(_("TMDB antwortet mit einem Fehler (%(code)s). Bitte später noch einmal versuchen.") % {"code": exc.code})
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TmdbError(_("TMDB ist nicht erreichbar. Besteht eine Internetverbindung?"))


def call(path, key=None, **params):
    """GET a v3 endpoint as JSON."""
    key = key if key is not None else stored_key()
    if not key:
        raise TmdbError(_("Für diese Datenbank ist noch kein TMDB-Schlüssel eingetragen."))
    headers = {}
    if kind_of_key(key) == "token":
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    body, _type = _open(f"{API}{path}?{query}", headers)
    try:
        return json.loads(body)
    except ValueError:
        raise TmdbError(_("TMDB hat unerwartet geantwortet. Bitte später noch einmal versuchen."))


def test_key(key):
    """(accepted, rejected, problem): TMDB took the key; TMDB refused it; or what went wrong in words."""
    try:
        answer = call("/authentication", key=key)
    except TmdbError as exc:
        return False, exc.rejected, str(exc)
    if answer.get("success"):
        return True, False, ""
    return False, True, _("TMDB lehnt den Schlüssel ab.")


def language():
    return LANGUAGES.get((get_language() or "de").lower(), "de-DE")


def year_of(text):
    return text[:4] if text and re.match(r"^\d{4}", text) else ""


def search(query, kind="", key=None):
    """Films and series for a title: [{"kind", "id", "title", "original_title", "year", "overview", "poster"}]."""
    query = (query or "").strip()[:200]
    if not query:
        return []
    path = {"movie": "/search/movie", "tv": "/search/tv"}.get(kind, "/search/multi")
    answer = call(path, key=key, query=query, language=language(), include_adult="false", page=1)
    found = []
    for item in answer.get("results") or []:
        media = kind or item.get("media_type")
        if media not in KINDS or not item.get("id"):
            continue
        title = item.get("title") if media == "movie" else item.get("name")
        original = item.get("original_title") if media == "movie" else item.get("original_name")
        found.append({
            "kind": media, "id": int(item["id"]), "title": title or original or "", "original_title": original or "",
            "year": year_of(item.get("release_date") if media == "movie" else item.get("first_air_date")),
            "overview": item.get("overview") or "", "poster": item.get("poster_path") or "",
            "popularity": item.get("popularity") or 0,
        })
    return found[:20]


def details(kind, tmdb_id, key=None, lang=None):
    if kind not in KINDS:
        raise TmdbError(_("Unbekannte Art."))
    lang = lang if lang in LANGUAGES.values() else language()
    # A series' "credits" hold only its latest season; "aggregate_credits" sums up all seasons.
    credits = "credits" if kind == "movie" else "aggregate_credits"
    data = call(f"/{kind}/{int(tmdb_id)}", key=key, language=lang, append_to_response=f"{credits},images",
                include_image_language=f"{lang[:2]},null,en")
    if not data.get("overview") and lang != "en-US":  # many titles have no text in the language on screen
        try:
            data["overview"] = call(f"/{kind}/{int(tmdb_id)}", key=key, language="en-US").get("overview") or ""
        except TmdbError:
            pass
    return data


def image(size, path):
    """(bytes, extension) of a TMDB picture."""
    if not IMAGE_PATH.match(path or ""):
        raise TmdbError(_("Ungültiger Bildpfad."))
    body, content_type = _open(f"{IMAGES}/{size}{path}", {"Accept": "image/*"}, limit=MAX_IMAGE)
    extension = IMAGE_TYPES.get(content_type.split(";")[0].strip().lower())
    if extension is None:
        raise TmdbError(_("TMDB hat kein Bild geschickt."))
    return body, extension


# ---------------------------------------------------------------- what a title fills

def config_of(table):
    return (table.look or {}).get("tmdb") or {}


def supports(table):
    return bool(config_of(table).get("fields"))


def tmdb_ref(kind, tmdb_id):
    return f"{kind}/{int(tmdb_id)}"


def parse_ref(text):
    match = re.fullmatch(r"(movie|tv)/(\d{1,10})", (text or "").strip())
    return (match.group(1), int(match.group(2))) if match else None


def _genres(data, config):
    mapping = config.get("genres") or {}
    values = []
    for genre in data.get("genres") or []:
        mapped = mapping.get(str(genre.get("id")))
        if mapped is None:
            mapped = genre.get("name") or ""
        values.extend(mapped if isinstance(mapped, list) else [mapped])
    return [v for v in dict.fromkeys(values) if v]


def _main_role(person):
    """The character, or for a series (aggregate credits) the role with the most episodes."""
    roles = sorted((r for r in person.get("roles") or [] if r.get("character")), key=lambda r: -(r.get("episode_count") or 0))
    return roles[0]["character"] if roles else person.get("character") or ""


def map_details(kind, data, config):
    """{"values": {field key: text or list as a form would send it}, "images": {field key: [(size, path, name)]}}
    for a TMDB answer (details with credits and images)."""
    fields = config.get("fields") or {}
    movie = kind == "movie"
    credits = data.get("credits") or {}
    cast = [dict(c, character=_main_role(c)) for c in (data.get("aggregate_credits") or credits).get("cast") or [] if c.get("name")]
    cast = sorted(cast, key=lambda c: c.get("order", 999))[:CAST]
    runtime = data.get("runtime") if movie else next(iter(data.get("episode_run_time") or []), None)
    title = (data.get("title") if movie else data.get("name")) or ""
    original = (data.get("original_title") if movie else data.get("original_name")) or ""
    texts = {
        "title": title or original,
        "original_title": original if original and original != title else "",
        "kind": (config.get("kinds") or {}).get(kind, ""),
        "year": year_of(data.get("release_date") if movie else data.get("first_air_date")),
        "runtime": str(runtime) if runtime else "",
        "seasons": "" if movie else str(data.get("number_of_seasons") or ""),
        "episodes": "" if movie else str(data.get("number_of_episodes") or ""),
        "genres": _genres(data, config),
        "director": ", ".join(dict.fromkeys(c["name"] for c in credits.get("crew") or [] if c.get("job") == "Director" and c.get("name"))),
        "creators": ", ".join(c["name"] for c in data.get("created_by") or [] if c.get("name")),
        "cast": "\n".join(f"{c['name']} – {c['character']}" if c.get("character") else c["name"] for c in cast),
        "overview": data.get("overview") or "",
        "tagline": data.get("tagline") or "",
        "tmdb_id": tmdb_ref(kind, data.get("id") or 0),
        "tmdb_url": f"{SITE}/{kind}/{int(data.get('id') or 0)}",
    }
    values = {fields[name]: value for name, value in texts.items() if fields.get(name)}

    base = slugify(title or original)[:60] or "tmdb"
    images = {}
    if fields.get("poster") and data.get("poster_path"):
        images[fields["poster"]] = [("w500", data["poster_path"], f"{base}-poster")]
    if fields.get("backdrop") and data.get("backdrop_path"):
        images[fields["backdrop"]] = [("w1280", data["backdrop_path"], f"{base}-backdrop")]
    if fields.get("images"):
        # Pictures without text first, then the best rated, without the backdrop already kept.
        backdrops = [b for b in (data.get("images") or {}).get("backdrops") or [] if b.get("file_path") and b["file_path"] != data.get("backdrop_path")]
        backdrops.sort(key=lambda b: (b.get("iso_639_1") is not None, -(b.get("vote_average") or 0)))
        images[fields["images"]] = [("w780", b["file_path"], f"{base}-{n + 1}") for n, b in enumerate(backdrops[:STILLS])]
    if fields.get("cast_photos"):
        images[fields["cast_photos"]] = [("w185", c["profile_path"], f"{n + 1:02d} {c['name']}")
                                         for n, c in enumerate(cast[:CAST_PHOTOS]) if c.get("profile_path")]
    return {"values": values, "images": {k: v for k, v in images.items() if v}}


def cast_list(text, photos):
    """[{"name", "role", "photo"}] for the cast strip: lines "Name – Rolle" and photos named "01 Name.jpg"."""
    by_name = {}
    for photo in photos:
        name = re.sub(r"^\d{2} ", "", photo["name"].rsplit(".", 1)[0])
        by_name.setdefault(name.casefold(), photo["url"])
    people = []
    for line in (text or "").splitlines():
        name, _sep, role = line.partition(" – ")
        if name.strip():
            people.append({"name": name.strip(), "role": role.strip(), "photo": by_name.get(name.strip().casefold(), ""),
                           "initials": "".join(part[:1] for part in name.split()[:2]).upper()})
    return people


# ---------------------------------------------------------------- filling a record

def find_record(table, kind, tmdb_id):
    from .models import Record

    key = (config_of(table).get("fields") or {}).get("tmdb_id")
    if not key:
        return None
    return Record.objects.filter(table=table, **{f"values__{key}": tmdb_ref(kind, tmdb_id)}).first()


def _choice_values(field, wanted):
    """Values of a choice that TMDB names but the field has no option for yet are added (genres TMDB adds)."""
    from .models import Field

    missing = [v for v in ([wanted] if isinstance(wanted, str) else wanted) if v and v not in field.option_values]
    if missing:
        field.options = [*field.options, *({"value": v[:100], "color": ""} for v in missing)]
        Field.objects.filter(pk=field.pk).update(options=field.options)


def fill(record, kind, data, user=None, pictures=True, lang=None):
    """Put a TMDB answer into the record: its values replace those TMDB gave before; rating, status, notes and
    viewings stay. Pictures replace the pictures of the same fields. Returns the problems with single pictures.
    `lang`: the TMDB language of `data`; fetched for a person, it becomes the table's language for refresh_due."""
    from core import fieldrules

    from . import links, services

    table = record.table
    config = config_of(table)
    mapped = map_details(kind, data, config)
    fields = {f.key: f for f in services.fields_of(table, user=user)}
    fixed = fieldrules.read_only(user, fieldrules.table_entity(table)) if user is not None else set()
    synced = fields.get((config.get("fields") or {}).get("synced") or "")
    if synced is not None and synced.type == "date":  # bookkeeping for refresh_due, not the person's content
        mapped["values"][synced.key] = timezone.localdate().isoformat()
        fixed.discard(synced.key)
    problems = []
    with transaction.atomic():
        if user is not None and lang and config.get("language") != lang:  # the next automatic fetch keeps it
            table.look = {**table.look, "tmdb": {**config, "language": lang}}
            table.save(update_fields=["look"])
        for key, raw in mapped["values"].items():
            field = fields.get(key)
            if field is None or key in fixed or not services.holds_value(field):
                continue
            if field.type in ("choice", "multichoice") and raw:
                _choice_values(field, raw)
            try:
                services._assign(record, field, raw if field.type == "multichoice" or not isinstance(raw, list) else ", ".join(raw))
            except ValidationError as exc:
                problems.extend(exc.messages)
        services.refresh(record, list(fields.values()))
        record.save()
    if not pictures:
        return problems
    from .models import Attachment

    for key, wanted in mapped["images"].items():
        field = fields.get(key)
        if field is None or field.type != "image" or key in fixed:
            continue
        got = []
        for size, path, name in wanted[: (99 if (field.config or {}).get("multiple") else 1)]:
            try:
                body, extension = image(size, path)
            except TmdbError as exc:
                problems.append(str(exc))
                continue
            got.append(ContentFile(body, name=f"{name}.{extension}"))
        if not got:
            continue
        with transaction.atomic():
            for old in Attachment.objects.filter(record=record, field=key):
                links.remove_file(old)
            for upload in got:
                upload.content_type = f"image/{'jpeg' if upload.name.endswith('jpg') else upload.name.rsplit('.', 1)[1]}"
                links.add_file(record, field, upload, user)
    return list(dict.fromkeys(problems))


def create(table, kind, tmdb_id, user=None, status=""):
    """A new record for this title (or the one that has it already): (record, created, problems)."""
    from .models import Record

    existing = find_record(table, kind, tmdb_id)
    if existing is not None:
        return existing, False, []
    data = details(kind, tmdb_id)
    record = Record(table=table)
    record.values = {}
    watch = config_of(table).get("watch") or {}
    if status in ("watchlist", "watched") and watch.get("status") and watch.get(status):
        record.values[watch["status"]] = watch[status]
    problems = fill(record, kind, data, user, lang=language())
    return record, True, problems


def add_viewing(record, user, day, how=""):
    """A viewing of the title in the table of viewings, and the title's status "watched"."""
    from . import links, services
    from .models import Table

    watch = config_of(record.table).get("watch") or {}
    plan = watch.get("viewings") or {}
    target = Table.objects.filter(key=plan.get("table") or "", archived=False).first()
    with transaction.atomic():
        if watch.get("status") and watch.get("watched"):
            field = next((f for f in services.fields_of(record.table) if f.key == watch["status"]), None)
            if field is not None:
                services.set_value(record, field, watch["watched"])
        if target is None or not services.may_edit(user, target):
            return None
        fields = {f.key: f for f in services.fields_of(target)}
        raw = {plan.get("date"): day.isoformat()}
        if how and plan.get("how") in fields:
            raw[plan["how"]] = how
        viewing = services.create_record(target, {k: v for k, v in raw.items() if k in fields})
        link = fields.get(plan.get("link") or "")
        if link is not None and link.type == "link":
            links.set_targets(viewing, link, [str(record.pk)])
            services.refresh(viewing)
            viewing.save()
        return viewing


def page_context(table, user, record=None):
    """What the list (record None) or a record page shows of TMDB and of "watched"; None for other tables."""
    from datetime import date

    from django.urls import reverse

    from . import services

    config = config_of(table)
    if not config:
        return None
    watch = config.get("watch") or {}
    context = {"search": supports(table), "watch": None, "how": how_choices(table) if watch else [],
               "can_edit": services.may_edit(user, table), "import_url": reverse("tmdb_import", args=[table.key])}
    if record is None:
        context["watch"] = bool(watch)
        return context
    values = record.values or {}
    ref = parse_ref(values.get((config.get("fields") or {}).get("tmdb_id") or "", ""))
    if ref is not None:
        context["refresh_url"] = reverse("tmdb_refresh", args=[table.key, record.pk])
        context["page_url"] = f"{SITE}/{ref[0]}/{ref[1]}"
    else:
        context["match_url"] = reverse("tmdb_refresh", args=[table.key, record.pk])
        context["match_title"] = _("Bei TMDB suchen und übernehmen")
    if watch.get("status"):
        context["watch"] = {"watched": values.get(watch["status"]) == watch.get("watched"), "today": date.today().isoformat(),
                            "url": reverse("record_watched", args=[table.key, record.pk])}
    return context


def cast_context(record, user):
    """The cast strip of a record page: [{"name", "role", "photo", "initials"}], or []."""
    from . import links, look, services

    fields = (config_of(record.table).get("fields") or {})
    visible = {f.key: f for f in services.fields_of(record.table, user=user)}
    text_field, photo_field = visible.get(fields.get("cast") or ""), visible.get(fields.get("cast_photos") or "")
    if text_field is None:
        return []
    photos = [{"name": a.name, "url": look.file_url(record.table, record.pk, a.pk)} for a in links.files(record, photo_field)] if photo_field else []
    return cast_list(str(services.display(record, text_field)), photos)


def how_choices(table):
    """[(value, label)] of the viewings' "how" field, for the quick form on the record page."""
    from . import services
    from .models import Table

    plan = (config_of(table).get("watch") or {}).get("viewings") or {}
    target = Table.objects.filter(key=plan.get("table") or "", archived=False).first()
    if target is None:
        return []
    field = next((f for f in services.fields_of(target) if f.key == plan.get("how")), None)
    return field.option_choices if field is not None and field.type == "choice" else []


# ---------------------------------------------------------------- six months at most

def _due(table, before):
    """[(day of the last fetch, record pk, record, (kind, id))] of the table's titles fetched before `before`
    (a title without a day counts as oldest)."""
    from .models import Record

    fields = config_of(table).get("fields") or {}
    id_key, synced_key = fields.get("tmdb_id"), fields.get("synced")
    if not id_key or not synced_key:
        return []
    due = []
    for record in Record.objects.filter(table=table).only("pk", "values", "table"):
        ref = parse_ref((record.values or {}).get(id_key) or "")
        day = (record.values or {}).get(synced_key) or ""
        if ref is not None and day < before:
            due.append((day, str(record.pk), record, ref))
    return due


def refresh_due(limit=REFRESH_BATCH, today=None):
    """Fetch the titles again whose TMDB data is REFRESH_AFTER days old: (refreshed, problems). Stops at the
    first problem with the key or the connection; the next round goes on. A title TMDB no longer knows keeps
    its values and is asked for again after REFRESH_AFTER days."""
    from core import audit

    from .models import Table

    if not stored_key():
        return 0, []
    before = ((today or timezone.localdate()) - timedelta(days=REFRESH_AFTER)).isoformat()
    work = []
    for table in Table.objects.filter(archived=False):
        if supports(table):
            lang = config_of(table).get("language")
            work.extend((day, pk, record, ref, lang) for day, pk, record, ref in _due(table, before))
    work.sort(key=lambda item: item[:2])  # oldest first, everywhere
    refreshed, problems = 0, []
    for _day, _pk, record, (kind, tmdb_id), lang in work[:limit]:
        try:
            fill(record, kind, details(kind, tmdb_id, lang=lang))
        except TmdbError as exc:
            if not exc.missing:  # the key, the connection or TMDB itself: the next round tries again
                problems.append(str(exc))
                break
            _mark_synced(record)
            problems.append(f"{record.title}: {exc}")
            continue
        refreshed += 1
    if refreshed:
        audit.log("table", f"TMDB: {refreshed} Titel automatisch neu abgeglichen")
    return refreshed, problems


def _mark_synced(record):
    """A title TMDB no longer has: wait REFRESH_AFTER days before asking again."""
    from . import services

    key = (config_of(record.table).get("fields") or {}).get("synced")
    field = next((f for f in services.fields_of(record.table) if f.key == key), None)
    if field is not None:
        services.set_value(record, field, timezone.localdate().isoformat())
