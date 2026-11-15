"""The module catalog (schule/modules.py) and what the loaded modules add (core/modules.py)."""
import os
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from core.context import NAV, SEARCHERS
from core.permissions import offered_roles
from prints.records import available_sources
from schule import modules as catalog

from .base import PREMIUM, AppTestCase


class CatalogTests(SimpleTestCase):
    def test_without_the_variable_every_present_module_loads(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("DATAFLUSS_MODULES", None)
            self.assertEqual(catalog.selected(), list(catalog.MODULES))  # every module of this repository is present

    def test_platform_and_schulverwaltung_always_load(self):
        with mock.patch.dict(os.environ, {"DATAFLUSS_MODULES": ""}):
            self.assertEqual(catalog.selected(), ["platform", "schulverwaltung"])
            self.assertNotIn("teaching", catalog.apps())

    def test_an_unknown_module_is_refused(self):
        with mock.patch.dict(os.environ, {"DATAFLUSS_MODULES": "premium, buchhaltung"}):
            with self.assertRaisesMessage(ValueError, "buchhaltung"):
                catalog.selected()

    def test_apps_keep_their_order(self):
        self.assertEqual(catalog.apps(list(catalog.MODULES)), list(catalog.ORDER))


class RegistryTests(AppTestCase):
    def test_menu(self):
        titles = [str(title) for title, _items in NAV]
        self.assertEqual(titles[:2], ["Übersicht", "Personen"])
        self.assertEqual(titles[-3:], ["IT", "Drucken und Daten", "Verwaltung"])
        if not PREMIUM:  # with it: kindergarten/tests/test_with_modules.py
            self.assertEqual(len(titles), 5)

    def test_roles_searchers_and_print_sources(self):
        roles = [value for value, _label in offered_roles()]
        sources = [key for key, _label in available_sources()]
        self.assertEqual(roles[:3], ["it", "sekretariat", "schulleitung"])
        self.assertEqual(sources[:6], ["pupil", "staff", "guardian", "asset", "case", "class"])
        if not PREMIUM:
            self.assertEqual((len(roles), SEARCHERS, len(sources)), (3, ["person.view"], 6))

    def test_pages_render(self):
        self.login(self.it)
        for name in ("dashboard", "pupils", "staff", "reports", "users", "settings_home"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        response = self.client.get(reverse("person_detail", args=[self.pupil.pk]))
        self.assertEqual(response.status_code, 200)

    def test_section_flags_show_only_with_their_module(self):
        from masks.services import fields_for

        keys = {field.key for field in fields_for("staff")}
        if not PREMIUM:
            self.assertFalse({"daz_staff", "kiga_staff"} & keys)


class MissingModuleTests(AppTestCase):
    """A database or backup with data of a module this installation lacks (core/modules.py, archive/snapshot.py)."""

    def fake_module_table(self, rows):
        from django.db import connection
        from django.db.migrations.recorder import MigrationRecorder

        MigrationRecorder.Migration.objects.create(app="zeugnisse", name="0001_initial")
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE zeugnisse_note (id integer)")
            for index in range(rows):
                cursor.execute("INSERT INTO zeugnisse_note (id) VALUES (%s)", [index])

    def test_empty_tables_of_a_missing_module_are_ignored(self):
        from core import modules

        self.fake_module_table(0)
        self.assertEqual(modules.missing_with_data(), set())

    def test_data_of_a_missing_module_stops_the_server(self):
        from django.core.management import CommandError, call_command

        from core import modules

        self.fake_module_table(2)
        self.assertEqual(modules.missing_with_data(), {"zeugnisse"})
        with self.assertRaisesMessage(CommandError, "zeugnisse"):
            call_command("check_modules", stdout=StringIO())

    def test_the_schema_leaves_out_a_missing_module(self):
        from sync import views

        views.schema.cache_clear()
        before = views.schema()
        self.fake_module_table(0)
        views.schema.cache_clear()
        self.assertEqual(views.schema(), before)
        views.schema.cache_clear()

    def manifest(self, rows):
        from archive import snapshot

        return {"format": snapshot.FORMAT, "migrations": [*snapshot._applied_migrations(), ["zeugnisse", "0001_initial"]],
                "rows": {"people.person": 1, "zeugnisse.note": rows}}

    def test_a_backup_with_data_of_a_missing_module_is_refused(self):
        from archive import snapshot

        self.assertIn("zeugnisse", snapshot.incompatible(self.manifest(3)))

    def test_a_backup_with_empty_tables_of_a_missing_module_fits(self):
        from archive import snapshot

        self.assertEqual(snapshot.incompatible(self.manifest(0)), "")

    def test_a_backup_names_its_modules(self):
        from django.conf import settings

        from archive import snapshot

        manifest = snapshot._manifest("Schule", [], [])
        self.assertEqual(manifest["datafluss"], settings.DATAFLUSS_VERSION)
        self.assertEqual(manifest["modules"], catalog_manifest())


def catalog_manifest():
    from core import modules

    return modules.manifest()


class SourceModuleTests(SimpleTestCase):
    def test_a_module_from_source_joins_the_catalog(self):
        import json
        import shutil
        import sys
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "datafluss-module.json").write_text(json.dumps({"id": "zeugnisbuch", "title": "Zeugnisbuch", "apps": ["zeugnisbuch_app"]}))
        (folder / "zeugnisbuch_app").mkdir()
        (folder / "zeugnisbuch_app" / "__init__.py").write_text("")
        saved = (dict(catalog.MODULES), dict(catalog.TITLES), list(catalog.ORDER), list(sys.path))
        try:
            with mock.patch.dict(os.environ, {"DATAFLUSS_MODULE_SOURCES": str(folder)}):
                catalog._load_sources()
                self.assertEqual(catalog.MODULES["zeugnisbuch"], ("zeugnisbuch_app",))
                self.assertEqual(catalog.ORDER[-2:], ["zeugnisbuch_app", "sync"])
                self.assertTrue(catalog.present("zeugnisbuch"))
        finally:
            catalog.MODULES.clear(); catalog.MODULES.update(saved[0])
            catalog.TITLES.clear(); catalog.TITLES.update(saved[1])
            catalog.ORDER[:] = saved[2]
            sys.path[:] = saved[3]
