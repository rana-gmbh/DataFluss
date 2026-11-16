"""Own tables, step 6 (docs/TABLES-PLAN.md §7): importing a file into a table, with links and undo, and exporting a list."""
import csv
import io
import json
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.context_user import set_current_user
from core.models import Role
from devices.models import Asset
from tables import links, services
from tables.importer import ALWAYS_NEW, TableTarget
from tables.models import EDIT, READ, Link, Record
from transfer import targets
from transfer.models import ImportSession

from .test_tables import TableCase

HEADERS = ["Titel", "Preis", "Gattung", "Erschienen", "Vorrätig", "ISBN", "Leser", "Gerät"]


class ImportCase(TableCase):
    def setUp(self):
        super().setUp()
        self.name.label = "Titel"
        self.name.save()
        self.genre.label = "Gattung"
        self.genre.options = [{"value": "Roman", "color": "", "labels": {"en": "Novel"}}, {"value": "Sachbuch", "color": ""}]
        self.genre.save()
        services.create_field(self.table, "Erschienen", "date")
        services.create_field(self.table, "Vorrätig", "bool")
        self.readers = services.create_field(self.table, "Leser", "link", config={"target": "pupil", "multiple": True})
        self.device = services.create_field(self.table, "Gerät", "link", config={"target": "asset"})
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.asset = Asset.objects.create(number="NB-7")
        self.momo = services.create_record(self.table, {"f_name": "Momo", "f_preis": "10", "f_isbn": "111"})
        self.target = TableTarget(self.table)
        set_current_user(None)

    def session(self, rows, match="f_isbn", mapping=None):
        mapping = mapping or self.target.auto_mapping(HEADERS)
        return ImportSession.objects.create(filename="b.csv", entity="table:buecher", headers=HEADERS, rows=rows, mapping=mapping,
                                            match_key=match, status=ImportSession.PREVIEWED, user=self.sek)


class TargetTests(ImportCase):
    def test_columns_match_keys_and_where_to(self):
        mapping = self.target.auto_mapping(HEADERS + ["Unbekannt"])
        self.assertEqual(mapping["Titel"], "f_name")
        self.assertEqual(mapping["Gattung"], "f_genre")
        self.assertEqual(mapping["Leser"], "f_leser")
        self.assertEqual(mapping["Unbekannt"], "")
        self.assertEqual([k for k, _ in self.target.match_keys()], ["f_name", "f_isbn", ALWAYS_NEW])
        self.assertIn(("table:buecher", "Tabelle: Bücher"), targets.choices(self.sek))
        self.assertNotIn("table:buecher", dict(targets.choices(self.sl)))
        self.assertIsNone(targets.target_for("table:gibt-es-nicht"))


class AnalyseTests(ImportCase):
    def test_dry_run_cleans_matches_and_warns(self):
        name = f"{self.pupil.first_name} {self.pupil.last_name}"
        report = self.target.analyse(self.session([
            ["Momo", "12,5", "Novel", "1.9.1973", "ja", "111", name, "NB-7"],
            ["Krabat", "9", "Sachbuch", "", "nein", "222", "Niemand Hier", ""],
            ["Kaputt", "viel", "Krimi", "", "", "333", "", ""],
            ["Momo", "10", "", "", "", "111", "", ""],
        ]))
        self.assertEqual((report["update"], report["create"], report["error"], report["same"]), (1, 1, 1, 1))
        update = report["rows"][0]
        self.assertEqual({c["label"] for c in update["changes"]}, {"Preis", "Gattung", "Erschienen", "Vorrätig", "Leser", "Gerät"})
        self.assertIn("nicht gefunden", report["rows"][1]["warnings"][0])
        self.assertTrue(report["rows"][2]["errors"])
        self.assertEqual(Record.objects.filter(table=self.table).count(), 1)  # nothing written

    def test_required_fields_are_needed_for_new_records(self):
        self.price.required = True
        self.price.save()
        report = TableTarget(self.table).analyse(self.session([["Ohne Preis", "", "", "", "", "999", "", ""]]))
        self.assertIn("Preis fehlt", report["rows"][0]["errors"][0])


