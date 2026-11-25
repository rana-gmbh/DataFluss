"""Templates someone else wrote (docs/website.md): the strict check, and the places where a template's values reach
markup, which must be safe even without the check."""
import copy
import json

from django.test import SimpleTestCase
from django.urls import reverse

from core.context_user import set_current_user
from masks import safe
from masks.models import Mask, MaskVersion
from prints import engine
from tables import blueprints, template_check as tc
from tables.models import Field, Record, Table

from .base import AppTestCase

INVENTORY = json.loads((blueprints.LIBRARY / "inventar.json").read_text(encoding="utf-8"))


def shared(**changes):
    """The inventory template as if someone else had uploaded it under an id of their own."""
    data = copy.deepcopy(INVENTORY)
    data.update(id="mein_inventar", **changes)
    return data


class StrictCheckTests(SimpleTestCase):
    def problems(self, data):
        return " ".join(tc.check_untrusted(data))

    def test_the_platforms_database_templates_pass_under_another_id(self):
        for name in ("buecherei", "filme", "inventar", "termine"):
            data = json.loads((blueprints.LIBRARY / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(tc.check_untrusted(data, reserved=()), [], name)

    def test_the_platforms_ids_are_taken(self):
        self.assertIn("vergeben", self.problems(copy.deepcopy(INVENTORY)))
        self.assertEqual(tc.check_untrusted(shared()), [])

    def test_no_school_data_and_no_modules(self):
        data = shared(requires=["schulverwaltung"])
        self.assertIn("Module", self.problems(data))
        data = shared()
        data["tables"][1]["fields"].append({"key": "f_kind", "label": "Kind", "type": "link", "config": {"target": "pupil"}})
        self.assertIn("eigenen Tabellen", self.problems(data))

    def test_unknown_keys_and_wrong_types_are_refused_not_crashing(self):
        data = shared()
        data["tables"][0]["script"] = "alert(1)"
        data["tables"][0]["fields"][0]["label"] = 42
        data["tables"][0]["rights"] = {"daz": "edit"}
        data["tables"][0]["icon"] = "<svg onload=x>"
        problems = self.problems(data)
        for words in ("unbekannte Angabe script", "muss ein Text sein", "unbekannte Angabe daz", "ungültiges Symbol"):
            self.assertIn(words, problems)
        for nonsense in ([], "x", {"format": 1, "id": "a", "version": 1, "title": "T", "tables": [{"key": "t", "name": "T", "rights": "edit"}]}):
            self.assertTrue(tc.check_untrusted(nonsense))

    def test_values_that_reach_markup_must_be_plain(self):
        data = shared()
        data["masks"][0]["tree"]["tabs"][0]["id"] = "x');alert(1);('"
        data["masks"][0]["tree"]["tabs"][0]["sections"][0]["items"][0]["w"] = "12;background:url(x)"
        option_field = next(f for t in data["tables"] for f in t["fields"] if f.get("options"))
        option_field["options"][0]["color"] = "red;position:fixed"
        page = data["prints"][0]["tree"].setdefault("page", {})
        page["footer_text"] = "x'}body{display:none"
        page["margin"] = "1234"
        data["prints"][0]["tree"]["rows"][0]["cols"][0]["items"][0]["align"] = "left;color:red"
        problems = self.problems(data)
        for words in ("ungültige Kennung", "muss eine Zahl sein", "ungültige Farbe", "Anführungszeichen", "vier Zahlen", "ungültiger Wert"):
            self.assertIn(words, problems)

    def test_references_must_point_into_the_template(self):
        data = shared()
        data["tables"][0]["look"] = {"cover": "f_gibt_es_nicht"}
        data["tables"][0]["fields"].append({"key": "f_summe", "label": "Summe", "type": "formula",
                                            "config": {"expression": "sum({@fremde_tabelle.f_wert})"}})
        problems = self.problems(data)
        self.assertIn("Feld, das es nicht gibt", problems)
        self.assertIn("fremde_tabelle", problems)

    def test_limits(self):
        data = shared()
        data["tables"] = data["tables"] * 20
        self.assertTrue(tc.check_untrusted(data))  # duplicate keys first, then too many
        data = shared(demo={"inv_raeume": [{"f_raum": "Raum"}] * (tc.MAX_DEMO_ROWS + 1)})
        self.assertIn("zu viele Einträge", self.problems(data))
        deep = {"a": None}
        node = deep
        for _ in range(30):
            node["a"] = {"a": None}
            node = node["a"]
        self.assertEqual(tc.load(json.dumps(deep).encode())[1], ["Die Vorlage ist zu tief verschachtelt."])
        self.assertTrue(tc.load(b"x" * (tc.MAX_BYTES + 1))[1])
        self.assertTrue(tc.load(b"\xff\xfe")[1])

    def test_notes_summary_and_canonical_file(self):
        data = shared(description="Mehr unter https://example.com", demo={"inv_raeume": [{"f_raum": "Raum 1"}]})
        notes = " ".join(tc.notes(data))
        self.assertIn("https://example.com", notes)
        self.assertIn("1 Beispieldatensätze", notes)
        summary = tc.summary(data)
        self.assertEqual([t["key"] for t in summary["tables"]], ["inv_raeume", "inv_objekte"])
        self.assertEqual(summary["records"], 1)
        out = tc.canonical({**data, "order": 40, "listed": True, "catalog": {"source": "fake"}}, {"source": "datafluss.de", "license": "CC-BY-4.0"})
        self.assertEqual((out["database"], out["requires"], out["catalog"]["source"]), (True, [], "datafluss.de"))
        self.assertNotIn("order", out)
        self.assertEqual(tc.check_untrusted(out), [])


class MarkupSafetyTests(AppTestCase):
    """Without any check: a tab id, a width, a column title or a print value never becomes script or style."""

    def setUp(self):
        set_current_user(self.it)
        self.table = Table.objects.create(key="sicher", name="Sicher", rights={"sekretariat": "edit"})
        self.field = Field.objects.create(table=self.table, key="f_name", label='<img src=x onerror=alert(1)>', type="text", order=10)
        self.record = Record.objects.create(table=self.table, values={"f_name": "A"}, title="A")

    def test_safe_tab_ids_and_widths(self):
        self.assertEqual(safe.tab_id("t1", 0), "t1")
        self.assertEqual(safe.tab_id("x');alert(1);('", 2), "tab3")
        self.assertEqual((safe.width("4"), safe.width("12;x"), safe.width(99), safe.width(None)), (4, 12, 12, 12))

    def test_a_record_page_with_a_hostile_tab_id(self):
        mask = Mask.objects.create(entity=Mask.RECORD, table=self.table, name="Seite", is_default=True)
        MaskVersion.objects.create(mask=mask, number=1, status=MaskVersion.PUBLISHED, tree={"tabs": [
            {"id": "x');alert(1);('", "title": "Tab", "sections": [{"id": "s1", "title": "", "items": [
                {"type": "field", "field": "f_name", "w": "6;background:red"}]}]}]})
        self.login(self.it)
        page = self.client.get(reverse("record_detail", args=[self.table.key, self.record.pk])).content.decode()
        self.assertNotIn("x');alert(1)", page)
        self.assertNotIn("onerror=alert(1)>", page)  # the label is shown, escaped
        self.assertNotIn("background:red", page)
        self.assertIn("show('tab1', $el)", page)

    def test_print_values_are_held_to_what_they_may_be(self):
        el = {"type": "text", "text": "Hallo", "align": "left;color:red", "size": "x"}
        self.assertEqual(engine.render_element(el, {"fields": {}}, {}), '<p style="font-size:9.5pt">Hallo</p>')
        css = engine.page_css({"preset": "a4", "margin": "1234", "footer_text": "a'}body{display:none}\\</style>"}, {"footer": ""}, False)
        self.assertIn("margin:12.0mm 18.0mm 16.0mm 18.0mm", css)
        self.assertIn("content:'a}body{display:none}/style';", css)  # still text inside the CSS string
        rows = engine.render_rows({"rows": [{"cols": [{"w": "x", "valign": "center;color:red", "items": []}]}]}, {}, {})
        self.assertIn("align-self:flex-start", rows)

    def test_apply_turns_bad_values_into_a_message(self):
        data = shared()
        data["tables"][0]["fields"][0]["label"] = 42
        with self.assertRaises(blueprints.BlueprintError):
            blueprints.apply(data, self.it)
        self.assertFalse(Table.objects.filter(key="inv_raeume").exists())


class ExportTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        blueprints.apply(INVENTORY, self.it, demo=True)
        self.tables = list(Table.objects.filter(key__in=["inv_raeume", "inv_objekte"]).order_by("order"))

    def wipe(self):
        from core.models import SavedView
        from prints.models import PrintTemplate
        from tables.models import Link

        Link.objects.all().delete()
        Record.everything.all().delete()
        Mask.objects.filter(table__in=self.tables).delete()
        PrintTemplate.objects.filter(source__startswith="table:").delete()
        SavedView.objects.filter(entity__startswith="table:").delete()
        Table.objects.all().delete()

    def test_export_passes_the_strict_check_and_applies_to_the_same_structure(self):
        data, problems, skipped = blueprints.export(self.tables, {"id": "mein_inventar", "title": "Mein Inventar"}, demo=True, user=self.it)
        self.assertEqual((problems, skipped), ([], []))
        self.assertEqual((data["database"], data["requires"], data["version"]), (True, [], 1))
        before = {t.key: sorted((f.key, f.type) for f in Field.objects.filter(table=t, archived=False)) for t in self.tables}
        rooms = Record.objects.filter(table=self.tables[0]).count()
        self.assertEqual(len(data["demo"]["inv_raeume"]), min(rooms, blueprints.DEMO_ROWS))
        self.assertEqual(len(data["masks"]), len(INVENTORY["masks"]))
        self.assertEqual(sorted(p["name"] for p in data["prints"]), sorted(p["name"] for p in INVENTORY["prints"]))
        self.wipe()
        blueprints.apply(json.loads(json.dumps(data)), self.it, demo=True)
        after = {t.key: sorted((f.key, f.type) for f in Field.objects.filter(table=t, archived=False)) for t in Table.objects.all()}
        self.assertEqual(after, before)
        self.assertEqual(Table.objects.get(key="inv_raeume").template, {"id": "mein_inventar", "version": 1})
        self.assertEqual(Record.objects.filter(table__key="inv_raeume").count(), len(data["demo"]["inv_raeume"]))

    def test_links_outside_the_export_and_secrets_stay_behind(self):
        Field.objects.create(table=self.tables[1], key="f_kind", label="Kind", type="link", config={"target": "pupil"}, order=900)
        Field.objects.create(table=self.tables[1], key="f_pin", label="PIN", type="secret", order=910)
        Field.objects.create(table=self.tables[1], key="f_n", label="Name doppelt", type="formula",
                             config={"expression": "{f_kind}", "result": "text"}, order=920)
        data, problems, skipped = blueprints.export([self.tables[1]], {"id": "nur_objekte", "title": "Objekte"}, demo=True, user=self.it)
        self.assertEqual(problems, [])
        keys = {f["key"] for f in data["tables"][0]["fields"]}
        self.assertNotIn("f_kind", keys)
        self.assertNotIn("f_n", keys)  # its formula named the field that stayed behind
        self.assertNotIn("f_raum", keys)  # a link to rooms, which are not exported
        self.assertTrue(all("f_pin" not in row for row in data.get("demo", {}).get("inv_objekte", [])))
        self.assertIn("Gegenstände · Kind", " ".join(skipped))

    def test_the_page_downloads_a_file_or_names_the_problems(self):
        self.login(self.it)
        url = reverse("table_export")
        self.assertContains(self.client.get(url), "Als Vorlage exportieren")
        answer = self.client.post(url, {"tables": [str(t.pk) for t in self.tables], "title": "Mein Inventar", "id": "mein_inventar"})
        self.assertEqual(answer["Content-Disposition"], 'attachment; filename="mein_inventar.json"')
        self.assertEqual(json.loads(answer.content)["id"], "mein_inventar")
        refused = self.client.post(url, {"tables": [str(t.pk) for t in self.tables], "title": "Inventar", "id": "inventar"})
        self.assertContains(refused, "vergeben")
        self.login(self.sek)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_school_template_exports_without_its_links_to_pupils(self):
        school = json.loads((blueprints.LIBRARY / "bibliothek.json").read_text(encoding="utf-8"))
        blueprints.apply(school, self.it)
        tables = list(Table.objects.filter(key__in=["buecher", "ausleihen"]).order_by("order"))
        data, problems, skipped = blueprints.export(tables, {"id": "schulbuecher", "title": "Bücher"})
        self.assertEqual(problems, [])
        self.assertIn("Ausleihen · Schüler", " ".join(skipped))
        card = next(p for p in data["prints"] if p["name"] == "Buchkarte")
        columns = [c["key"] for row in card["tree"]["rows"] for col in row["cols"] for el in col["items"] if el["type"] == "list" for c in el["columns"]]
        self.assertTrue(columns)
        self.assertNotIn("f_schueler", columns)
