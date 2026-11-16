"""Own tables with fewer clicks: new records, history, link picker, lists of many records, guessed types and
a new table from a file (tables/infer.py)."""
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import fieldtypes as ft
from core.context_user import set_current_user
from core.models import Role
from tables import formula, infer, links, services
from tables.models import EDIT, Field, Record, Table
from tables.views_records import rows
from transfer.models import ImportSession

from .test_tables import TableCase


class NewRecordTests(TableCase):
    def setUp(self):
        super().setUp()
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT})
        set_current_user(None)
        self.login(self.sek)

    def new(self):
        answer = self.client.post(reverse("record_new", args=["buecher"]))
        record = Record.objects.get(table=self.table)
        self.assertRedirects(answer, reverse("record_detail", args=["buecher", record.pk]) + "?neu=1")
        return record

    def test_new_record_offers_discard_and_another(self):
        self.assertEqual(self.client.get(reverse("record_new", args=["buecher"])).status_code, 405)
        record = self.new()
        page = self.client.get(reverse("record_detail", args=["buecher", record.pk]) + "?neu=1")
        self.assertContains(page, "data-focus-first")
        self.assertContains(page, "Verwerfen")
        self.assertContains(page, "<title>(ohne Titel)", html=False)
        self.assertNotContains(self.client.get(reverse("record_detail", args=["buecher", record.pk])), "data-focus-first")

    def test_discarding_an_untouched_record_leaves_nothing(self):
        record = self.new()
        self.client.post(reverse("record_delete", args=["buecher", record.pk]), {"discard": "1"})
        self.assertFalse(Record.everything.filter(pk=record.pk).exists())

    def test_discarding_a_filled_record_keeps_it_in_the_bin(self):
        record = self.new()
        self.client.post(reverse("record_field", args=["buecher", record.pk, "f_name"]), {"value": "Momo"})
        answer = self.client.post(reverse("record_delete", args=["buecher", record.pk]), {"discard": "1"})
        self.assertRedirects(answer, reverse("table_records", args=["buecher"]))
        self.assertTrue(Record.everything.filter(pk=record.pk).exclude(deleted_at=None).exists())

    def test_saving_the_title_field_sends_the_new_title(self):
        record = self.new()
        answer = self.client.post(reverse("record_field", args=["buecher", record.pk, "f_name"]), {"value": "Momo"})
        self.assertEqual(json.loads(answer["HX-Trigger"])["fieldSaved"]["title"], "Momo")

    def test_bring_back_everything_from_the_bin(self):
        for name in ("A", "B", "C"):
            services.create_record(self.table, {"f_name": name}).recycle()
        self.assertContains(self.client.get(reverse("records_bin", args=["buecher"])), "Alle wiederherstellen")
        self.client.post(reverse("records_bin", args=["buecher"]), {"all": "1"})
        self.assertEqual(Record.objects.filter(table=self.table).count(), 3)


class HistoryAndPickerTests(TableCase):
    def setUp(self):
        super().setUp()
        self.done = services.create_field(self.table, "Vorrätig", "bool")
        self.out = services.create_field(self.table, "Erschienen", "date")
        self.tags = services.create_field(self.table, "Schlagworte", "multichoice", "Spannend\nLustig")
        self.shelf = services.create_field(self.table, "Regal", "link", config=links.clean_config("link", {"target": "table:buecher", "multiple": "1"}))
        self.momo = services.create_record(self.table, {"f_name": "Momo"})
        self.krabat = services.create_record(self.table, {"f_name": "Krabat"})
        for field, raw in ((self.done, "1"), (self.out, "1973-09-01"), (self.tags, ["Spannend", "Lustig"]), (self.price, "1234,5")):
            services.set_value(self.momo, field, raw)
        set_current_user(None)
        self.login(self.it)

    def test_history_shows_values_as_the_fields_do(self):
        html = self.client.get(reverse("record_block", args=["buecher", self.momo.pk, "history"])).content.decode()
        for shown in ("Ja", "01.09.1973", "Spannend, Lustig", "1.234,50 €"):
            self.assertIn(shown, html)
        for raw in ("True", "1973-09-01", "[&quot;Spannend", "1234.50"):
            self.assertNotIn(raw, html)

    def test_picker_knows_its_search_and_keeps_the_focus_for_several(self):
        options = self.client.get(reverse("record_link_options", args=["buecher", self.momo.pk, "f_regal"]), {"q": "kra"})
        self.assertContains(options, 'data-q="kra"')
        self.assertContains(options, "Krabat")
        added = self.client.post(reverse("record_link", args=["buecher", self.momo.pk, "f_regal"]), {"action": "add", "target": str(self.krabat.pk)})
        self.assertContains(added, "$el.focus()")
        removed = self.client.post(reverse("record_link", args=["buecher", self.momo.pk, "f_regal"]), {"action": "remove", "target": str(self.krabat.pk)})
        self.assertNotContains(removed, "$el.focus()")


