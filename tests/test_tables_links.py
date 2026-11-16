"""Own tables, step 4 (docs/TABLES-PLAN.md): link fields, file fields, what a user sees of a target, sync and the night."""
import io
import json
import shutil
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from archive import nightly
from core import permissions
from core.models import Role
from devices.models import Asset
from sync.scope import Scope
from tables import links, services
from tables.models import EDIT, READ, Attachment, Link, Record

from .test_tables_records import RecordCase


def png(name="bild.png"):
    data = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(data, "PNG")
    return SimpleUploadedFile(name, data.getvalue(), content_type="image/png")


class LinkCase(RecordCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.loans = services.create_table("Ausleihen")
        services.set_rights(self.loans, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.book = services.create_field(self.loans, "Buch", "link", config=links.clean_config("link", {"target": "table:buecher", "reverse": "Ausleihen"}))
        self.readers = services.create_field(self.loans, "Leser", "link", config=links.clean_config("link", {"target": "pupil", "multiple": "1"}))
        self.device = services.create_field(self.loans, "Gerät", "link", config=links.clean_config("link", {"target": "asset"}))
        self.photo = services.create_field(self.loans, "Foto", "image")
        self.files = services.create_field(self.loans, "Belege", "file", config={"multiple": True})
        self.loan = services.create_record(self.loans, {"f_name": "Ausleihe 1"})
        self.other = self.pupil.__class__.objects.create(kind="pupil", first_name="Ben", last_name="Zweiter")
        self.asset = Asset.objects.create(number="NB-1")

    def url(self, name, *args):
        return reverse(name, args=["ausleihen", *args])


class LinkServiceTests(LinkCase):
    def test_config_and_refusals(self):
        self.assertEqual(self.book.config, {"target": "record", "table": "buecher", "reverse": "Ausleihen"})
        with self.assertRaisesMessage(ValidationError, "worauf"):
            links.clean_config("link", {"target": "table:gibt-es-nicht"})
        with self.assertRaisesMessage(ValidationError, "Unbekannter Datensatz"):
            links.set_targets(self.loan, self.book, [str(self.pupil.pk)])  # a pupil is not a book
        with self.assertRaisesMessage(ValidationError, "nur ein"):
            links.set_targets(self.loan, self.book, [str(self.momo.pk), str(services.create_record(self.table).pk)])

    def test_single_link_keeps_one_row_with_a_derived_id(self):
        links.set_targets(self.loan, self.book, [str(self.momo.pk)])
        other = services.create_record(self.table, {"f_name": "Krabat"})
        links.add_target(self.loan, self.book, other.pk)
        rows = list(Link.objects.filter(record=self.loan, field="f_buch"))
        self.assertEqual([(r.pk, r.target_id) for r in rows], [(links.link_id(self.loan.pk, "f_buch"), other.pk)])
        links.remove_target(self.loan, self.book, other.pk)
        self.assertEqual(links.targets(self.loan, self.book), [])
        self.assertEqual(Link.objects.filter(record=self.loan, field="f_buch").count(), 1)  # kept, empty

    def test_multiple_links_add_and_remove_rows_per_target(self):
        links.add_target(self.loan, self.readers, self.pupil.pk)
        links.add_target(self.loan, self.readers, self.other.pk)
        links.add_target(self.loan, self.readers, self.pupil.pk)
        self.assertEqual(links.targets(self.loan, self.readers), [self.pupil.pk, self.other.pk])
        self.assertTrue(Link.objects.filter(pk=links.link_id(self.loan.pk, "f_leser", self.other.pk)).exists())
        links.remove_target(self.loan, self.readers, self.pupil.pk)
        self.assertEqual(links.targets(self.loan, self.readers), [self.other.pk])

    def test_what_a_user_sees_of_targets(self):
        hidden = services.create_table("Intern")
        secret = services.create_record(hidden, {"f_name": "Nur IT"})
        note = services.create_field(self.loans, "Ablage", "link", config={"target": "record", "table": hidden.key})
        links.set_targets(self.loan, note, [str(secret.pk)])
        links.set_targets(self.loan, self.device, [str(self.asset.pk)])
        self.assertEqual(links.display(self.loan, note, self.it), "Nur IT")
        self.assertEqual(links.display(self.loan, note, self.sl), "nicht sichtbar")
        self.assertEqual(links.display(self.loan, self.device, self.sl), "NB-1")
        with mock.patch.dict(permissions.CAPABILITIES, {"device.view": {Role.IT}}):
            self.assertEqual(links.display(self.loan, self.device, self.sl), "nicht sichtbar")
            self.assertEqual(links.candidates(self.sl, self.device, "NB"), [])
        self.assertEqual(links.candidates(self.sl, note, ""), [])
        self.assertEqual([c["title"] for c in links.candidates(self.sek, self.book, "mom")], ["Momo"])

    def test_backlinks_follow_the_source_tables_right_and_the_bin(self):
        links.set_targets(self.loan, self.book, [str(self.momo.pk)])
        links.add_target(self.loan, self.readers, self.pupil.pk)
        groups = links.backlinks(self.sl, "record", self.momo.pk)
        self.assertEqual([(g["title"], [r["title"] for r in g["records"]]) for g in groups], [("Ausleihen", ["Ausleihe 1"])])
        self.assertEqual(links.backlinks(self.sl, "pupil", self.pupil.pk)[0]["title"], "Ausleihen · Leser")
        services.set_rights(self.loans, {Role.SCHULLEITUNG: "none"})
        self.assertEqual(links.backlinks(self.sl, "record", self.momo.pk), [])
        self.loan.recycle()
        self.assertEqual(links.backlinks(self.it, "record", self.momo.pk), [])

    def test_values_never_hold_links_or_files(self):
        self.loan.values = {**self.loan.values, "f_buch": str(self.momo.pk)}
        self.assertTrue(services.check_record(self.loan))

    def test_going_back_to_one_target_needs_records_with_one(self):
        links.add_target(self.loan, self.readers, self.pupil.pk)
        links.add_target(self.loan, self.readers, self.other.pk)
        with self.assertRaisesMessage(ValidationError, "mehrere"):
            services.edit_field(self.readers, {"label": "Leser"}, "all")
        links.remove_target(self.loan, self.readers, self.other.pk)
        services.edit_field(self.readers, {"label": "Leser"}, "all")
        self.assertNotIn("multiple", self.readers.config)


class FileTests(LinkCase):
    def test_upload_replace_and_refuse(self):
        first = links.add_file(self.loan, self.photo, png("a.png"), self.it)
        self.assertTrue(first.file.name.startswith(f"tables/ausleihen/{self.loan.pk}/"))
        links.add_file(self.loan, self.photo, png("b.png"), self.it)
        self.assertEqual([a.name for a in links.files(self.loan, self.photo)], ["b.png"])  # one image: replaced
        with self.assertRaisesMessage(ValidationError, "Bild"):
            links.add_file(self.loan, self.photo, SimpleUploadedFile("x.png", b"kein Bild"), self.it)
        links.add_file(self.loan, self.files, SimpleUploadedFile("a.pdf", b"%PDF-1"), self.it)
        links.add_file(self.loan, self.files, SimpleUploadedFile("b.pdf", b"%PDF-2"), self.it)
        self.assertEqual(len(links.files(self.loan, self.files)), 2)

    def test_files_open_only_through_the_table(self):
        item = links.add_file(self.loan, self.files, SimpleUploadedFile("beleg.txt", b"geheim"), self.it)
        self.login(self.sl)
        answer = self.client.get(self.url("record_file", self.loan.pk, item.pk))
        self.assertEqual(b"".join(answer.streaming_content), b"geheim")
        self.assertIn("attachment", answer["Content-Disposition"])
        self.assertEqual(self.client.get(f"/media/{item.file.name}").status_code, 403)
        services.set_rights(self.loans, {Role.SCHULLEITUNG: "none"})
        self.assertEqual(self.client.get(self.url("record_file", self.loan.pk, item.pk)).status_code, 403)

    def test_record_page_uploads_and_deletes(self):
        self.login(self.sek)
        answer = self.client.post(self.url("record_files", self.loan.pk, "f_foto"), {"file": png()})
        self.assertContains(answer, "bild.png")
        bad = self.client.post(self.url("record_files", self.loan.pk, "f_foto"), {"file": SimpleUploadedFile("x.png", b"nein")})
        self.assertIn("toast", json.loads(bad["HX-Trigger"]))
        item = Attachment.objects.get(record=self.loan, field="f_foto")
        self.client.post(self.url("record_files", self.loan.pk, "f_foto"), {"delete": str(item.pk)})
        self.assertFalse(Attachment.objects.filter(pk=item.pk).exists())


class LinkPageTests(LinkCase):
    def test_picker_offers_visible_targets_and_links_them(self):
        self.login(self.sek)
        options = self.client.get(self.url("record_link_options", self.loan.pk, "f_buch"), {"q": "mom"})
        self.assertContains(options, "Momo")
        answer = self.client.post(self.url("record_link", self.loan.pk, "f_buch"), {"action": "add", "target": str(self.momo.pk)})
        self.assertContains(answer, "Momo")
        self.assertNotContains(self.client.get(self.url("record_link_options", self.loan.pk, "f_buch"), {"q": "mom"}), "Momo")
        self.client.post(self.url("record_link", self.loan.pk, "f_buch"), {"action": "remove", "target": str(self.momo.pk)})
        self.assertEqual(links.targets(self.loan, self.book), [])

    def test_a_hidden_target_cannot_be_linked_by_id(self):
        hidden = services.create_table("Intern")
        secret = services.create_record(hidden, {"f_name": "Nur IT"})
        services.create_field(self.loans, "Ablage", "link", config={"target": "record", "table": hidden.key})
        self.login(self.sek)
        answer = self.client.post(self.url("record_link", self.loan.pk, "f_ablage"), {"action": "add", "target": str(secret.pk)})
        self.assertEqual(answer.status_code, 403)
        self.login(self.sl)
        self.assertEqual(self.client.post(self.url("record_link", self.loan.pk, "f_buch"), {"action": "add", "target": str(self.momo.pk)}).status_code, 403)

    def test_list_rows_and_backlink_sections(self):
        links.set_targets(self.loan, self.book, [str(self.momo.pk)])
        links.add_target(self.loan, self.readers, self.pupil.pk)
        links.set_targets(self.loan, self.device, [str(self.asset.pk)])
        self.login(self.sl)
        row = self.client.get(self.url("table_rows")).json()[0]
        self.assertEqual((row["f_buch"], row["f_leser"], row["f_geraet"]), ("Momo", self.pupil.display_name, "NB-1"))
        self.assertContains(self.client.get(reverse("table_backlinks", args=["record", self.momo.pk])), "Ausleihe 1")
        self.assertContains(self.client.get(reverse("table_backlinks", args=["pupil", self.pupil.pk])), "Ausleihe 1")
        self.assertContains(self.client.get(reverse("table_backlinks", args=["asset", self.asset.pk])), "Ausleihe 1")
        self.assertEqual(self.client.get(reverse("table_backlinks", args=["pupil", self.other.pk])).content, b"")
        self.assertEqual(self.client.get(reverse("table_backlinks", args=["erfunden", self.other.pk])).status_code, 404)


class LinkSyncTests(LinkCase):
    def test_links_and_files_travel_with_readable_tables_and_are_checked(self):
        links.set_targets(self.loan, self.book, [str(self.momo.pk)])
        item = links.add_file(self.loan, self.photo, png(), self.it)
        held = Scope(self.sl).records()
        self.assertIn(links.link_id(self.loan.pk, "f_buch"), held[Link])
        self.assertIn(item.pk, held[Attachment])
        self.assertFalse(Scope(self.sl).may_write(Link, links.link_id(self.loan.pk, "f_buch"), ["target_id"]))
        self.assertTrue(Scope(self.sek).allows(Link, links.link_id(self.loan.pk, "f_buch")))
        forged = Link.objects.create(pk=uuid.uuid4(), record=self.loan, field="f_buch", target_kind="record", target_id=self.momo.pk)
        self.assertFalse(Scope(self.sek).allows(Link, forged.pk))  # not the id every computer derives
        wrong_kind = Link.objects.create(pk=links.link_id(self.loan.pk, "f_geraet"), record=self.loan, field="f_geraet", target_kind="pupil", target_id=self.pupil.pk)
        self.assertFalse(Scope(self.sek).allows(Link, wrong_kind.pk))
        self.assertTrue(Scope(self.sek).allows(Attachment, item.pk))

    def test_the_night_removes_links_to_what_is_gone_and_files_of_emptied_records(self):
        links.set_targets(self.loan, self.device, [str(self.asset.pk)])
        self.asset.delete()
        doomed = services.create_record(self.loans, {"f_name": "Alt"})
        item = links.add_file(doomed, self.files, SimpleUploadedFile("alt.txt", b"x"), self.it)
        stored = Path(self.media) / item.file.name
        doomed.recycle()
        Record.everything.filter(pk=doomed.pk).update(deleted_at=timezone.now() - timedelta(days=Record.KEEP_DAYS + 1))
        with self.captureOnCommitCallbacks(execute=True):
            nightly.empty_bins()
        self.assertEqual(links.targets(self.loan, self.device), [])
        self.assertFalse(Record.everything.filter(pk=doomed.pk).exists())
        self.assertFalse(stored.exists())
