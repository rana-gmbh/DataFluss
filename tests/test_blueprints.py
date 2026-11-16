"""Own tables, step 8 (docs/TABLES-PLAN.md §8): module templates, data-only bundles, and databases without Schulverwaltung."""
import copy
import json

from django.urls import reverse

from core import modules
from core.context_user import set_current_user
from core.models import SavedView, SchoolSettings
from masks.models import Mask
from prints.models import PrintTemplate
from prints.records import available_sources
from schule import bundles
from tables import blueprints, links, services
from tables.models import Field, Link, Record, Table
from transfer import targets

from .base import AppTestCase
from .test_bundles import BundleCase, make_bundle

LIBRARY = blueprints._read(blueprints.LIBRARY / "bibliothek.json")


class CheckTests(BundleCase):
    def test_the_library_is_valid_and_mistakes_are_named(self):
        self.assertEqual(blueprints.check(LIBRARY), [])
        broken = copy.deepcopy(LIBRARY)
        broken["id"] = "Bibliothek!"
        broken["tables"][0]["fields"][0]["type"] = "erfunden"
        broken["tables"][1]["fields"][1]["config"]["table"] = "fehlt"
        broken["prints"][0]["table"] = "fehlt"
        problems = " ".join(blueprints.check(broken))
        for words in ("Kennung", "unbekannter Typ", "verknüpfte Tabelle fehlt", "prints"):
            self.assertIn(words, problems)
        self.assertEqual(blueprints.check({"format": 9}), ["Unbekanntes Vorlagenformat."])

    def test_a_bundle_may_bring_templates_only(self):
        template = json.dumps({**LIBRARY, "id": "inventar", "requires": []}).encode()
        path = make_bundle(self.folder, module_id="inventar", files={"templates/inventar.json": template},
                           manifest_extra={"apps": [], "templates": ["templates/inventar.json"]})
        self.assertEqual(bundles.read_archive(path)["templates"], ["templates/inventar.json"])
        stray = make_bundle(self.folder, module_id="stray", files={"elsewhere/x.json": b"{}"}, manifest_extra={"apps": [], "templates": ["elsewhere/x.json"]})
        with self.assertRaises(bundles.BundleError):
            bundles.read_archive(stray)
        empty = make_bundle(self.folder, module_id="leer", files={"templates/x.json": b"{}"}, manifest_extra={"apps": []})
        with self.assertRaises(bundles.BundleError):
            bundles.read_archive(empty)


class ApplyTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)

    def test_apply_creates_everything_with_the_same_ids_everywhere(self):
        report = blueprints.apply(LIBRARY, self.it, demo=True)
        books, loans = Table.objects.get(key="buecher"), Table.objects.get(key="ausleihen")
        self.assertEqual(books.pk, blueprints.item_id("bibliothek", "table", "buecher"))
        self.assertEqual(books.template, {"id": "bibliothek", "version": 1})
        self.assertEqual(Field.objects.get(table=books, key="f_ausleihen_gesamt").config["result"], "number")
        self.assertEqual(Field.objects.get(table=loans, key="f_ueberfaellig").config["result"], "bool")
        self.assertTrue(Mask.objects.filter(table=loans, is_default=True).exists())
        self.assertEqual(set(PrintTemplate.objects.filter(source__startswith="table:").values_list("name", flat=True)), {"Ausleihschein", "Buchkarte"})
        self.assertEqual(SavedView.objects.filter(entity="table:ausleihen", owner=None).count(), 2)
        self.assertEqual(Record.objects.filter(table=books).count(), 4)
        momo = Record.objects.get(table=books, title="Momo")
        self.assertEqual(Link.objects.filter(target_id=momo.pk).count(), 1)
        self.assertIn("Bücher", report["created"])
        again = blueprints.apply(LIBRARY, self.it, demo=True)
        self.assertEqual(again["created"], [])
        self.assertEqual(Record.objects.filter(table=books).count(), 4)

    def test_a_newer_version_adds_and_keeps_what_was_changed(self):
        blueprints.apply(LIBRARY, self.it)
        author = Field.objects.get(table__key="buecher", key="f_autor")
        author.label = "Verfasser"
        author.save()
        newer = copy.deepcopy(LIBRARY)
        newer["version"] = 2
        newer["tables"][0]["fields"].append({"key": "f_verlag", "label": "Verlag", "type": "text"})
        report = blueprints.apply(newer, self.it)
        self.assertTrue(Field.objects.filter(table__key="buecher", key="f_verlag").exists())
        self.assertEqual(Field.objects.get(pk=author.pk).label, "Verfasser")
        self.assertIn("Bücher · Verfasser", report["changed"])
        self.assertEqual(Table.objects.get(key="buecher").template["version"], 2)
        self.assertEqual(blueprints.installed_version("bibliothek"), 2)

    def test_a_table_of_the_school_with_the_same_key_is_left_alone(self):
        services.create_table("Bücher")
        with self.assertRaisesMessage(blueprints.BlueprintError, "buecher"):
            blueprints.apply(LIBRARY, self.it)
        self.assertFalse(Table.objects.filter(key="ausleihen").exists())  # all or nothing

    def test_the_page(self):
        self.login(self.it)
        self.assertContains(self.client.get(reverse("table_blueprints")), "Bibliothek")
        self.client.post(reverse("table_blueprints"), {"template": "bibliothek", "demo": "on"})
        self.assertEqual(Record.objects.filter(table__key="buecher").count(), 4)
        self.assertContains(self.client.get(reverse("table_blueprints")), "Version 1 angewendet")
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("table_blueprints")).status_code, 403)


