"""Own tables, step 7 (docs/TABLES-PLAN.md §7): tables as print sources, lists over links, printing from lists and records."""
import io
import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from core.context_user import set_current_user
from core.models import Role
from prints import engine
from prints.models import PrintTemplate
from prints.records import available_sources, field_catalog, load_records, sample_record
from prints.views import _doc
from tables import links, services
from tables.models import EDIT, READ

from .test_tables import TableCase


def pdf_text(pdf):
    import pymupdf

    return " ".join(page.get_text() for page in pymupdf.open(stream=pdf, filetype="pdf"))


class PrintCase(TableCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.loans = services.create_table("Ausleihen")
        services.set_rights(self.loans, {Role.SEKRETARIAT: EDIT})
        self.book = services.create_field(self.loans, "Buch", "link", config={"target": "record", "table": "buecher", "reverse": "Ausleihen"})
        self.reader = services.create_field(self.loans, "Leser", "link", config={"target": "pupil"})
        self.cover = services.create_field(self.table, "Umschlag", "image")
        self.momo = services.create_record(self.table, {"f_name": "Momo", "f_preis": "12,5", "f_genre": "Roman"})
        self.loan = services.create_record(self.loans, {"f_name": "Ausleihe 1"})
        links.set_targets(self.loan, self.book, [str(self.momo.pk)])
        links.set_targets(self.loan, self.reader, [str(self.pupil.pk)])
        self.template = PrintTemplate.objects.create(name="Buchkarte", source="table:buecher", tree={"page": {"preset": "a4"}, "rows": [{"cols": [{"w": 12, "items": [
            {"type": "heading", "text": "{{titel}}"}, {"type": "text", "text": "Preis: {{f_preis}}"},
            {"type": "list", "link": "@ausleihen.f_buch", "columns": [{"key": "titel", "title": "Ausleihe"}, {"key": "f_leser", "title": "Leser"}]},
        ]}]}]})
        set_current_user(None)


class SourceTests(PrintCase):
    def test_tables_are_sources_for_those_who_may_read_them(self):
        self.assertIn(("table:buecher", "Tabelle: Bücher"), available_sources(self.sl))
        self.assertNotIn("table:ausleihen", dict(available_sources(self.sl)))
        self.assertIn("table:ausleihen", dict(available_sources(self.it)))
        self.assertEqual([f["key"] for f in field_catalog("table:buecher")][:3], ["titel", "f_name", "f_preis"])
        self.assertTrue(engine.render_pdf(_doc(self.template), [sample_record("table:buecher")])[0].startswith(b"%PDF"))

    def test_records_carry_text_lists_and_photo(self):
        image = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(image, "PNG")
        links.add_file(self.momo, self.cover, SimpleUploadedFile("u.png", image.getvalue(), content_type="image/png"))
        set_current_user(self.sek)
        record = load_records("table:buecher", [str(self.momo.pk)])[0]
        self.assertEqual((record["fields"]["f_preis"], record["fields"]["f_genre"], record["title"]), ("12,50 €", "Roman", "Momo"))
        self.assertTrue(record["photo"].startswith("data:image/png;base64,"))
        self.assertEqual(record["lists"]["@ausleihen.f_buch"], [{"titel": "Ausleihe 1", "f_name": "Ausleihe 1", "f_buch": "Momo", "f_leser": self.pupil.display_name}])
        text = pdf_text(engine.render_pdf(_doc(self.template), [record])[0])
        self.assertIn("Preis: 12,50", text)
        self.assertIn("Ausleihe 1", text)

    def test_a_list_from_a_table_the_printer_may_not_read_stays_empty(self):
        set_current_user(self.sl)
        record = load_records("table:buecher", [str(self.momo.pk)])[0]
        self.assertEqual(record["lists"]["@ausleihen.f_buch"], [])
        set_current_user(self.sl)
        services.set_rights(self.table, {Role.SCHULLEITUNG: "none"})
        self.assertEqual(load_records("table:buecher", [str(self.momo.pk)]), [])


class PageTests(PrintCase):
    def test_print_selected_and_all_records(self):
        services.create_record(self.table, {"f_name": "Krabat"})
        self.login(self.sek)
        answer = self.client.post(reverse("report_run"), {"template": self.template.pk, "ids": str(self.momo.pk)})
        self.assertEqual(answer["Content-Type"], "application/pdf")
        self.assertIn("Ausleihe 1", pdf_text(answer.content))
        everything = self.client.post(reverse("report_run"), {"template": self.template.pk, "scope": "all"})
        self.assertIn("Krabat", pdf_text(everything.content))

    def test_without_the_tables_right_there_is_nothing_to_print(self):
        services.set_rights(self.table, {Role.SCHULLEITUNG: "none"})
        self.login(self.sl)
        self.assertEqual(self.client.post(reverse("report_run"), {"template": self.template.pk, "ids": str(self.momo.pk)}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("reports")), "Buchkarte")

    def test_designer_new_template_and_where_templates_show(self):
        self.login(self.it)
        answer = self.client.post(reverse("template_new"), {"source": "table:buecher", "name": "Etikett"})
        template = PrintTemplate.objects.get(name="Etikett")
        self.assertRedirects(answer, reverse("template_designer", args=[template.pk]))
        config = self.client.get(reverse("template_designer", args=[template.pk])).context["config"]
        self.assertEqual([item["key"] for item in config["lists"]], ["@ausleihen.f_buch"])
        self.assertContains(self.client.get(reverse("template_designer", args=[template.pk])), "Tabelle: Bücher")
        self.assertEqual(self.client.post(reverse("template_new"), {"source": "table:gibt-es-nicht"}).status_code, 404)
        preview = self.client.post(reverse("template_preview", args=[self.template.pk]), json.dumps({"tree": self.template.tree}), content_type="application/json")
        self.assertEqual(preview.status_code, 200)
        page = self.client.get(reverse("table_records", args=["buecher"]))
        self.assertIn("Buchkarte", [t["name"] for t in page.context["config"]["templates"]])
        self.assertContains(self.client.get(reverse("record_detail", args=["buecher", self.momo.pk])), "Buchkarte")
