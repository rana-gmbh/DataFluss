"""Own tables, step 5 (docs/TABLES-PLAN.md §5): the formula language, its checks and limits, and where it shows."""
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.context_user import set_current_user
from core.models import Role
from tables import formula as F, links, services
from tables.models import EDIT, READ, Field, Record

from .test_tables import TableCase


class FormulaCase(TableCase):
    def setUp(self):
        super().setUp()
        self.born = services.create_field(self.table, "Erschienen", "date")
        self.author = services.create_field(self.table, "Autor", "text")
        self.momo = services.create_record(self.table, {"f_name": "Momo", "f_preis": "12,5", "f_genre": "Roman", "f_erschienen": "01.09.1973", "f_autor": "Ende"})
        self.loans = services.create_table("Ausleihen")
        services.set_rights(self.loans, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.book = services.create_field(self.loans, "Buch", "link", config=links.clean_config("link", {"target": "table:buecher", "reverse": "Ausleihen", "multiple": "1"}))
        self.fee = services.create_field(self.loans, "Gebühr", "number", config={"decimals": 2})
        self.loan1 = services.create_record(self.loans, {"f_name": "A1", "f_gebuehr": "2"})
        self.loan2 = services.create_record(self.loans, {"f_name": "A2", "f_gebuehr": "3,5"})
        for loan in (self.loan1, self.loan2):
            links.set_targets(loan, self.book, [str(self.momo.pk)])
        krabat = services.create_record(self.table, {"f_name": "Krabat", "f_preis": "9"})
        links.add_target(self.loan2, self.book, krabat.pk)

    def formula(self, table, label, typed, **extra):
        config = services.formula_config(table, {"expression": typed, **extra})
        return services.create_field(table, label, "formula", config=config)

    def value(self, typed, record=None, table=None, user=None):
        table = table or self.table
        record = record or self.momo
        probe = Field(table=table, key="__t__", label="T", type="formula", config={"expression": F.to_keys(typed, F.Schema(table))})
        return F.compute(Record.objects.get(pk=record.pk), probe, F.Context(user))


class LanguageTests(FormulaCase):
    def test_arithmetic_text_logic_and_functions(self):
        cases = {
            "1 + 2 * 3": Decimal(7), "(1 + 2) * 3": Decimal(9), "-{Preis} + 1": Decimal("-11.50"), "7 / 2": Decimal("3.5"),
            '{Name} & " von " & {Autor}': "Momo von Ende", "{Preis} > 10 and not ({Genre} = \"Sachbuch\")": True,
            "1 = 1 or 1 / 0": True, 'if({Preis} > 10, "teuer", "günstig")': "teuer", "if(true, 1, 1 / 0)": Decimal(1),
            "round(2.345, 2)": Decimal("2.35"), "floor(2.7) + ceil(2.1) + abs(-1)": Decimal(6), "min(3, 1, 2) + max(1, 5)": Decimal(6),
            'len("Momo") + 0': Decimal(4), 'upper("ab") & lower("CD") & trim("  x ")': "ABcdx", 'concat("a", 1, true)': "a1Ja",
            'contains({Name}, "OM")': True, "year({Erschienen}) * 100 + month({Erschienen})": Decimal(197309), "day({Erschienen})": Decimal(1),
            '{Erschienen} + 30': date(1973, 10, 1), "coalesce({ISBN}, \"ohne\")": "ohne", "isempty({ISBN})": True, "format({Preis})": "12,50",
            '"Sie sagte \\"Hallo\\""': 'Sie sagte "Hallo"',
        }
        for typed, want in cases.items():
            with self.subTest(formula=typed):
                self.assertEqual(self.value(typed), want)

    def test_dates_and_empty_values(self):
        self.assertEqual(self.value("days({Erschienen}, today())"), Decimal((timezone.localdate() - date(1973, 9, 1)).days))
        self.assertEqual(self.value("age({Erschienen})"), Decimal(F._age(date(1973, 9, 1))))
        self.assertEqual(self.value("today() - {Erschienen}"), self.value("days({Erschienen}, today())"))
        empty = services.create_record(self.table, {"f_name": "Leer"})
        self.assertIsNone(self.value("{Preis} * 2", empty))
        self.assertEqual(self.value('{Preis} = null', empty), True)

    def test_links_reverse_links_and_paths(self):
        self.assertEqual(self.value("count({Ausleihen})"), Decimal(2))
        self.assertEqual(self.value("sum({Buch}.{Preis})", self.loan2, self.loans), Decimal("21.50"))
        self.assertEqual(self.value("avg({Buch}.{Preis})", self.loan2, self.loans), Decimal("10.75"))
        self.assertEqual(self.value('join({Buch})', self.loan2, self.loans), "Momo, Krabat")
        self.assertEqual(self.value("first({Buch}.{Name})", self.loan1, self.loans), "Momo")
        self.assertEqual(self.value("count({Buch})", self.loan2, self.loans), Decimal(2))
        self.assertEqual(self.value('{ISBN} & ""'), "")

    def test_formulas_read_formulas_and_survive_renames(self):
        double = self.formula(self.table, "Doppelt", "{Preis} * 2")
        total = self.formula(self.table, "Mit Porto", "{Doppelt} + 1")
        self.assertEqual(F.compute(self.momo, total), Decimal("26.00"))
        self.price.label = "Kaufpreis"
        self.price.save()
        self.assertEqual(F.compute(Record.objects.get(pk=self.momo.pk), Field.objects.get(pk=total.pk)), Decimal("26.00"))
        self.assertEqual(F.to_names(Field.objects.get(pk=double.pk).config["expression"], F.Schema(self.table)), "{Kaufpreis} * 2")


class RefusalTests(FormulaCase):
    def problem(self, typed, table=None, field_key=None):
        with self.assertRaises(ValidationError) as caught:
            services.formula_config(table or self.table, {"expression": typed}, field_key)
        return caught.exception.messages[0]

    def test_mistakes_are_named_with_their_place(self):
        for typed, message in {
            "": "leer", "(1 + 2": "Erwartet", "1 +": "endet zu früh", "{Preis} $ 2": "Unerwartetes Zeichen", "{Gibt es nicht}": "Unbekanntes Feld",
            "summe({Preis})": "Unbekannte Funktion", "round()": "erwartet 1–2", '{Preis} + "x"': "nur Zahlen", "sum({Preis})": "Verknüpfung",
            '{Erschienen} < "gestern"': "derselben Art", 'year({Name})': "passt nicht", "{Name}.{Preis}": "keine Verknüpfung", 'if(true, 1, "x")': "verschiedener Art",
        }.items():
            with self.subTest(formula=typed):
                self.assertIn(message, self.problem(typed))
        with self.assertRaises(F.FormulaError) as caught:
            F.to_keys("1 + {Preis} + {Gibt es nicht}", F.Schema(self.table))
        self.assertEqual(caught.exception.position, 14)

    def test_secrets_and_loops_are_refused(self):
        services.create_field(self.table, "Code", "secret")
        self.assertIn("Geheime", self.problem("{Code} & \"\""))
        first = self.formula(self.table, "Eins", "{Preis} + 1")
        second = self.formula(self.table, "Zwei", "{Eins} + 1")
        self.assertIn("sich selbst", self.problem("{Zwei} + 1", field_key=first.key))
        first.config = {"expression": "{f_zwei} + 1", "result": "number"}  # as if a device had sent a loop
        first.save()
        self.assertEqual(F.display(self.momo, Field.objects.get(pk=second.pk))[0], "#Fehler")

    def test_limits(self):
        self.assertIn("zu lang", self.problem("1" * (F.MAX_LENGTH + 1)))
        self.assertIn("verschachtelt", self.problem("(" * 40 + "1" + ")" * 40))
        with mock.patch.object(F, "MAX_STEPS", 20):
            text, problem = F.display(self.momo, self.formula(self.table, "Lang", " + ".join(["1"] * 30)))
        self.assertEqual((text, "Schritte" in problem), ("#Fehler", True))
        with mock.patch.object(F, "MAX_LINKED", 1):
            self.assertEqual(F.display(self.momo, self.formula(self.table, "Viele", "count({Ausleihen})"))[0], "#Fehler")
        self.assertEqual(F.display(self.momo, self.formula(self.table, "Null", "1 / ({Preis} - {Preis})")), ("#Fehler", "Teilen durch null."))

    def test_random_input_never_breaks_anything(self):
        pieces = ["{Preis}", "{Name}", "{Erschienen}", "{Ausleihen}", "{Gibt}", "1", "2.5", '"x"', "true", "null", "+", "-", "*", "/", "&",
                  "=", "<", ">=", "and", "or", "not", "(", ")", ",", ".", "if(", "count(", "sum(", "round(", "days(", "today()", "{", "}", '"', "#", " "]
        rng = random.Random(2026)
        schema = F.Schema(self.table)
        for _ in range(600):
            typed = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 12)))
            try:
                stored = F.to_keys(typed, schema)
                F.check(stored, schema)
            except F.FormulaError:
                continue
            probe = Field(table=self.table, key="__f__", label="F", type="formula", config={"expression": stored})
            text, problem = F.display(self.momo, probe)
            self.assertIsInstance(text, str, typed)


