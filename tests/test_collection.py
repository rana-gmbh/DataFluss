"""A MyMovies collection into "Filme & Serien" (tables/collection.py), with TMDB mocked."""
import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from tables import collection, tmdb
from tables.models import Field, Record, Table
from transfer.models import ImportSession

from .base import AppTestCase
from .test_tmdb import FakeTmdb, FilmTemplateMixin


def title(**values):
    return {"media": "Movie", "local": "", "original": "", "year": "", "imdb": "", "rating": "-1", "added": "", **values}


COLLECTION = [
    title(local="Der Leuchtturm", original="The Lighthouse", year="2021", imdb="tt0000001", rating="8", added="03/07/2011 11:59:15 PM"),
    title(local="Der Leuchtturm (Blu-ray)", original="The Lighthouse", year="2021", imdb="tt0000001", rating="9", added="06/26/2021 09:16:36 AM"),
    title(media="TV Serie", local="Kapitäne: Die komplette erste Staffel", original="Captains: The Complete First Season", year="2019",
          imdb="tt0000002", rating="8", added="08/05/2013 10:00:00 AM"),
    title(media="TV Serie", local="Kapitäne: Staffel 2", original="Captains", year="2020", imdb="tt0000002", rating="7", added="09/30/2013 10:00:00 AM"),
    title(media="TV Serie", local="Kapitäne: Staffel 3", original="Captains", year="2021", imdb="tt0000002", rating="-1", added="10/13/2013 10:00:00 AM"),
    title(local="Unbekannt am See", original="Unknown by the Lake", year="2007", imdb="tt0000000", rating="4", added="05/09/2015 01:00:00 PM"),
]


class FakeWithFind(FakeTmdb):
    def __call__(self, url, headers=None, limit=None):
        path = url[len(tmdb.API):].split("?")[0]
        if path.startswith("/find/"):
            self.calls.append((url, headers or {}))
            answer = {"/find/tt0000001": {"movie_results": [{"id": 4711}]}, "/find/tt0000002": {"tv_results": [{"id": 815}]}}.get(path, {})
            return json.dumps(answer).encode(), "application/json"
        if path.startswith("/search/"):
            self.calls.append((url, headers or {}))
            return json.dumps({"results": []}).encode(), "application/json"
        return super().__call__(url, headers, limit)


class GroupTests(SimpleTestCase):
    def test_copies_seasons_and_titles_without_imdb(self):
        groups = {g["imdb"] or g["title"]: g for g in collection.group(COLLECTION)}
        self.assertEqual(len(groups), 3)
        film = groups["tt0000001"]
        self.assertEqual((film["days"], film["stars"], film["tv"]), (["2011-03-07", "2021-06-26"], ["4", "4.5"], False))
        series = groups["tt0000002"]
        self.assertEqual((series["tv"], series["year"], len(series["days"]), series["stars"]), (True, "2019", 3, ["4", "3.5"]))
        self.assertEqual(groups["Unbekannt am See"]["imdb"], "")  # tt0000000 is no id
        self.assertEqual(collection.summary(list(groups.values()), len(COLLECTION)),
                         {"entries": 6, "titles": 3, "series": 1, "merged": 2, "without_imdb": 1})

    def test_stars_in_halves_and_series_names(self):
        self.assertEqual(collection.rating_of({"stars": ["4", "3.5", "3.5"]}), Decimal("3.5"))  # 3.67 -> 3.5
        self.assertEqual(collection.rating_of({"stars": ["4", "3.5"]}), Decimal("4"))  # 3.75 -> 4
        self.assertIsNone(collection.rating_of({"stars": []}))
        for name in ("Vikings: The Complete First Season", "Vikings: Season 4 - Volume 1", "Vikings - Staffel 2", "Vikings: Seizoen 7"):
            self.assertEqual(collection.series_name(name), "Vikings")
        self.assertEqual(collection.series_name("Stand By Me: Das Geheimnis eines Sommers"), "Stand By Me: Das Geheimnis eines Sommers")


