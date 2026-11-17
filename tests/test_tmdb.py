"""The template "Filme & Serien", its TMDB import (tables/tmdb.py, HTTP mocked) and the look of tables (tables/look.py)."""
import copy
import io
import json
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from PIL import Image

from core.context_user import set_current_user
from core.models import Role, SchoolSettings, User
from tables import blueprints, formula, look, services, tmdb
from tables.models import Attachment, Field, Record, Table
from tables.views_records import rows

from .base import AppTestCase

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tmdb"
TEMPLATE = blueprints._read(blueprints.LIBRARY / "filme.json")
CONFIG = TEMPLATE["tables"][0]["look"]["tmdb"]
V3 = "0123456789abcdef0123456789abcdef"
V4 = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0ZXN0In0.c2lnbmF0dXJl"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def picture():
    out = io.BytesIO()
    Image.new("RGB", (4, 6), (200, 30, 30)).save(out, "JPEG")
    return out.getvalue()


class FakeTmdb:
    """Stands in for tables/tmdb.py _open: answers from the fixtures, records what was asked."""

    def __init__(self, status=None):
        self.calls = []
        self.status = status

    def __call__(self, url, headers=None, limit=None):
        self.calls.append((url, headers or {}))
        if self.status:
            raise tmdb.TmdbError("TMDB lehnt den Schlüssel ab.", rejected=True)
        if url.startswith(tmdb.IMAGES):
            return picture(), "image/jpeg"
        path = url[len(tmdb.API):].split("?")[0]
        if path.startswith("/person/"):
            number = int(path.rsplit("/", 1)[1])
            return json.dumps({"id": number, "name": f"Neu {number}", "profile_path": "/neu.jpg"}).encode(), "application/json"
        answer = {"/authentication": {"success": True}, "/search/multi": fixture("search"), "/movie/4711": fixture("movie_4711"),
                  "/tv/815": fixture("tv_815")}.get(path)
        if answer is None:
            raise tmdb.TmdbError("Diesen Titel gibt es bei TMDB nicht (mehr).", missing=True)
        return json.dumps(answer).encode(), "application/json"


class KeyTests(SimpleTestCase):
    def test_both_kinds_of_key_are_taken_as_pasted(self):
        self.assertEqual(tmdb.clean_key(f'  "{V3}" '), V3)
        self.assertEqual(tmdb.clean_key(f"Bearer {V4}\n"), V4)
        self.assertEqual(tmdb.kind_of_key(V3), "api_key")
        self.assertEqual(tmdb.kind_of_key(V4), "token")
        for wrong in ("", "abc", "x" * 32, "eyJnurEinTeil"):
            with self.assertRaises(ValidationError):
                tmdb.clean_key(wrong)

    def test_a_v3_key_goes_into_the_query_and_a_token_into_the_header(self):
        fake = FakeTmdb()
        with mock.patch.object(tmdb, "_open", fake):
            tmdb.call("/authentication", key=V3)
            tmdb.call("/authentication", key=V4)
        (first, first_headers), (second, second_headers) = fake.calls
        self.assertIn(f"api_key={V3}", first)
        self.assertNotIn("Authorization", first_headers)
        self.assertNotIn("api_key", second)
        self.assertEqual(second_headers["Authorization"], f"Bearer {V4}")

    def test_problems_are_named(self):
        with mock.patch.object(tmdb, "_open", FakeTmdb(status=401)):
            self.assertEqual(tmdb.test_key(V3)[:2], (False, True))
        with mock.patch.object(tmdb, "_open", FakeTmdb()):
            self.assertEqual(tmdb.test_key(V3), (True, False, ""))
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            accepted, rejected, problem = tmdb.test_key(V3)
        self.assertFalse(accepted or rejected)
        self.assertIn("Internet", problem)

    def test_picture_paths_are_checked(self):
        for bad in ("../x.jpg", "/a/b.jpg", "/x.svg", "x.jpg", "/" + "a" * 90 + ".jpg"):
            with self.assertRaises(tmdb.TmdbError):
                tmdb.image("w92", bad)