class VisibilityTests(FormulaCase):
    def test_formulas_read_only_what_the_user_may_see(self):
        hidden = services.create_table("Intern")
        services.create_field(hidden, "Betrag", "number")
        secret = services.create_record(hidden, {"f_name": "Geheim", "f_betrag": "100"})
        link = services.create_field(self.table, "Ablage", "link", config={"target": "record", "table": hidden.key})
        links.set_targets(self.momo, link, [str(secret.pk)])
        total = self.formula(self.table, "Intern summiert", "sum({Ablage}.{Betrag})")
        self.assertEqual(F.display(self.momo, total, F.Context(self.it)), ("100", ""))
        self.assertEqual(F.display(self.momo, total, F.Context(self.sl)), ("nicht sichtbar", ""))
        self.login(self.sl)
        row = next(r for r in self.client.get(reverse("table_rows", args=["buecher"])).json() if r["name"] == "Momo")
        self.assertEqual(row["f_intern_summiert"], "nicht sichtbar")


class FieldTests(FormulaCase):
    def test_presets_are_valid_formulas(self):
        labels = []
        for table in (self.table, self.loans):
            for label, typed in F.presets(table):
                labels.append(label)
                services.formula_config(table, {"expression": typed})
        self.assertTrue(any("Ausleihen" in label for label in labels))
        self.assertIn("Alter aus Geburtsdatum", labels)

    def test_result_type_decimals_and_title(self):
        field = self.formula(self.table, "Brutto", "{Preis} * 1.19", decimals="2")
        self.assertEqual((field.config["result"], field.config["decimals"]), ("number", 2))
        self.assertEqual(F.display(self.momo, field), ("14,88", ""))
        label = self.formula(self.table, "Etikett", '{Name} & " (" & {Autor} & ")"')
        counted = self.formula(self.table, "Anzahl", "count({Ausleihen})")
        self.assertTrue(services.may_name(label))
        self.assertFalse(services.may_name(counted))  # its value changes with other records
        self.table.title_field = label.key
        self.table.save()
        services.set_value(self.momo, self.name, "Momo")
        self.assertEqual(Record.objects.get(pk=self.momo.pk).title, "Momo (Ende)")

    def test_dialog_list_and_record_page(self):
        self.login(self.it)
        check = self.client.post(reverse("table_formula_check", args=["buecher"]), {"expression": "count({Ausleihen})"})
        self.assertContains(check, "Ergebnis: Zahl")
        self.assertContains(check, "Momo")
        bad = self.client.post(reverse("table_formula_check", args=["buecher"]), {"expression": "{Preis} +"})
        self.assertContains(bad, "endet zu früh")
        self.client.post(reverse("table_field_new", args=["buecher"]), {"label": "Ausleihen gesamt", "type": "formula", "expression": "count({Ausleihen})", "in_grid": "on"})
        field = Field.objects.get(table=self.table, key="f_ausleihen_gesamt")
        page = self.client.get(reverse("table_field", args=["buecher", field.pk]))
        self.assertContains(page, "count({Ausleihen})")
        self.client.post(reverse("table_field", args=["buecher", field.pk]), {"label": "Ausleihen gesamt", "expression": "count({Ausleihen}) * 10", "in_grid": "on"})
        self.assertEqual(Field.objects.get(pk=field.pk).config["expression"], "count({@ausleihen.f_buch}) * 10")
        config = self.client.get(reverse("table_records", args=["buecher"])).context["config"]
        self.assertEqual({c["key"]: c["type"] for c in config["columns"]}["f_ausleihen_gesamt"], "number")
        self.assertFalse({c["key"]: c["editable"] for c in config["columns"]}["f_ausleihen_gesamt"])
        row = next(r for r in self.client.get(reverse("table_rows", args=["buecher"])).json() if r["name"] == "Momo")
        self.assertEqual(row["f_ausleihen_gesamt"], 20.0)
        self.assertContains(self.client.get(reverse("record_detail", args=["buecher", self.momo.pk])), ">20<")
        answer = self.client.post(reverse("record_field", args=["buecher", self.momo.pk, "f_autor"]), {"value": "M. Ende"})
        self.assertTrue(json.loads(answer["HX-Trigger"])["fieldSaved"]["reload"])  # computed values follow
        self.assertEqual(self.client.post(reverse("table_api_field", args=["buecher", self.momo.pk, "f_ausleihen_gesamt"]),
                                          json.dumps({"value": 1}), content_type="application/json").status_code, 422)