class WithoutSchulverwaltungTests(AppTestCase):
    def setUp(self):
        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(modules_off=["schulverwaltung"])
        set_current_user(self.it)
        self.table = services.create_table("Termine")
        services.create_record(self.table, {"f_name": "Elternabend"})

    def test_people_devices_and_logins_are_gone(self):
        self.assertNotIn("schulverwaltung", modules.active())
        self.assertFalse(self.it.can("person.view") or self.it.can("device.view") or self.it.can("vault.view") or self.it.can("admin.fields"))
        self.assertTrue(self.it.can("tables.design") and self.it.can("home.view"))
        self.assertEqual([key for key, _label in available_sources(self.it)], ["table:termine"])
        self.assertEqual(targets.choices(self.it), [("table:termine", "Tabelle: Termine")])
        self.assertEqual([value for value, _label in links.target_choices()], ["table:termine"])
        self.assertFalse(blueprints.usable(LIBRARY))  # the library links to pupils

    def test_pages(self):
        self.login(self.it)
        start = self.client.get(reverse("dashboard"))
        self.assertTemplateUsed(start, "core/tables_home.html")
        self.assertContains(start, "Termine")
        self.assertNotContains(start, 'href="/schueler/"')
        self.assertEqual(self.client.get(reverse("pupils")).status_code, 403)
        self.assertContains(self.client.get(reverse("global_search"), {"q": "eltern"}), "Elternabend")
        settings_page = self.client.get(reverse("settings_home"))
        self.assertNotContains(settings_page, reverse("mask_list"))
        self.assertContains(settings_page, reverse("table_list"))
        self.assertNotContains(self.client.get(reverse("transfer")), "Geräte als Excel")

    def test_a_template_without_people_works_here(self):
        mine = copy.deepcopy(LIBRARY)
        mine.update(id="vereinsbuecher", requires=[])
        mine["tables"][1]["fields"] = [f for f in mine["tables"][1]["fields"] if f["key"] != "f_schueler"]
        mine["tables"][0]["key"], mine["tables"][1]["key"] = "vbuecher", "vausleihen"
        mine["tables"][1]["fields"][1]["config"]["table"] = "vbuecher"
        mine["tables"][0]["fields"][-1]["config"]["expression"] = "count({@vausleihen.f_buch})"
        for group in ("masks", "prints", "views"):
            for item in mine[group]:
                item["table"] = {"buecher": "vbuecher", "ausleihen": "vausleihen"}[item["table"]]
        mine["prints"][1]["tree"]["rows"][0]["cols"][0]["items"][2]["link"] = "@vausleihen.f_buch"
        mine["demo"] = {"vbuecher": mine["demo"]["buecher"], "vausleihen": mine["demo"]["ausleihen"]}
        self.assertEqual(blueprints.check(mine), [])
        blueprints.apply(mine, self.it, demo=True)
        self.assertEqual(Record.objects.filter(table__key="vbuecher").count(), 4)
