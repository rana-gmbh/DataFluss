"""Templates from datafluss.de and from files (tables/catalog.py, docs/tables.md "Von datafluss.de")."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.context_user import set_current_user
from core.models import SchoolSettings
from tables import blueprints, catalog
from tables.models import CatalogTemplate, Table

from .base import AppTestCase

BASE = "https://vorlagen.example"
INVENTORY = json.loads((blueprints.LIBRARY / "inventar.json").read_text(encoding="utf-8"))


def template(version=1, **changes):
    data = {**copy.deepcopy(INVENTORY), "id": "mein_inventar", "title": "Mein Inventar", "version": version, **changes}
    data.pop("order", None)
    return data


class FakeCatalog:
    """Answers _open like datafluss.de would: an index and the files it names."""

    def __init__(self, *templates):
        self.files, self.entries, self.calls = {}, [], []
        for data in templates:
            self.add(data)

    def add(self, data, **entry):
        raw = json.dumps(data).encode()
        url = f"{BASE}/api/v1/vorlagen/{data['id']}/{data['version']}.json"
        self.files[url] = raw
        self.entries = [e for e in self.entries if e["id"] != data["id"]] + [{
            "id": data["id"], "version": data["version"], "title": data["title"], "author": "Anna", "license": "CC-BY-4.0",
            "rating": 4.5, "ratings": 2, "downloads": 7, "has_demo": bool(data.get("demo")), "tables": ["Räume"],
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "url": url, "page": f"{BASE}/vorlagen/{data['id']}/",
            "changes": data.get("changes") or {}, **entry}]

    def __call__(self, url, limit):
        self.calls.append(url)
        if not catalog._allowed(url):
            raise catalog.CatalogError("fremd")
        if url.endswith("/api/v1/vorlagen.json"):
            return json.dumps({"format": 1, "templates": self.entries}).encode()
        if url in self.files:
            return self.files[url]
        raise catalog.CatalogError("Diese Vorlage gibt es im Katalog nicht (mehr).")


@override_settings(TEMPLATE_CATALOG_URL=BASE)
class CatalogCase(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        cache.clear()
        SchoolSettings.objects.filter(pk=1).update(modules_off=["schulverwaltung"])  # a database without Schulverwaltung
        self.fake = FakeCatalog(template())
        patcher = mock.patch.object(catalog, "_open", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class IndexAndFetchTests(CatalogCase):
    def test_the_index_keeps_only_well_formed_entries_of_the_catalog(self):
        good = self.fake.entries[0]
        self.fake.entries += [{**good, "id": "Böse!"}, {**good, "id": "fremd", "url": "https://anderswo.example/x.json"},
                              {**good, "id": "kaputt", "sha256": "abc"}, {**good, "id": "mein_inventar"}, "kein Objekt"]
        entries = catalog.index()
        self.assertEqual([e["id"] for e in entries], ["mein_inventar"])
        self.assertEqual((entries[0]["author"], entries[0]["rating"]), ("Anna", 4.5))

    def test_only_the_catalogs_own_origin_and_https(self):
        self.assertTrue(catalog._allowed(BASE + "/api/v1/vorlagen.json"))
        self.assertFalse(catalog._allowed("https://anderswo.example/api/v1/vorlagen.json"))
        self.assertFalse(catalog._allowed("http://vorlagen.example/api/v1/vorlagen.json"))
        with override_settings(TEMPLATE_CATALOG_URL=""):
            self.assertFalse(catalog.enabled())
            with self.assertRaises(catalog.CatalogError):
                catalog.index(cache=False)

    def test_a_file_must_match_the_index_and_pass_the_strict_check(self):
        item = catalog.entry("mein_inventar")
        self.assertEqual(catalog.fetch(item)["id"], "mein_inventar")
        url = item["url"]
        self.fake.files[url] = self.fake.files[url].replace(b"Mein Inventar", b"Dein Inventar")
        with self.assertRaisesRegex(catalog.CatalogError, "stimmt nicht"):
            catalog.fetch(item)
        hostile = template()
        hostile["masks"][0]["tree"]["tabs"][0]["id"] = "x');alert(1);('"
        self.fake.add(hostile)
        with self.assertRaisesRegex(catalog.CatalogError, "ungültig"):
            catalog.fetch(self.fake.entries[-1] | {"url": self.fake.entries[-1]["url"]})
        self.fake.add(template(id="filme"))  # the platform's id: refused, whatever the catalog says
        with self.assertRaisesRegex(catalog.CatalogError, "vergeben"):
            catalog.fetch(next(e for e in self.fake.entries if e["id"] == "filme"))


class InstallAndUpdateTests(CatalogCase):
    def test_install_keeps_the_file_and_notes_the_source(self):
        item = catalog.entry("mein_inventar")
        catalog.install(catalog.fetch(item), "catalog", self.it, details=catalog.info(item))
        table = Table.objects.get(key="inv_raeume")
        self.assertEqual(table.template, {"id": "mein_inventar", "version": 1, "source": "catalog"})
        kept = CatalogTemplate.objects.get()
        self.assertEqual((kept.source, kept.author, kept.sha256), ("catalog", "Anna", item["sha256"]))
        self.assertEqual(blueprints.available()["mein_inventar"]["origin"], "catalog")

    def test_a_file_never_changes_a_catalog_template_nor_the_platforms(self):
        item = catalog.entry("mein_inventar")
        catalog.install(catalog.fetch(item), "catalog", self.it, details=catalog.info(item))
        data, problems = catalog.read_file(json.dumps(template(version=2)).encode())
        self.assertEqual(problems, [])
        with self.assertRaisesRegex(blueprints.BlueprintError, "anderen Quelle"):
            catalog.install(data, "file", self.it)
        self.assertEqual(Table.objects.get(key="inv_raeume").template["version"], 1)
        blueprints.apply(json.loads((blueprints.LIBRARY / "termine.json").read_text(encoding="utf-8")), self.it)
        _data, problems = catalog.read_file(json.dumps(template(id="termine")).encode())
        self.assertIn("vergeben", " ".join(problems))

    def test_the_daily_check_finds_a_newer_version_and_ergaenzen_adds_it(self):
        item = catalog.entry("mein_inventar")
        catalog.install(catalog.fetch(item), "catalog", self.it, details=catalog.info(item))
        newer = template(version=2, changes={"2": {"de": "Mit Seriennummer.", "en": "With serial number."}})
        newer["tables"][1]["fields"].append({"key": "f_seriennummer", "label": "Seriennummer", "type": "text"})
        self.fake.add(newer)
        cache.clear()
        self.assertEqual(catalog.check_updates(), 1)
        self.assertEqual(catalog.check_updates(), 0)  # once a day
        self.assertEqual(blueprints.newer_versions(self.it), [{"id": "mein_inventar", "title": "Mein Inventar", "version": 2, "installed": 1,
                                                               "news": "Mit Seriennummer.", "catalog": True}])
        self.login(self.it)
        self.assertContains(self.client.get(reverse("table_list")), reverse("table_catalog_update", args=["mein_inventar"]))
        self.client.post(reverse("table_catalog_update", args=["mein_inventar"]))
        self.assertTrue(Table.objects.get(key="inv_objekte").fields.filter(key="f_seriennummer").exists())
        self.assertEqual(Table.objects.get(key="inv_raeume").template, {"id": "mein_inventar", "version": 2, "source": "catalog"})
        self.assertEqual(blueprints.newer_versions(self.it), [])
        self.assertIsNone(CatalogTemplate.objects.get().latest_version)

    def test_ergaenzen_asks_the_catalog_afresh_not_the_list_shown_minutes_ago(self):
        item = catalog.entry("mein_inventar")
        catalog.install(catalog.fetch(item), "catalog", self.it, details=catalog.info(item))
        catalog.index()  # the list as "Vorlage hinzufügen" showed it, kept for ten minutes
        self.fake.add(template(version=2))
        catalog.update("mein_inventar", self.it)
        self.assertEqual(Table.objects.get(key="inv_raeume").template["version"], 2)

    def test_the_catalogs_own_note_is_no_link_for_the_reviewer(self):
        data = {**template(), "catalog": {"source": "datafluss.de", "url": f"{BASE}/vorlagen/mein_inventar/"}}
        self.assertFalse([n for n in catalog.tc.notes(data) if "Links" in n])

    def test_no_question_to_the_catalog_when_switched_off_connected_or_nothing_applied(self):
        self.assertEqual(catalog.check_updates(force=True), 0)
        self.assertEqual(self.fake.calls, [])
        item = catalog.entry("mein_inventar")
        catalog.install(catalog.fetch(item), "catalog", self.it, details=catalog.info(item))
        self.fake.calls.clear()
        SchoolSettings.objects.filter(pk=1).update(catalog_updates=False)
        self.assertEqual(catalog.check_updates(force=True), 0)
        SchoolSettings.objects.filter(pk=1).update(catalog_updates=True)
        with mock.patch.dict(os.environ, {"SYNC_TOKEN": "t"}):
            self.assertEqual(catalog.check_updates(force=True), 0)
        self.assertEqual(self.fake.calls, [])
        CatalogTemplate.objects.update(checked_at=timezone.now() - timedelta(days=2))
        catalog.check_updates()
        self.assertEqual(len(self.fake.calls), 1)


class PageTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.login(self.it)

    def test_choose_preview_and_apply_from_the_catalog(self):
        page = self.client.get(reverse("table_blueprints"))
        self.assertContains(page, reverse("table_catalog"))
        listing = self.client.get(reverse("table_catalog"))
        self.assertContains(listing, "Mein Inventar")
        self.assertNotContains(listing, "Filme &amp; Serien")  # the platform's own are above already
        self.assertRedirects(self.client.post(reverse("table_template_preview"), {"pick": "mein_inventar"}), reverse("table_template_preview"))
        preview = self.client.get(reverse("table_template_preview"))
        self.assertContains(preview, "Gegenstände")
        self.assertContains(preview, "CC BY 4.0")
        self.assertFalse(Table.objects.filter(key="inv_raeume").exists())  # nothing before "Anwenden"
        self.client.post(reverse("table_template_preview"), {"action": "apply"})
        self.assertEqual(Table.objects.get(key="inv_raeume").template["source"], "catalog")
        self.assertEqual(self.client.get(reverse("table_template_preview")).status_code, 302)  # the preview is used up

    def test_a_file_is_checked_shown_and_applied(self):
        refused = self.client.post(reverse("table_template_preview"), {"file": SimpleUploadedFile("x.json", b"{\"format\": 1}")})
        self.assertContains(refused, "lässt sich nicht anwenden")
        upload = SimpleUploadedFile("t.json", json.dumps(template(id="vereinsheim")).encode())
        self.client.post(reverse("table_template_preview"), {"file": upload})
        self.assertContains(self.client.get(reverse("table_template_preview")), "Aus einer Datei")
        self.client.post(reverse("table_template_preview"), {"action": "apply", "demo": "1"})
        self.assertEqual(Table.objects.get(key="inv_raeume").template, {"id": "vereinsheim", "version": 1, "source": "file"})

    def test_a_database_template_only_where_one_fits(self):
        SchoolSettings.objects.filter(pk=1).update(modules_off=[])  # Schulverwaltung in use
        self.client.post(reverse("table_template_preview"), {"pick": "mein_inventar"})
        self.assertContains(self.client.get(reverse("table_template_preview")), "passt nur in eine Datenbank ohne Schulverwaltung")
        self.client.post(reverse("table_template_preview"), {"action": "apply"})
        self.assertFalse(Table.objects.filter(key="inv_raeume").exists())

    def test_catalog_away_is_a_note_not_an_error(self):
        with mock.patch.object(catalog, "_open", side_effect=catalog.CatalogError("Der Vorlagenkatalog ist nicht erreichbar.")):
            cache.clear()
            self.assertContains(self.client.get(reverse("table_catalog")), "nicht erreichbar")

    def test_connected_computers_install_on_the_server(self):
        with mock.patch.dict(os.environ, {"SYNC_TOKEN": "t"}):
            self.assertContains(self.client.get(reverse("table_blueprints")), "auf dem Server installiert")
            self.assertEqual(self.client.post(reverse("table_template_preview"), {"pick": "mein_inventar"}).status_code, 403)

    def test_the_switch(self):
        self.client.post(reverse("table_catalog_switch"), {"action": "off"})
        self.assertFalse(SchoolSettings.load().catalog_updates)
        self.assertContains(self.client.get(reverse("table_blueprints")), "Ausgeschaltet")


class SidecarIndexTests(AppTestCase):
    def test_the_wizard_asks_the_sidecar_without_a_database(self):
        root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(root), "TEMPLATE_CATALOG_URL": ""}
        out = subprocess.run([sys.executable, str(root / "client" / "sidecar" / "sidecar.py"), "--catalog-index"],
                             capture_output=True, env=env, timeout=60)
        self.assertEqual(json.loads(out.stdout), {"error": "Der Vorlagenkatalog ist auf diesem Server ausgeschaltet."})