class ImportTests(FilmTemplateMixin, AppTestCase):
    def setUp(self):
        self.apply()
        self.login(self.it)
        self.fake = FakeWithFind()
        patcher = mock.patch.object(tmdb, "_open", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_import(self, titles=COLLECTION):
        response = self.client.post(reverse("collection", args=["fs_titel"]), json.dumps({"filename": "Export.xml", "titles": titles}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        url = response.json()["url"]
        self.assertContains(self.client.get(url), "6 Einträge, daraus 3 Titel")
        session = ImportSession.objects.latest("created_at")
        for _round in range(10):
            body = self.client.post(reverse("collection_step", args=["fs_titel", session.pk])).json()
            if body["finished"]:
                return body
        self.fail("the import did not finish")

    def viewing_days(self, record):
        return sorted(r.values["f_datum"] for r in Record.objects.filter(table=self.viewings_table, links__target_id=record.pk))

    @property
    def viewings_table(self):
        return Table.objects.get(key="fs_sichtungen")

    def test_a_collection_comes_over(self):
        body = self.run_import()
        self.assertEqual((body["created"], body["existing"], body["unmatched"]), (2, 0, ["Unbekannt am See"]))
        film = Record.objects.get(table=self.films, values__f_tmdb_id="movie/4711")
        self.assertEqual((film.values["f_bewertung"], film.values["f_status"]), ("4.5", "Gesehen"))  # 4 and 4.5 -> 4.25 -> 4.5
        self.assertEqual(self.viewing_days(film), ["2011-03-07", "2021-06-26"])
        series = Record.objects.get(table=self.films, values__f_tmdb_id="tv/815")
        self.assertEqual(series.values["f_bewertung"], "4.0")  # 4 and 3.5 -> 3.75 -> 4
        self.assertEqual(self.viewing_days(series), ["2013-10-13"])  # one viewing, the last season's day
        plain = Record.objects.get(table=self.films, title="Unbekannt am See")
        self.assertEqual((plain.values["f_originaltitel"], plain.values["f_jahr"], plain.values["f_bewertung"]), ("Unknown by the Lake", 2007, "2.0"))
        self.assertEqual(self.viewing_days(plain), ["2015-05-09"])
        self.assertEqual(ImportSession.objects.get().rows, [])  # what the file held is not kept

    def test_twice_adds_nothing_and_keeps_ones_own_stars(self):
        self.run_import()
        film = Record.objects.get(table=self.films, values__f_tmdb_id="movie/4711")
        from tables import services

        services.set_value(film, Field.objects.get(table=self.films, key="f_bewertung"), "2")
        body = self.run_import()
        self.assertEqual((body["created"], body["existing"]), (0, 2))
        self.assertEqual(Record.objects.filter(table=self.films).count(), 3)
        self.assertEqual(Record.objects.filter(table=self.viewings_table).count(), 4)
        film.refresh_from_db()
        self.assertEqual(film.values["f_bewertung"], "2.0")

    def test_tmdb_refusing_pauses_and_the_next_step_goes_on(self):
        response = self.client.post(reverse("collection", args=["fs_titel"]), json.dumps({"filename": "x.xml", "titles": COLLECTION}),
                                    content_type="application/json")
        session = ImportSession.objects.latest("created_at")
        self.fake.status = 401
        body = self.client.post(reverse("collection_step", args=["fs_titel", session.pk])).json()
        self.assertEqual((body["done"], body["finished"]), (0, False))
        self.assertIn("Schlüssel", body["paused"])
        self.fake.status = None
        self.assertTrue(self.client.post(reverse("collection_step", args=["fs_titel", session.pk])).json()["done"] >= 1)
        self.assertEqual(response.status_code, 200)

    def test_one_step_at_a_time(self):
        session = collection.start(self.films, self.it, COLLECTION, "x.xml")
        ImportSession.objects.filter(pk=session.pk).update(report={**session.report, "busy_until": 9e12})
        body = self.client.post(reverse("collection_step", args=["fs_titel", session.pk])).json()
        self.assertTrue(body["busy"])
        self.assertFalse(Record.objects.filter(table=self.films).exists())

    def test_who_may_only_read_cannot_and_others_see_no_session(self):
        session = collection.start(self.films, self.it, COLLECTION, "x.xml")
        self.login(self.gast_user())
        self.assertIn(self.client.get(reverse("collection", args=["fs_titel"])).status_code, (403, 404))
        self.assertIn(self.client.post(reverse("collection_step", args=["fs_titel", session.pk])).status_code, (403, 404))

    def test_a_file_without_titles(self):
        response = self.client.post(reverse("collection", args=["fs_titel"]), json.dumps({"titles": [{"local": ""}]}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse("collection", args=["fs_titel"]), b"kein json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def gast_user(self):
        from core.models import User

        return User.objects.create_user("leser", password="ein-langes-passwort", role="gast")