class MappingTests(SimpleTestCase):
    def test_a_film(self):
        mapped = tmdb.map_details("movie", fixture("movie_4711"), CONFIG)
        values = mapped["values"]
        self.assertEqual(values["f_titel"], "Der Leuchtturm am Ende der Welt")
        self.assertEqual(values["f_originaltitel"], "The Lighthouse at World's End")
        self.assertEqual((values["f_art"], values["f_jahr"], values["f_laufzeit"]), ("Film", "2021", "118"))
        self.assertEqual(values["f_genres"], ["Drama", "Mystery", "Seefahrt"])  # an unknown genre comes with its name
        self.assertEqual(values["f_regie"], "Ada Regie")
        self.assertEqual(values["f_staffeln"], "")
        lines = values["f_besetzung"].splitlines()
        self.assertEqual(len(lines), tmdb.CAST)
        self.assertEqual(lines[0], "Person 1 – Rolle 1")  # in TMDB's order, not the file's
        self.assertEqual((values["f_tmdb_id"], values["f_tmdb_link"]), ("movie/4711", "https://www.themoviedb.org/movie/4711"))
        images = mapped["images"]
        self.assertEqual(images["f_poster"], [("w500", "/poster4711.jpg", "der-leuchtturm-am-ende-der-welt-poster")])
        self.assertEqual(images["f_hintergrund"][0][:2], ("w1280", "/backdrop4711.jpg"))
        # without the backdrop, pictures without text first, then the best rated
        self.assertEqual([path for _size, path, _name in images["f_bilder"]], ["/still2.jpg", "/still3.jpg", "/still1.jpg", "/still5.jpg"])
        photos = images["f_besetzungsfotos"]
        self.assertTrue(all(size == "w185" for size, _path, _name in photos))
        self.assertEqual(photos[0][2], "01 Person 1")
        self.assertNotIn("03 Person 3", [name for *_rest, name in photos])  # no photo at TMDB

    def test_a_series(self):
        values = tmdb.map_details("tv", fixture("tv_815"), CONFIG)["values"]
        self.assertEqual((values["f_art"], values["f_jahr"], values["f_laufzeit"]), ("Serie", "2019", "45"))
        self.assertEqual((values["f_staffeln"], values["f_episoden"]), ("3", "30"))
        self.assertEqual(values["f_erschaffen"], "Clara Idee, Dora Buch")
        self.assertEqual(values["f_genres"], ["Action", "Abenteuer", "Krimi"])
        self.assertEqual(values["f_originaltitel"], "")  # the same as the title
        self.assertEqual(values["f_regie"], "")
        # all seasons (aggregate credits), each with the role of most episodes
        self.assertEqual(values["f_besetzung"], "Emil Wache – Kapitän\nFritzi Fern – Lotsin")

    def test_the_cast_strip_pairs_names_and_photos(self):
        people = tmdb.cast_list("Ada – Kapitänin\nBert\n", [{"name": "01 Ada.jpg", "url": "/a"}])
        self.assertEqual(people[0], {"name": "Ada", "role": "Kapitänin", "photo": "/a", "initials": "A"})
        self.assertEqual((people[1]["role"], people[1]["photo"]), ("", ""))


class FilmTemplateMixin:
    def apply(self):
        set_current_user(self.it)
        blueprints.apply(TEMPLATE, self.it)
        self.films, self.viewings = Table.objects.get(key="fs_titel"), Table.objects.get(key="fs_sichtungen")
        tmdb.save_key(V3)


