import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from devices.models import Asset
from people.models import ClassGroup, Person
from transfer import exporters, importer
from transfer.models import ImportSession
from transfer.parsers import parse_date, read_table
from vault.models import Credential

from .base import AppTestCase

NINOX = (
    "Vorname;Nachname;E-Mail des Schülers;Klasse;Last of Gerätenummer;Last of Seriennummer;Bild;Archiviert;Geburtsdatum;Schuleintritt;Schulaustritt;Benutzername Bibox;Schultyp\r\n"
    "Anna;Beispiel;anna.beispiel@schule.example;3a;;;B2217/Anna.jpg;No;27. May 2018;1. Aug 2026;;annab1;GS\r\n"
    "Ben;Probe;Ben.Probe@falsch.example;10b;SuS1149;5190920853;B2105/Ben.jpg;No;23. Apr 2011;1. Aug 2025;;benp1;GY\r\n"
    "Kein;;;;;;;No;;1. Aug 2025;;;GY\r\n"
    "Carla;Test;carla@schule.example;Rote Gruppe;;;B1/C.jpg;No;30. Feb 2011;1. Aug 2025;;;Kiga\r\n"
).encode("utf-8")


class ParserTests(AppTestCase):
    def test_dates(self):
        self.assertEqual(parse_date("27. May 2018").isoformat(), "2018-05-27")
        self.assertEqual(parse_date("3. Mär 2020").isoformat(), "2020-03-03")
        self.assertEqual(parse_date("24.12.2015").isoformat(), "2015-12-24")
        self.assertEqual(parse_date("2015-12-24").isoformat(), "2015-12-24")
        self.assertEqual(parse_date("05/27/2018").isoformat(), "2018-05-27")
        self.assertIsNone(parse_date(""))

    def test_csv_keeps_text_exactly(self):
        headers, rows = read_table("x.csv", "Serie;Name\n005190920853;Müller\n".encode("utf-8"))
        self.assertEqual(rows[0][0], "005190920853")

    def test_windows_encoding(self):
        headers, rows = read_table("x.csv", "Name;Ort\nMüller;Köln\n".encode("cp1252"))
        self.assertEqual(rows[0], ["Müller", "Köln"])

    def test_xlsx_roundtrip_keeps_leading_zeros(self):
        content = exporters.to_xlsx(["Serie"], [["005190920853"]])
        headers, rows = read_table("x.xlsx", content)
        self.assertEqual(rows[0][0], "005190920853")