class ManyRecordTests(TableCase):
    def setUp(self):
        super().setUp()
        self.shelf = services.create_field(self.table, "Regal", "link", config=links.clean_config("link", {"target": "table:buecher"}))
        self.total = services.create_field(self.table, "Summe", "formula", config=services.formula_config(self.table, {"expression": "{Preis} * 2 + count({Regal})"}))
        self.first = services.create_record(self.table, {"f_name": "Erstes", "f_preis": "1"})

    def add(self, n):
        for i in range(n):
            record = services.create_record(self.table, {"f_name": f"Buch {i}", "f_preis": str(i)})
            links.set_targets(record, self.shelf, [str(self.first.pk)])

    def test_the_step_limit_counts_per_value_not_per_list(self):
        self.add(30)
        with mock.patch.object(formula, "MAX_STEPS", 40):
            data = rows(self.table, self.it)
        self.assertFalse([r for r in data if r["f_summe"] == "#Fehler"])

    def test_link_formulas_in_a_list_need_no_query_per_record(self):
        self.add(5)
        with CaptureQueriesContext(connection) as few:
            rows(self.table, self.it)
        self.add(25)
        with CaptureQueriesContext(connection) as many:
            rows(self.table, self.it)
        self.assertEqual(len(few.captured_queries), len(many.captured_queries))

    def test_list_columns_title_wide_and_computed_decimals_free(self):
        self.login(self.it)
        columns = {c["key"]: c for c in self.client.get(reverse("table_records", args=["buecher"])).context["config"]["columns"]}
        self.assertTrue(columns["f_name"]["wide"])
        self.assertIsNone(columns["f_summe"]["decimals"])
        self.assertEqual(columns["f_preis"]["decimals"], 2)


class NumberTests(TableCase):
    def test_numbers_as_found_in_files(self):
        for text, plain in (("1.234,5", "1234.5"), ("1234,5", "1234.5"), ("1,234.5", "1234.5"), ("1.234.567", "1234567"), ("12", "12"), ("1 234", "1234")):
            self.assertEqual(ft.normalize_number(text), plain)
        self.assertEqual(str(ft.clean(self.price, "12,50 €")), "12.50")
        count = services.create_field(self.table, "Anzahl", "number")
        self.assertEqual(ft.clean(count, "3,0"), 3)
        with self.assertRaisesMessage(Exception, "ganze Zahl"):
            ft.clean(count, "3,5")


class GuessTests(TableCase):
    def test_types_from_values(self):
        cases = {
            ("Preis", ("12,50 €", "1.299,00 €")): ("number", {"decimals": 2, "unit": "€"}),
            ("Anzahl", ("1", "30")): ("number", {}),
            ("Gewicht", ("1.5", "2.25")): ("number", {"decimals": 2}),
            ("PLZ", ("01234", "10115")): ("text", {}),
            ("Mail", ("a@b.de", "x@y.org")): ("email", {}),
            ("Seit", ("01.02.2024", "2023-05-01")): ("date", {}),
            ("Zeit", ("01.02.2024 10:30", "02.02.2024 11:00")): ("datetime", {}),
            ("Aktiv", ("ja", "nein", "ja")): ("bool", {}),
            ("Web", ("https://a.de", "https://b.de")): ("url", {}),
            ("Tel", ("030 1234567", "+49 30 1")): ("phone", {}),
            ("Notiz", ("kurz", "zwei\nZeilen")): ("textarea", {}),
            ("Name", ("Anna", "Ben")): ("text", {}),
        }
        for (header, values), (kind, config) in cases.items():
            with self.subTest(header=header):
                guess = infer.column(header, list(values))
                self.assertEqual((guess["type"], guess["config"]), (kind, config))
        self.assertEqual(infer.column("Status", ["Neu", "Alt", "Neu", "Alt", "Neu"])["options"], ["Neu", "Alt"])

    def test_types_and_links_from_names(self):
        rules = infer.label_rules([("table:buecher", "Bücher"), ("pupil", "Schüler"), ("staff", "Personal")], own="ausleihen")
        import re

        def guess(label):
            for rule in rules:
                text = infer._fold(label) if rule.get("fold") else label
                if re.search(rule["pattern"], text, re.IGNORECASE):
                    return rule["type"], rule.get("target", "")
            return "text", ""

        self.assertEqual(guess("Buch"), ("link", "table:buecher"))
        self.assertEqual(guess("Schüler"), ("link", "pupil"))
        self.assertEqual(guess("Schülerzahl"), ("number", ""))
        self.assertEqual(guess("Lehrkraft"), ("link", "staff"))
        self.assertEqual(guess("E-Mail der Eltern"), ("email", ""))
        self.assertEqual(guess("Rückgabe am"), ("date", ""))
        self.assertEqual(guess("Postleitzahl"), ("text", ""))
        self.assertEqual(guess("Bemerkungen"), ("textarea", ""))


