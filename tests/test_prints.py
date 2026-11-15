import json

from django.urls import reverse
from weasyprint.urls import URLFetchingError

from devices.models import Asset, SupportCase
from people.models import Guardian, Guardianship, Person
from prints import engine
from prints.models import PrintJob, PrintTemplate
from prints.records import load_records, sample_record
from prints.views import _doc
from vault.models import Platform
from vault.services import generate_login

from .base import AppTestCase


class EngineTests(AppTestCase):
    def test_every_default_template_renders_a_pdf(self):
        guardian = Guardian.objects.create(first_name="Eva", last_name="Muster")
        Guardianship.objects.create(person=self.pupil, guardian=guardian)
        asset = Asset.objects.create(number="SuS1", serial="1")
        case = SupportCase.objects.create(person=self.pupil, asset=asset, description="Test")
        staff = Person.objects.create(kind="staff", first_name="L", last_name="K")
        ids = {"pupil": [self.pupil.pk], "staff": [staff.pk], "guardian": [guardian.pk], "asset": [asset.pk], "case": [case.pk], "class": [self.group.pk]}
        for template in PrintTemplate.objects.all():
            records = load_records(template.source, ids[template.source], include_secrets=template.contains_secrets)
            pdf, pages, warnings = engine.render_pdf(_doc(template), records)
            self.assertTrue(pdf.startswith(b"%PDF"), template.name)
            self.assertGreaterEqual(pages, 1)

    def test_all_sample_records_render(self):
        for template in PrintTemplate.objects.all():
            pdf, pages, _ = engine.render_pdf(_doc(template), [sample_record(template.source)])
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_chinese_names_and_fonts_are_embedded(self):
        import pymupdf

        record = sample_record("pupil")
        record["title"] = "王小明 Müller-Šimić"
        template = PrintTemplate.objects.get(name="Zugangsdaten Schüler")
        pdf, _, _ = engine.render_pdf(_doc(template), [record])
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        self.assertIn("王小明", doc[0].get_text())
        self.assertTrue(all(font[1] != "n/a" for font in doc[0].get_fonts()))
        # The bundled fonts, not the system's: a broken font URL falls back without an error.
        names = " ".join(font[3].split("+")[-1] for font in doc[0].get_fonts())
        self.assertIn("Inter", names)
        self.assertIn("Noto-Sans-SC", names)

    def test_overflow_is_reported(self):
        doc = {"name": "T", "source": "pupil", "tree": {"page": {"preset": "a4"}, "rows": [{"cols": [{"w": 12, "items": [{"type": "spacer", "h_mm": 240}, {"type": "spacer", "h_mm": 240}]}]}]}}
        _, pages, warnings = engine.render_pdf(doc, [sample_record("pupil")])
        self.assertGreater(pages, 1)
        self.assertTrue(warnings)

    def test_four_up_fits_four_records_on_one_page(self):
        template = PrintTemplate.objects.get(name__startswith="Zugangskärtchen")
        _, pages, _ = engine.render_pdf(_doc(template), [sample_record("pupil")] * 8)
        self.assertEqual(pages, 2)

    def test_labels_fit_on_sheet(self):
        template = PrintTemplate.objects.get(name="Geräte-Etiketten")
        _, pages, _ = engine.render_pdf(_doc(template), [sample_record("asset")] * 24)
        self.assertEqual(pages, 1)

    def test_hide_empty_platforms(self):
        record = sample_record("pupil")
        record["logins"].append({"key": "leer", "name": "LeerePlattform", "rows": [{"key": "u", "label": "U", "secret": False, "value": ""}]})
        html = engine.render_logins({"hide_empty": True}, record)
        self.assertNotIn("LeerePlattform", html)

    def test_values_are_escaped(self):
        record = sample_record("pupil")
        record["fields"]["first_name"] = "<script>alert(1)</script>"
        html = engine.render_element({"type": "field", "key": "first_name"}, record, engine.school_info())
        self.assertNotIn("<script>", html)

    def test_fetcher_blocks_files_and_network(self):
        fetcher = engine.SafeFetcher()
        for url in ("file:///etc/passwd", "http://169.254.169.254/latest/meta-data/", "https://example.com/x.png"):
            with self.assertRaises(URLFetchingError):
                fetcher.fetch(url)

    def test_code_mode_is_sandboxed(self):
        doc = {"name": "T", "source": "pupil", "tree": {}, "code_mode": True, "code_html": "{{ r.__class__.__mro__ }}"}
        from jinja2.exceptions import SecurityError

        doc["code_html"] = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        with self.assertRaises(SecurityError):
            engine.render_pdf(doc, [sample_record("pupil")])

    def test_code_mode_escapes_data(self):
        record = sample_record("pupil")
        record["title"] = "<b>x</b>"
        html = engine.build_html({"code_mode": True, "code_html": "{{ r.title }}"}, [record])
        self.assertIn("&lt;b&gt;x", html)


class ReportViewTests(AppTestCase):
    def template(self):
        return PrintTemplate.objects.get(name="Zugangsdaten Schüler")

    def test_secret_print_needs_permission(self):
        generate_login(self.pupil, self.m365)
        self.login(self.sl)
        response = self.client.post(reverse("report_run"), {"template": self.template().pk, "ids": str(self.pupil.pk)})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PrintJob.objects.exists())

    def test_print_is_logged_and_not_cached(self):
        generate_login(self.pupil, self.m365)
        self.login(self.sek)
        response = self.client.post(reverse("report_run"), {"template": self.template().pk, "ids": str(self.pupil.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("no-store", response["Cache-Control"])
        job = PrintJob.objects.get()
        self.assertTrue(job.with_secrets)
        self.assertEqual(job.user, self.sek)

    def test_decrypted_password_reaches_pdf(self):
        import pymupdf

        credential = generate_login(self.pupil, self.m365)
        self.login(self.sek)
        response = self.client.post(reverse("report_run"), {"template": self.template().pk, "ids": str(self.pupil.pk)})
        text = pymupdf.open(stream=response.content, filetype="pdf")[0].get_text()
        self.assertIn(credential.get_secret("password"), text)

    def test_school_management_can_print_lists(self):
        self.login(self.sl)
        template = PrintTemplate.objects.get(name="Klassenliste")
        response = self.client.post(reverse("report_run"), {"template": template.pk, "class_id": self.group.pk})
        self.assertEqual(response.status_code, 200)

    def test_designer_is_it_only(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("template_designer", args=[self.template().pk])).status_code, 403)

    def test_preview_uses_sample_data_only(self):
        credential = generate_login(self.pupil, self.m365)
        self.login(self.it)
        response = self.client.post(reverse("template_preview", args=[self.template().pk]), json.dumps({"tree": self.template().tree}), content_type="application/json")
        import pymupdf

        text = pymupdf.open(stream=response.content, filetype="pdf")[0].get_text()
        self.assertIn("Müller-Šimić", text)
        self.assertNotIn(credential.get_secret("password"), text)

    def test_save_keeps_versions_and_restore_works(self):
        self.login(self.it)
        template = self.template()
        original = template.tree
        url = reverse("template_save", args=[template.pk])
        self.client.post(url, json.dumps({"tree": {"page": {"preset": "a5"}, "rows": []}, "name": template.name}), content_type="application/json")
        template.refresh_from_db()
        self.assertEqual(template.version, 2)
        self.assertFalse(template.contains_secrets)
        self.client.post(reverse("template_restore", args=[template.pk, 1]))
        template.refresh_from_db()
        self.assertEqual(template.tree, original)
        self.assertTrue(template.contains_secrets)