class TemplateTests(FilmTemplateMixin, AppTestCase):
    def test_the_template_is_valid_and_complete(self):
        self.assertEqual(blueprints.check(TEMPLATE), [])
        self.apply()
        self.assertEqual((self.films.section, self.viewings.section), ("Filme & Serien", "Filme & Serien"))
        self.assertFalse(self.viewings.in_nav)
        self.assertEqual(self.films.look["cover"], "f_poster")
        self.assertTrue(tmdb.supports(self.films))
        kinds = {f.key: f.type for f in services.fields_of(self.films)}
        self.assertEqual((kinds["f_bewertung"], kinds["f_sichtungen"], kinds["f_zuletzt"]), ("rating", "backlink", "formula"))
        self.assertEqual(Field.objects.get(table=self.films, key="f_zuletzt").config["result"], "date")
        again = blueprints.apply(TEMPLATE, self.it)
        self.assertEqual(again["created"], [])

    def test_version_2_adds_the_people_to_a_database_with_version_1(self):
        first = copy.deepcopy(TEMPLATE)
        first["version"] = 1
        first["tables"] = [t for t in first["tables"] if t["key"] not in ("fs_darsteller", "fs_regie")]
        first["tables"][0]["fields"] = [f for f in first["tables"][0]["fields"] if f["key"] not in ("f_darsteller", "f_regisseure")]
        del first["tables"][0]["look"]["tmdb"]["people"]
        first["masks"] = [m for m in first["masks"] if m["table"] in ("fs_titel", "fs_sichtungen")]
        first["views"] = [v for v in first["views"] if v["table"] in ("fs_titel", "fs_sichtungen")]
        for tab in first["masks"][0]["tree"]["tabs"]:
            for section in tab["sections"]:
                section["items"] = [i for i in section["items"] if i.get("field") not in ("f_darsteller", "f_regisseure")]
        set_current_user(self.it)
        blueprints.apply(first, self.it)
        films = Table.objects.get(key="fs_titel")
        films.look = {**films.look, "cover": "", "tmdb": {**films.look["tmdb"], "language": "en-US"}}
        films.save()
        report = blueprints.apply(TEMPLATE, self.it)
        self.assertIn("Darsteller", report["created"])
        films.refresh_from_db()
        self.assertEqual(films.look["tmdb"]["people"]["cast"], {"table": "fs_darsteller", "link": "f_darsteller"})
        self.assertEqual((films.look["cover"], films.look["tmdb"]["language"]), ("", "en-US"))  # what was set here stays
        self.assertEqual(blueprints.installed_version("filme"), 2)

    def test_it_works_without_schulverwaltung(self):
        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(modules_off=["schulverwaltung"])
        self.assertTrue(blueprints.usable(TEMPLATE))
        self.apply()
        self.login(self.it)
        page = self.client.get(reverse("table_blueprints"))
        self.assertContains(page, "Filme &amp; Serien")
        self.assertEqual(self.client.get(reverse("table_records", args=["fs_titel"])).status_code, 200)

    def test_a_database_of_its_own(self):
        """Offered in an empty database, never in a school database (tables/blueprints.py offered)."""
        self.login(self.it)
        self.assertNotContains(self.client.get(reverse("table_blueprints")), "Filme &amp; Serien")
        self.assertNotContains(self.client.get(reverse("table_list")), "TMDB: Filme und Serien")
        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(modules_off=["schulverwaltung"])
        self.assertContains(self.client.get(reverse("table_blueprints")), "Filme &amp; Serien")