CSV = ("Bezeichnung;Kategorie;Anschaffung;Preis;Seriennummer;Defekt;Kontakt;\n"
       "Beamer;Beamer;14.03.2023;499,00 €;X1;nein;it@schule.example;\n"
       "Laptop;Laptop;01.09.2022;1.149,00 €;X2;ja;it@schule.example;\n")


class FromFileTests(TableCase):
    def upload(self, user=None):
        self.login(user or self.it)
        return self.client.post(reverse("table_from_file"), {"file": SimpleUploadedFile("Geräte.csv", CSV.encode(), content_type="text/csv")})

    def test_a_file_becomes_a_table_with_its_rows(self):
        answer = self.upload()
        session = ImportSession.objects.get(entity="newtable")
        self.assertRedirects(answer, reverse("table_from_file_columns", args=[session.pk]))
        page = self.client.get(answer.url)
        self.assertEqual(page.context["name"], "Geräte")
        self.assertEqual([c["header"] for c in page.context["columns"]][-1], "Spalte 8")
        self.assertEqual(page.context["title_index"], 0)
        post = {"name": "Geräte", "title": "0"}
        for c in page.context["columns"][:7]:
            post[f"use_{c['index']}"] = "1"
        answer = self.client.post(answer.url, post)
        table = Table.objects.get(name="Geräte")
        self.assertRedirects(answer, reverse("table_records", args=[table.key]))
        fields = {f.label: f for f in services.fields_of(table)}
        self.assertEqual({label: f.type for label, f in fields.items()}, {
            "Bezeichnung": "text", "Kategorie": "text", "Anschaffung": "date", "Preis": "number", "Seriennummer": "text",
            "Defekt": "bool", "Kontakt": "email"})
        self.assertEqual(fields["Bezeichnung"].key, "f_name")
        self.assertEqual(fields["Preis"].config, {"decimals": 2, "unit": "€"})
        records = {r.title: r for r in Record.objects.filter(table=table)}
        self.assertEqual(set(records), {"Beamer", "Laptop"})
        self.assertEqual(records["Laptop"].values["f_preis"], "1149.00")
        self.assertIs(records["Laptop"].values["f_defekt"], True)
        self.assertEqual(ImportSession.objects.get(pk=session.pk).status, ImportSession.DONE)

    def test_title_of_another_type_replaces_the_name_field(self):
        answer = self.upload()
        post = {"name": "Nach Datum", "title": "2", "use_0": "1", "use_2": "1"}
        self.client.post(answer.url, post)
        table = Table.objects.get(name="Nach Datum")
        self.assertEqual(table.title_field, "f_anschaffung")
        self.assertFalse(Field.objects.filter(table=table, key="f_name").exists())
        self.assertEqual(Record.objects.filter(table=table).count(), 2)

    def test_access_for_every_role_in_one_choice(self):
        answer = self.upload()
        self.client.post(answer.url, {"name": "Für alle", "title": "0", "use_0": "1", "access": "read"})
        table = Table.objects.get(name="Für alle")
        self.assertEqual(set(table.rights.values()), {"read"})
        self.client.post(reverse("table_list"), {"name": "Nur für IT", "access": "it"})
        self.assertEqual(set(Table.objects.get(name="Nur für IT").rights.values()) - {"none"}, set())
        self.client.post(reverse("table_list"), {"name": "Gemeinsam", "access": "edit"})
        self.assertEqual(set(Table.objects.get(name="Gemeinsam").rights.values()), {"edit"})

    def test_only_designers_who_may_import(self):
        self.assertEqual(self.upload(self.sek).status_code, 403)