class CommitTests(ImportCase):
    def test_import_links_and_take_it_back(self):
        other = services.create_record(self.table, {"f_name": "Krabat", "f_isbn": "222", "f_preis": "5"})
        session = self.session([
            ["Momo", "12,5", "Roman", "01.09.1973", "ja", "111", f"{self.pupil.first_name} {self.pupil.last_name}; {self.pupil.email}", "NB-7"],
            ["Krabat", "9", "", "", "", "222", "", ""],
            ["Neu", "4", "Sachbuch", "", "", "333", "", ""],
            ["Kaputt", "viel", "", "", "", "444", "", ""],
        ])
        result = self.target.commit(session, self.sek)
        self.assertEqual(result, {"created": 1, "updated": 2, "skipped": 1})
        momo = Record.objects.get(pk=self.momo.pk)
        self.assertEqual((momo.values["f_preis"], momo.values["f_genre"], momo.values["f_erschienen"], momo.values["f_vorraetig"]), ("12.50", "Roman", "1973-09-01", True))
        self.assertEqual(links.targets(momo, self.readers), [self.pupil.pk])
        self.assertEqual(links.targets(momo, self.device), [self.asset.pk])
        self.assertTrue(Record.objects.filter(table=self.table, title="Neu").exists())
        session.refresh_from_db()
        self.assertEqual((session.status, session.rows), (ImportSession.DONE, []))
        self.assertEqual(self.target.undo_blocker(session), "")
        self.target.rollback(session)
        momo = Record.objects.get(pk=self.momo.pk)
        self.assertEqual(momo.values, {"f_name": "Momo", "f_preis": "10.00", "f_isbn": "111"})
        self.assertEqual(links.targets(momo, self.readers), [])
        self.assertFalse(Record.objects.filter(table=self.table, title="Neu").exists())
        self.assertEqual(Record.objects.get(pk=other.pk).values["f_preis"], "5.00")

    def test_always_new_and_blocked_undo(self):
        session = self.session([["Momo", "1", "", "", "", "555", "", ""]], match=ALWAYS_NEW)
        self.target.commit(session, self.sek)
        self.assertEqual(Record.objects.filter(table=self.table, title="Momo").count(), 2)
        new = Record.objects.get(table=self.table, values__f_isbn="555")
        services.set_value(new, self.price, "2")
        session.refresh_from_db()
        self.assertIn("seither geändert", self.target.undo_blocker(session))
        ImportSession.objects.filter(pk=session.pk).update(updated_at=timezone.now() - timedelta(days=2))
        session.refresh_from_db()
        self.assertIn("24 Stunden", self.target.undo_blocker(session))

    def test_unique_values_are_kept_unique(self):
        session = self.session([["Doppelt", "1", "", "", "", "111", "", ""]], match=ALWAYS_NEW)
        result = self.target.commit(session, self.sek)
        self.assertEqual(result["skipped"], 1)


class PageTests(ImportCase):
    def upload(self, text, entity="table:buecher"):
        return self.client.post(reverse("transfer_upload"), {"entity": entity, "file": SimpleUploadedFile("b.csv", text.encode("utf-8"))})

    def test_whole_flow_through_the_pages(self):
        self.login(self.sek)
        self.assertContains(self.client.get(reverse("transfer"), {"bereich": "table:buecher"}), 'value="table:buecher" selected')
        answer = self.upload("Titel;Preis;ISBN\nMomo;15;111\nNeu;3;999\n")
        session = ImportSession.objects.get(entity="table:buecher")
        self.assertRedirects(answer, reverse("transfer_mapping", args=[session.pk]))
        page = self.client.get(reverse("transfer_mapping", args=[session.pk]))
        self.assertContains(page, "Bestehende Datensätze erkennen über")
        self.client.post(reverse("transfer_mapping", args=[session.pk]), {"map_0": "f_name", "map_1": "f_preis", "map_2": "f_isbn", "match_key": "f_isbn"})
        self.assertContains(self.client.get(reverse("transfer_preview", args=[session.pk])), "Neu")
        self.client.post(reverse("transfer_commit", args=[session.pk]))
        self.assertEqual(Record.objects.get(pk=self.momo.pk).values["f_preis"], "15.00")
        self.assertContains(self.client.get(reverse("transfer")), "Bücher")
        self.client.post(reverse("transfer_undo", args=[session.pk]))
        self.assertEqual(Record.objects.get(pk=self.momo.pk).values["f_preis"], "10.00")

    def test_read_only_and_unknown_tables_are_refused(self):
        self.login(self.sl)
        self.assertEqual(self.upload("Titel\nX\n").status_code, 403)
        self.login(self.it)
        self.assertEqual(self.upload("Titel\nX\n", entity="table:gibt-es-nicht").status_code, 403)
        self.assertEqual(self.upload("Titel\nX\n", entity="erfunden").status_code, 403)

    def test_export_shows_what_the_list_shows(self):
        links.set_targets(self.momo, self.device, [str(self.asset.pk)])
        services.set_value(self.momo, self.genre, "Roman")
        self.login(self.sl)
        payload = {"entity": "table:buecher", "format": "csv", "ids": [str(self.momo.pk)],
                   "columns": [{"key": "f_name", "title": "Titel"}, {"key": "f_preis", "title": "Preis"}, {"key": "f_genre", "title": "Gattung"}, {"key": "f_geraet", "title": "Gerät"}]}
        answer = self.client.post(reverse("export_rows"), {"payload": json.dumps(payload)})
        rows = list(csv.reader(io.StringIO(answer.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows, [["Titel", "Preis", "Gattung", "Gerät"], ["Momo", "10,00 €", "Roman", "NB-7"]])
        services.set_rights(self.table, {Role.SCHULLEITUNG: "none"})
        self.assertEqual(self.client.post(reverse("export_rows"), {"payload": json.dumps(payload)}).status_code, 403)
        self.assertFalse(Link.objects.filter(record=self.momo, field="f_leser").exists())