class ImportTests(AppTestCase):
    def setUp(self):
        self.login(self.sek)
        response = self.client.post(reverse("transfer_upload"), {"entity": "pupil", "file": SimpleUploadedFile("ninox.csv", NINOX)})
        self.session = ImportSession.objects.get()
        self.assertEqual(response.status_code, 302)

    def analyse(self):
        post = {f"map_{i}": self.session.mapping.get(h, "") for i, h in enumerate(self.session.headers)}
        post["match_key"] = "legacy_ninox_id"
        self.client.post(reverse("transfer_mapping", args=[self.session.pk]), post)
        self.session.refresh_from_db()

    def test_ninox_columns_are_recognised(self):
        mapping = self.session.mapping
        self.assertEqual(mapping["Vorname"], "first_name")
        self.assertEqual(mapping["Last of Gerätenummer"], "_device_number")
        self.assertEqual(mapping["Benutzername Bibox"], "login:bibox:username")
        self.assertEqual(mapping["Bild"], "_photo_path")

    def test_dry_run_writes_nothing_and_reports_problems(self):
        before = Person.objects.count()
        self.analyse()
        report = self.session.report
        self.assertEqual(Person.objects.count(), before)
        self.assertEqual(report["create"], 2)
        self.assertEqual(report["error"], 2)
        text = json.dumps(report["rows"], ensure_ascii=False)
        self.assertIn("Nachname fehlt", text)
        self.assertIn("Datum nicht lesbar", text)
        self.assertIn("führende Nullen", text)
        self.assertIn("kleingeschrieben", text)

    def test_commit_creates_people_devices_logins_and_clears_rows(self):
        self.analyse()
        self.client.post(reverse("transfer_commit", args=[self.session.pk]))
        ben = Person.objects.get(legacy_ninox_id="B2105")
        self.assertEqual(ben.email, "ben.probe@falsch.example")
        self.assertEqual(ben.current_class.name, "10b")
        self.assertEqual(ClassGroup.objects.get(name="10b").level, 10)
        self.assertEqual(Asset.objects.get(number="SuS1149").open_assignment.person, ben)
        self.assertEqual(Credential.objects.get(person=ben).values["username"], "benp1")
        self.session.refresh_from_db()
        self.assertEqual(self.session.rows, [])

    def test_second_import_recognises_existing(self):
        self.analyse()
        self.client.post(reverse("transfer_commit", args=[self.session.pk]))
        self.client.post(reverse("transfer_upload"), {"entity": "pupil", "file": SimpleUploadedFile("again.csv", NINOX)})
        second = ImportSession.objects.filter(status="uploaded").get()
        second.report = importer.analyse(second)
        self.assertEqual(second.report["create"], 0)
        self.assertEqual(second.report["same"] + second.report["update"], 2)

    def test_undo_removes_everything_it_created(self):
        before = Person.objects.count()
        self.analyse()
        self.client.post(reverse("transfer_commit", args=[self.session.pk]))
        self.client.post(reverse("transfer_undo", args=[self.session.pk]))
        self.assertEqual(Person.objects.count(), before)
        self.assertFalse(Asset.objects.filter(number="SuS1149").exists())
        self.assertFalse(ClassGroup.objects.filter(name="10b").exists())

    def test_undo_restores_changed_values(self):
        person = Person.objects.create(kind="pupil", first_name="Anna", last_name="Beispiel", legacy_ninox_id="B2217", birth_place="X")
        self.analyse()
        self.client.post(reverse("transfer_commit", args=[self.session.pk]))
        self.assertEqual(Person.objects.get(pk=person.pk).school_type, "GS")
        self.client.post(reverse("transfer_undo", args=[self.session.pk]))
        self.assertEqual(Person.objects.get(pk=person.pk).school_type, "")


class OldNinoxRunTests(AppTestCase):
    def test_a_ninox_run_in_the_history_cannot_be_undone(self):
        session = ImportSession.objects.create(filename="ninox-daten.zip", entity="ninox", status=ImportSession.DONE, user=self.sek, match_key="")
        self.login(self.sek)
        page = self.client.get(reverse("transfer"))
        self.assertContains(page, "Über die Sicherung zurückzunehmen.")
        self.assertNotContains(page, reverse("transfer_undo", args=[session.pk]))
        self.assertEqual(self.client.post(reverse("transfer_undo", args=[session.pk])).status_code, 400)
        session.refresh_from_db()
        self.assertEqual(session.status, ImportSession.DONE)

class ExportTests(AppTestCase):
    def test_formula_injection_is_neutralised_but_phone_numbers_survive(self):
        content = exporters.to_csv(["A"], [["=HYPERLINK(\"http://x\")"], ["+86 138 0000 0000"], ["@cmd"]]).decode("utf-8")
        self.assertIn("'=HYPERLINK", content)
        self.assertIn("+86 138 0000 0000", content)
        self.assertIn("'@cmd", content)

    def test_csv_opens_in_german_excel(self):
        content = exporters.to_csv(["A", "B"], [["ä", "ö"]])
        self.assertTrue(content.startswith("﻿".encode("utf-8")))
        self.assertIn(b";", content)

    def test_export_follows_columns_and_order(self):
        second = Person.objects.create(kind="pupil", first_name="Zoe", last_name="Zett")
        self.login(self.sl)
        payload = {"entity": "pupil", "format": "csv", "columns": [{"key": "last_name", "title": "Nachname"}], "ids": [str(second.pk), str(self.pupil.pk)]}
        content = self.client.post(reverse("export_rows"), {"payload": json.dumps(payload)}).content.decode("utf-8-sig")
        lines = content.split("\r\n")
        self.assertEqual(lines[:3], ["Nachname", "Zett", "Muster"])