class LibraryTemplateTests(TableCase):
    """The templates that come with the platform and need no module: Inventar and Termine."""

    def setUp(self):
        from core.models import SchoolSettings
        from tables import blueprints

        super().setUp()
        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(modules_off=["schulverwaltung"])
        self.found = blueprints.available()

    def test_templates_without_a_module_apply_with_their_samples(self):
        from tables import blueprints

        for template_id in ("inventar", "termine"):
            with self.subTest(template=template_id):
                template = self.found[template_id]
                self.assertEqual(blueprints.check(template), [])
                self.assertTrue(blueprints.usable(template))
                blueprints.apply(template, self.it, demo=True)
                self.assertEqual(blueprints.apply(template, self.it)["created"], [])
        room = {r["name"]: r for r in rows(Table.objects.get(key="inv_raeume"), self.it)}["R 12"]
        self.assertEqual((room["f_anzahl"], room["f_wert"]), (2.0, 2991.0))  # 499 + 28 × 89 from the items in the room
        self.assertEqual(Record.objects.filter(table__key="ter_termine").count(), 3)

    def test_usable_templates_come_first_on_the_page(self):
        self.login(self.it)
        cards = self.client.get(reverse("table_blueprints")).context["rows"]
        self.assertEqual([c["id"] for c in cards][-1], "bibliothek")


class ReversePathTests(TableCase):
    def setUp(self):
        super().setUp()
        self.loans = services.create_table("Ausleihen")
        self.book = services.create_field(self.loans, "Buch", "link", config=links.clean_config("link", {"target": "table:buecher", "reverse": "Ausleihen"}))
        self.fee = services.create_field(self.loans, "Gebühr", "number", config={"decimals": 2})
        self.momo = services.create_record(self.table, {"f_name": "Momo"})
        for fee in ("1,50", "2"):
            loan = services.create_record(self.loans, {"f_gebuehr": fee})
            links.set_targets(loan, self.book, [str(self.momo.pk)])

    def test_a_field_of_the_records_that_link_here(self):
        schema = formula.Schema(self.table)
        stored = formula.to_keys("sum({Ausleihen}.{Gebühr})", schema)
        self.assertEqual(stored, "sum({@ausleihen.f_buch}.{f_gebuehr})")
        self.assertEqual(formula.to_names(stored, schema), "sum({Ausleihen}.{Gebühr})")
        self.assertEqual(formula.check(stored, schema), "number")
        field = services.create_field(self.table, "Gebühren", "formula", config=services.formula_config(self.table, {"expression": "sum({Ausleihen}.{Gebühr})"}))
        self.assertEqual(formula.compute(self.momo, field), 3.5)


class CreateTargetTests(HistoryAndPickerTests):
    def test_a_missing_target_is_created_from_the_search(self):
        url = reverse("record_link_options", args=["buecher", self.momo.pk, "f_regal"])
        self.assertContains(self.client.get(url, {"q": "Neues Buch"}), "neu anlegen")
        self.assertNotContains(self.client.get(url, {"q": "krabat"}), "neu anlegen")  # it exists
        answer = self.client.post(reverse("record_link", args=["buecher", self.momo.pk, "f_regal"]), {"action": "create", "title": "Neues Buch"})
        made = Record.objects.get(table=self.table, title="Neues Buch")
        self.assertEqual(links.targets(self.momo, self.shelf), [made.pk])
        self.assertContains(answer, "Neues Buch")

    def test_only_who_may_edit_the_target_table(self):
        services.set_rights(self.table, {Role.SEKRETARIAT: "read"})
        self.login(self.sek)
        self.assertEqual(self.client.post(reverse("record_link", args=["buecher", self.momo.pk, "f_regal"]), {"action": "create", "title": "X"}).status_code, 403)


class TextAfterImportTests(TableCase):
    def test_text_becomes_a_choice_number_or_date(self):
        kind = services.create_field(self.table, "Art", "text")
        count = services.create_field(self.table, "Stück", "text")
        since = services.create_field(self.table, "Seit", "text")
        for name, art, n, day in (("A", "Roman", "1200", "01.02.2024"), ("B", "Sachbuch", "3", "2024-05-01"), ("C", "Roman", "", "")):
            services.create_record(self.table, {"f_name": name, "f_art": art, "f_stueck": n, "f_seit": day})
        services.change_type(kind, "choice")
        services.change_type(count, "number")
        services.change_type(since, "date")
        kind.refresh_from_db()
        self.assertEqual([o["value"] for o in kind.options], ["Roman", "Sachbuch"])
        a = Record.objects.get(table=self.table, title="A")
        self.assertEqual((a.values["f_art"], a.values["f_stueck"], a.values["f_seit"]), ("Roman", 1200, "2024-02-01"))
        self.assertIn(Record.objects.get(table=self.table, title="C").values.get("f_stueck"), (None, ""))