class ImportTests(FilmTemplateMixin, AppTestCase):
    def setUp(self):
        self.apply()
        self.login(self.it)
        self.fake = FakeTmdb()
        patcher = mock.patch.object(tmdb, "_open", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def take_over(self, **extra):
        return self.client.post(reverse("tmdb_import", args=["fs_titel"]), {"kind": "movie", "id": "4711", **extra})

    def test_a_film_with_its_pictures(self):
        response = self.take_over(status="watched", viewing="1", how="Kino")
        record = Record.objects.get(table=self.films)
        self.assertRedirects(response, reverse("record_detail", args=["fs_titel", record.pk]), fetch_redirect_response=False)
        self.assertEqual(record.title, "Der Leuchtturm am Ende der Welt")
        self.assertEqual(record.values["f_jahr"], 2021)
        self.assertEqual(record.values["f_status"], "Gesehen")
        self.assertEqual(record.values["f_genres"], ["Drama", "Mystery", "Seefahrt"])
        self.assertIn("Seefahrt", Field.objects.get(table=self.films, key="f_genres").option_values)
        counts = {key: Attachment.objects.filter(record=record, field=key).count() for key in ("f_poster", "f_hintergrund", "f_bilder", "f_besetzungsfotos")}
        self.assertEqual(counts, {"f_poster": 1, "f_hintergrund": 1, "f_bilder": 4, "f_besetzungsfotos": 6})
        viewing = Record.objects.get(table=self.viewings)
        self.assertEqual(viewing.values, {"f_datum": date.today().isoformat(), "f_wie": "Kino"})
        field = Field.objects.get(table=self.films, key="f_zuletzt")
        self.assertEqual(formula.display(record, field)[0], date.today().strftime("%d.%m.%Y"))
        page = self.client.get(reverse("record_detail", args=["fs_titel", record.pk]))
        self.assertContains(page, "record-hero")
        self.assertContains(page, "Person 1")  # the cast strip
        self.assertContains(page, "Nochmal gesehen")
        self.assertContains(page, "Mit TMDB aktualisieren")

    def test_the_same_title_twice_opens_the_first(self):
        self.take_over()
        self.take_over()
        self.assertEqual(Record.objects.filter(table=self.films).count(), 1)

    def test_a_watchlist_entry_and_watching_it_later(self):
        self.take_over(status="watchlist")
        record = Record.objects.get(table=self.films)
        self.assertEqual(record.values["f_status"], "Merkliste")
        self.assertFalse(Record.objects.filter(table=self.viewings).exists())
        url = reverse("record_watched", args=["fs_titel", record.pk])
        self.client.post(url, {"date": "2026-11-01", "how": "Netflix"})
        self.client.post(url, {"date": "2026-11-15", "how": "erfunden"})
        record.refresh_from_db()
        self.assertEqual(record.values["f_status"], "Gesehen")
        self.assertEqual(sorted(r.values.get("f_wie", "") for r in Record.objects.filter(table=self.viewings)), ["", "Netflix"])
        self.assertEqual(formula.display(record, Field.objects.get(table=self.films, key="f_zuletzt"))[0], "15.11.2026")
        self.assertEqual(formula.display(record, Field.objects.get(table=self.films, key="f_anzahl"))[0], "2")

    def test_an_update_keeps_what_is_ones_own(self):
        self.take_over()
        record = Record.objects.get(table=self.films)
        services.set_value(record, Field.objects.get(table=self.films, key="f_bewertung"), "4,5")
        services.set_value(record, Field.objects.get(table=self.films, key="f_handlung"), "Alt")
        self.client.post(reverse("tmdb_refresh", args=["fs_titel", record.pk]))
        record.refresh_from_db()
        self.assertEqual(record.values["f_bewertung"], "4.5")
        self.assertTrue(record.values["f_handlung"].startswith("Eine Leuchtturmwärterin"))
        self.assertEqual(Attachment.objects.filter(record=record, field="f_poster").count(), 1)

    def test_a_record_typed_by_hand_is_matched(self):
        record = services.create_record(self.films, {"f_titel": "Leuchtturm"})
        self.client.post(reverse("tmdb_refresh", args=["fs_titel", record.pk]), {"kind": "movie", "id": "4711"})
        record.refresh_from_db()
        self.assertEqual(record.values["f_tmdb_id"], "movie/4711")

    def test_search_names_what_is_there_already(self):
        self.take_over()
        page = self.client.get(reverse("tmdb_search", args=["fs_titel"]), {"q": "Leuchtturm"})
        self.assertContains(page, "schon in der Tabelle", count=1)
        self.assertContains(page, "Die Küstenwache")
        self.assertNotContains(page, "Jemand")  # people are not offered
        self.assertContains(page, reverse("tmdb_thumb", args=["fs_titel", "w92", "poster4711.jpg"]))
        thumb = self.client.get(reverse("tmdb_thumb", args=["fs_titel", "w92", "poster4711.jpg"]))
        self.assertEqual(thumb["Content-Type"], "image/jpeg")
        self.assertEqual(self.client.get(reverse("tmdb_thumb", args=["fs_titel", "original", "poster4711.jpg"])).status_code, 404)

    def test_without_a_key_the_dialog_says_how_to_get_one(self):
        tmdb.save_key("")
        page = self.client.get(reverse("tmdb_search", args=["fs_titel"]), {"q": "Leuchtturm"})
        self.assertContains(page, reverse("tables_tmdb"))
        self.assertEqual(self.fake.calls, [])

    def test_who_may_only_read_cannot_take_over(self):
        guest = User.objects.create_user("gast", password="x" * 14, role="gast")
        self.films.rights = {"gast": "read"}
        self.films.save()
        self.login(guest)
        self.assertEqual(self.take_over().status_code, 403)
        self.assertEqual(self.client.get(reverse("tmdb_search", args=["fs_titel"]), {"q": "x"}).status_code, 403)
        self.assertFalse(Record.objects.filter(table=self.films).exists())

    def test_the_people_of_a_film_get_records_of_their_own(self):
        self.take_over(status="watched", viewing="1")
        film = Record.objects.get(table=self.films)
        actors, directors = Table.objects.get(key="fs_darsteller"), Table.objects.get(key="fs_regie")
        self.assertEqual(Record.objects.filter(table=actors).count(), 10)  # CAST, the first ten in TMDB's order
        ada = Record.objects.get(table=directors)
        self.assertEqual((ada.title, ada.values["f_tmdb_id"], ada.values["f_tmdb_abgleich"]), ("Ada Regie", "person/2001", date.today().isoformat()))
        self.assertEqual(Attachment.objects.filter(record=ada, field="f_foto").count(), 1)
        first = Record.objects.get(table=actors, values__f_tmdb_id="person/1001")
        self.assertEqual(Attachment.objects.filter(record=first, field="f_foto").count(), 1)
        third = Record.objects.get(table=actors, values__f_tmdb_id="person/1003")
        self.assertEqual(Attachment.objects.filter(record=third, field="f_foto").count(), 0)  # no photo at TMDB
        values = {r["name"]: r for r in rows(actors, self.it)}["Person 1"]
        self.assertEqual((values["f_titel_anzahl"], values["f_gesehen"]), (1.0, 1.0))
        page = self.client.get(reverse("record_detail", args=["fs_regie", ada.pk]))
        self.assertContains(page, film.title)  # the films of the person, from the other side
        film_page = self.client.get(reverse("record_detail", args=["fs_titel", film.pk]))
        self.assertContains(film_page, reverse("record_detail", args=["fs_darsteller", first.pk]))  # the cast strip links
        # Again from TMDB: the same people, no second record of anyone
        self.client.post(reverse("tmdb_refresh", args=["fs_titel", film.pk]))
        self.assertEqual(Record.objects.filter(table=actors).count(), 10)
        self.assertEqual(Record.objects.filter(table=directors).count(), 1)

    def test_people_of_a_series_and_photos_fetched_once(self):
        self.client.post(reverse("tmdb_import", args=["fs_titel"]), {"kind": "tv", "id": "815"})
        self.assertEqual(sorted(r.title for r in Record.objects.filter(table__key="fs_regie")), ["Clara Idee", "Dora Buch"])
        self.assertEqual(sorted(r.title for r in Record.objects.filter(table__key="fs_darsteller")), ["Emil Wache", "Fritzi Fern"])
        emil = [url for url, _h in self.fake.calls if url.endswith("/emil.jpg")]
        self.assertEqual(len(emil), 1)  # for the title's cast photos and Emil's own record

    def test_who_may_not_edit_the_people_gets_the_film_without_them(self):
        actors = Table.objects.get(key="fs_darsteller")
        actors.rights = {**actors.rights, "it": "read"}
        actors.save()
        with mock.patch.object(services, "may_edit", side_effect=lambda user, table: table.key != "fs_darsteller"):
            self.take_over()
        self.assertTrue(Record.objects.filter(table=self.films).exists())
        self.assertFalse(Record.objects.filter(table=actors).exists())
        self.assertTrue(Record.objects.filter(table__key="fs_regie").exists())

    def test_other_tables_have_no_tmdb(self):
        other = services.create_table("Bücher")
        self.assertEqual(self.client.get(reverse("tmdb_search", args=[other.key]), {"q": "x"}).status_code, 404)

    def test_the_list_offers_the_gallery(self):
        self.take_over()
        page = self.client.get(reverse("table_records", args=["fs_titel"]))
        self.assertContains(page, "Aus TMDB hinzufügen")
        self.assertContains(page, 'id="gallery"')
        rows = self.client.get(reverse("table_rows", args=["fs_titel"])).json()
        self.assertTrue(rows[0]["_cover"].startswith("/tabellen/fs_titel/"))
        self.assertEqual(self.client.get(rows[0]["_cover"]).status_code, 200)


class RefreshTests(FilmTemplateMixin, AppTestCase):
    """TMDB's terms: no data older than six months. tables/tmdb.py refresh_due, in the nightly work."""

    def setUp(self):
        self.apply()
        self.fake = FakeTmdb()
        patcher = mock.patch.object(tmdb, "_open", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.later = date.today() + timedelta(days=tmdb.REFRESH_AFTER + 1)

    def title(self, kind="movie", tmdb_id=4711):
        record, _created, _problems = tmdb.create(self.films, kind, tmdb_id, self.it)
        return record

    def details_asked(self):
        return [url for url, _headers in self.fake.calls if "append_to_response" in url]

    def test_taking_over_notes_the_day_and_the_language(self):
        record = self.title()
        self.assertEqual(record.values["f_tmdb_abgleich"], date.today().isoformat())
        self.films.refresh_from_db()
        self.assertEqual(self.films.look["tmdb"]["language"], "de-DE")

    def test_only_titles_fetched_long_ago_come_again(self):
        film, series = self.title(), self.title("tv", 815)
        services.set_value(film, Field.objects.get(table=self.films, key="f_handlung"), "Von Hand geändert")
        self.fake.calls.clear()
        self.assertEqual(tmdb.refresh_due(), (0, []))
        self.assertEqual(self.fake.calls, [])
        refreshed, problems = tmdb.refresh_due(today=self.later)
        self.assertEqual((refreshed, problems), (2 + 11 + 4, []))  # two titles, their people (10 + 1, 2 + 2)
        film.refresh_from_db()
        self.assertTrue(film.values["f_handlung"].startswith("Eine Leuchtturmwärterin"))
        self.assertEqual(film.values["f_tmdb_abgleich"], date.today().isoformat())
        self.assertEqual(Attachment.objects.filter(record=film, field="f_poster").count(), 1)
        self.assertEqual(len(self.details_asked()), 2)

    def test_the_oldest_first_and_a_few_at_a_time(self):
        film, series = self.title(), self.title("tv", 815)
        Record.objects.filter(pk=series.pk).update(values={**series.values, "f_tmdb_abgleich": ""})  # never noted
        self.fake.calls.clear()
        self.assertEqual(tmdb.refresh_due(limit=1, today=self.later), (1, []))
        self.assertEqual(len(self.details_asked()), 1)
        self.assertIn("/tv/815", self.details_asked()[0])

    def test_in_the_language_of_the_last_fetch(self):
        self.title()
        self.films.look = {**self.films.look, "tmdb": {**self.films.look["tmdb"], "language": "en-US"}}
        self.films.save()
        self.fake.calls.clear()
        tmdb.refresh_due(today=self.later)
        self.assertIn("language=en-US", self.details_asked()[0])

    def test_a_refused_key_stops_the_round(self):
        self.title(), self.title("tv", 815)
        self.fake.status = 401
        self.fake.calls.clear()
        refreshed, problems = tmdb.refresh_due(today=self.later)
        self.assertEqual((refreshed, len(problems), len(self.fake.calls)), (0, 1, 1))

    def test_a_title_tmdb_lost_waits_for_the_next_turn(self):
        lost = services.create_record(self.films, {"f_titel": "Verschollen", "f_tmdb_id": "movie/999"})
        refreshed, problems = tmdb.refresh_due()
        self.assertEqual(refreshed, 0)
        self.assertIn("Verschollen", problems[0])
        lost.refresh_from_db()
        self.assertEqual((lost.values["f_titel"], lost.values["f_tmdb_abgleich"]), ("Verschollen", date.today().isoformat()))
        self.fake.calls.clear()
        tmdb.refresh_due()
        self.assertEqual(self.fake.calls, [])

    def test_without_a_key_nothing_happens(self):
        self.title()
        tmdb.save_key("")
        self.fake.calls.clear()
        self.assertEqual(tmdb.refresh_due(today=self.later), (0, []))
        self.assertEqual(self.fake.calls, [])

    def test_it_is_part_of_the_nightly_work(self):
        from archive import nightly

        with mock.patch.object(tmdb, "refresh_due", side_effect=RuntimeError("kaputt")) as refresh, self.assertLogs("tables.apps", "ERROR"):
            nightly.empty_bins()  # a failure here must not stop the bins and the backup
        refresh.assert_called_once()


class KeySettingsTests(AppTestCase):
    def setUp(self):
        self.fake = FakeTmdb()
        patcher = mock.patch.object(tmdb, "_open", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_key_is_kept_encrypted_and_sent_to_computers(self):
        from sync.server import _school_payload

        self.login(self.it)
        self.assertContains(self.client.get(reverse("tables_tmdb")), "https://www.themoviedb.org/settings/api")
        self.assertContains(self.client.post(reverse("tables_tmdb_test"), {"key": V4}), "Lesezugriffs-Token funktioniert")
        self.client.post(reverse("tables_tmdb"), {"key": V3})
        stored = SchoolSettings.load().tmdb_key
        self.assertTrue(stored.startswith("v1:"))
        self.assertNotIn(V3, stored)
        self.assertEqual(tmdb.stored_key(), V3)
        self.assertEqual(_school_payload()["tmdb_key"], stored)
        self.assertContains(self.client.get(reverse("tables_tmdb")), "…cdef")
        self.client.post(reverse("tables_tmdb"), {"action": "remove"})
        self.assertEqual(tmdb.stored_key(), "")

    def test_a_refused_key_is_not_kept(self):
        self.login(self.it)
        with mock.patch.object(tmdb, "_open", FakeTmdb(status=401)):
            self.client.post(reverse("tables_tmdb"), {"key": V3})
        self.assertEqual(tmdb.stored_key(), "")

    def test_only_designers(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("tables_tmdb")).status_code, 403)


class LookTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        self.table = services.create_table("Geräte")
        self.photo = services.create_field(self.table, "Foto", "image")
        self.state = services.create_field(self.table, "Zustand", "choice", "Gut\nDefekt")
        self.login(self.it)

    def test_the_settings_form(self):
        url = reverse("table_settings", args=[self.table.key]) + "?tab=allgemein"
        self.assertContains(self.client.get(url), "Darstellung")
        self.client.post(url, {"action": "look", "cover": self.photo.key, "subtitle": [self.state.key, "", ""], "flag": self.state.key,
                               "layout": "gallery", "shape": "wide"})
        self.table.refresh_from_db()
        self.assertEqual(self.table.look, {"cover": self.photo.key, "flag": self.state.key, "subtitle": [self.state.key], "layout": "gallery", "shape": "wide"})
        self.client.post(url, {"action": "look", "cover": self.state.key})  # not a picture
        self.table.refresh_from_db()
        self.assertEqual(self.table.look["cover"], self.photo.key)

    def test_a_cover_hidden_for_the_role_is_left_out(self):
        self.table.look = {"cover": self.photo.key, "layout": "gallery"}
        self.table.save()
        self.assertEqual(look.grid_config(self.table, self.it)["layout"], "gallery")
        with mock.patch("core.fieldrules.hidden", return_value={self.photo.key}):
            self.assertIsNone(look.grid_config(self.table, self.it))

    def test_without_a_picture_the_record_page_stays_as_it_was(self):
        self.table.look = {"cover": self.photo.key}
        self.table.save()
        record = services.create_record(self.table, {"f_name": "Beamer"})
        page = self.client.get(reverse("record_detail", args=[self.table.key, record.pk]))
        self.assertNotContains(page, "record-hero")
