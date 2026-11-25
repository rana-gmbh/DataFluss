"""A published site never holds what was not chosen (publish/, docs/publish.md "Sicherheit").

Every value that must stay private is a unique marker. The site is built, and then every file of it (pages,
search index, stylesheets, pictures, files) is searched for each marker: none may be there.
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from core.context_user import set_current_user
from core.models import Role, UserRole
from publish import render, snapshot
from publish.models import Publication
from tables import links, services
from tables.models import READ, Field, Record

from .base import AppTestCase

PRIVATE = {
    "not_chosen": "NICHTGEWAEHLT7731",
    "role_hidden": "ROLLENVERBORGEN4412",
    "secret": "GEHEIMNIS9981",
    "formula": "NICHTGEWAEHLT7731",
    "other_table_title": "AKTENTITEL6620",
    "other_table_value": "AKTENWERT1188",
    "no_role_right": "KEINLESERECHT2290",
    "binned": "PAPIERKORB4040",
    "archived_field": "ARCHIVIERT5050",
    "file_name": "SCANNAME3301",
    "file_content": "DATEIINHALT5521",
    "exif": "EXIFTEXT8080",
    "pupil_first": "Lena",
    "pupil_last": "Muster",
    "table_hidden_title": "UNSICHTBARERFILM1919",
}
PUBLIC = {"title": "Momo", "year": "1986", "linked": "Die unendliche Geschichte"}


def picture_with_exif(name="poster.jpg"):
    image = Image.new("RGB", (900, 1200), "teal")
    exif = Image.Exif()
    exif[0x010E] = PRIVATE["exif"]  # ImageDescription
    exif[0x8825] = {1: "N", 2: (52.0, 31.0, 12.0)}  # GPS
    data = io.BytesIO()
    image.save(data, "JPEG", exif=exif)
    return SimpleUploadedFile(name, data.getvalue(), content_type="image/jpeg")


class PublishCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        role = Role.SCHULLEITUNG

        self.films = services.create_table("Filme")
        services.set_rights(self.films, {role: READ})
        self.year = services.create_field(self.films, "Jahr", "number")
        self.note = services.create_field(self.films, "Notiz", "text")
        self.internal = services.create_field(self.films, "Intern", "text")
        self.code = services.create_field(self.films, "Code", "secret")
        self.poster = services.create_field(self.films, "Poster", "image")
        self.scan = services.create_field(self.films, "Scan", "file")
        self.old = services.create_field(self.films, "Alt", "text")
        self.sum = services.create_field(self.films, "Aus Notiz", "formula", config=services.formula_config(self.films, {"expression": "{Notiz}"}))
        self.pupils = services.create_field(self.films, "Schüler", "link", config=links.clean_config("link", {"target": "pupil", "multiple": "1"}))
        self.related = services.create_field(self.films, "Verwandt", "link", config=links.clean_config("link", {"target": "table:filme", "multiple": "1"}))

        self.files_table = services.create_table("Akten")
        services.set_rights(self.files_table, {role: READ})
        self.file_note = services.create_field(self.files_table, "Wert", "text")
        self.dossier = services.create_field(self.films, "Akte", "link", config=links.clean_config("link", {"target": "table:akten"}))

        self.staff_table = services.create_table("Personalakten")  # the role may not read it
        services.set_rights(self.staff_table, {role: "none"})

        self.momo = services.create_record(self.films, {"f_name": PUBLIC["title"], "f_jahr": PUBLIC["year"], "f_notiz": PRIVATE["not_chosen"],
                                                        "f_intern": PRIVATE["role_hidden"], "f_code": PRIVATE["secret"], "f_alt": PRIVATE["archived_field"]})
        self.story = services.create_record(self.films, {"f_name": PUBLIC["linked"]})
        binned = services.create_record(self.films, {"f_name": PRIVATE["binned"]})
        binned.recycle()
        dossier = services.create_record(self.files_table, {"f_name": PRIVATE["other_table_title"], "f_wert": PRIVATE["other_table_value"]})
        services.create_record(self.staff_table, {"f_name": PRIVATE["no_role_right"]})
        links.set_targets(self.momo, self.pupils, [str(self.pupil.pk)])
        links.set_targets(self.momo, self.related, [str(self.story.pk)])
        links.set_targets(self.momo, self.dossier, [str(dossier.pk)])
        links.add_file(self.momo, self.poster, picture_with_exif(), self.it)
        links.add_file(self.momo, self.scan, SimpleUploadedFile(f"{PRIVATE['file_name']}.pdf", f"%PDF {PRIVATE['file_content']}".encode()), self.it)
        Field.objects.filter(pk=self.old.pk).update(archived=True)

        row = UserRole.objects.get(key=role)
        row.fields = {"table:filme": {"f_intern": "hidden"}}
        row.save()
        set_current_user(None)

        chosen = ["f_name", "f_jahr", "f_intern", "f_code", "f_poster", "f_alt", "f_aus_notiz", "f_schueler", "f_verwandt", "f_akte"]
        self.publication = Publication.objects.create(
            name="Filmabend", slug="filmabend", language="de", role=role,
            choice={"filme": {"fields": chosen, "layout": "gallery", "files": False},
                    "personalakten": {"fields": ["f_name"]}})

    def seen(self, publication=None):
        """The hash the confirmation page sends back for what it shows now."""
        from publish import service

        publication = Publication.objects.get(pk=(publication or self.publication).pk)
        return service.state_hash(service.confirmed_state(publication))

    def confirmed(self, publication=None):
        """The publication, confirmed as it is now (as after "Ja, veröffentlichen")."""
        from publish import service

        publication = Publication.objects.get(pk=(publication or self.publication).pk)
        self.assertTrue(service.confirm(publication, self.seen(publication)))
        return publication

    def site_text(self, site):
        """Every file of the site as text (bytes decoded leniently), keyed by path."""
        return {path: site.read(path).decode("utf-8", "replace") for path in site.files}


class LeakTests(PublishCase):
    def test_nothing_private_in_any_file(self):
        site = render.build(self.publication)
        files = self.site_text(site)
        self.assertIn("index.html", files)
        self.assertIn(f"filme/{self.momo.pk}.html", files)
        everything = "\n".join(files.values())
        for name, marker in PRIVATE.items():
            with self.subTest(name):
                self.assertNotIn(marker, everything)
        for name, text in PUBLIC.items():
            with self.subTest(name):
                self.assertIn(text, files[f"filme/{self.momo.pk}.html"])

    def test_the_search_finds_what_is_chosen(self):
        """The control for the test above: chosen values do show up, so the markers would be found."""
        self.publication.choice = {"filme": {"fields": ["f_name", "f_notiz", "f_akte", "f_aus_notiz"]}, "akten": {"fields": ["f_name"]}}
        files = self.site_text(render.build(self.publication))
        page = files[f"filme/{self.momo.pk}.html"]
        self.assertIn(PRIVATE["not_chosen"], page)
        self.assertIn(PRIVATE["other_table_title"], page)
        self.assertIn(PRIVATE["not_chosen"].lower(), files["_assets/search.js"])
        self.assertNotIn(PRIVATE["other_table_value"], "\n".join(files.values()))
        self.assertNotIn(PRIVATE["role_hidden"], "\n".join(files.values()))

    def test_tables_without_choice_or_right_are_not_there(self):
        site = render.build(self.publication)
        folders = {path.split("/")[0] for path in site.files}
        self.assertIn("filme", folders)
        self.assertNotIn("akten", folders)
        self.assertNotIn("personalakten", folders)

    def test_pictures_are_new_files_without_metadata(self):
        site = render.build(self.publication)
        pictures = [path for path in site.files if path.startswith("_media/")]
        self.assertTrue(pictures)
        for path in pictures:
            data = site.read(path)
            self.assertNotIn(PRIVATE["exif"].encode(), data)
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertFalse(dict(image.getexif()))
                self.assertLessEqual(max(image.size), snapshot.LARGE)

    def test_files_only_when_the_table_publishes_files(self):
        Publication.objects.filter(pk=self.publication.pk).update(choice={"filme": {"fields": ["f_name", "f_scan"], "files": False}})
        self.publication.refresh_from_db()
        site = render.build(self.publication)
        self.assertFalse([p for p in site.files if p.endswith(".pdf")])
        self.publication.choice = {"filme": {"fields": ["f_name", "f_scan"], "files": True}}
        site = render.build(self.publication)
        pdfs = [p for p in site.files if p.endswith(".pdf")]
        self.assertEqual(len(pdfs), 1)
        self.assertIn(PRIVATE["file_content"].encode(), site.read(pdfs[0]))

    def test_new_fields_are_never_published_by_themselves(self):
        extra = services.create_field(self.films, "Neu", "text")
        services.set_value(Record.objects.get(pk=self.momo.pk), extra, "NEUESFELD6006")
        set_current_user(None)
        everything = "\n".join(self.site_text(render.build(self.publication)).values())
        self.assertNotIn("NEUESFELD6006", everything)

    def test_a_field_hidden_later_disappears(self):
        self.assertIn("1986", "\n".join(self.site_text(render.build(self.publication)).values()))
        row = UserRole.objects.get(key=Role.SCHULLEITUNG)
        row.fields = {"table:filme": {"f_intern": "hidden", "f_jahr": "hidden"}}
        row.save()
        set_current_user(None)
        self.assertNotIn("1986", "\n".join(self.site_text(render.build(PublicationReload(self.publication))).values()))

    def test_the_table_title_hidden_from_the_role_hides_the_table(self):
        services.set_rights(self.films, {Role.SCHULLEITUNG: "none"})
        set_current_user(None)
        site = render.build(Publication.objects.get(pk=self.publication.pk))
        self.assertFalse([p for p in site.files if p.startswith("filme/")])
        self.assertNotIn(PUBLIC["title"], "\n".join(self.site_text(site).values()))

    def test_the_check_stops_a_snapshot_with_a_field_not_seen(self):
        from publish.viewer import PublicationViewer

        data = snapshot.build(self.publication)
        data["tables"][0]["field_keys"].append("f_notiz")
        viewer = PublicationViewer(self.publication)
        with self.assertRaises(snapshot.Unsafe):
            snapshot.check(data, viewer, viewer.readable_tables())

    def test_the_viewer_cannot_do_anything(self):
        from publish.viewer import PublicationViewer

        viewer = PublicationViewer(self.publication)
        for capability in ("pupil.view", "staff.view", "device.view", "vault.view", "tables.design", "print.run", "admin.users"):
            self.assertFalse(viewer.can(capability))
        self.assertEqual(services.right(viewer, self.films), READ)
        self.assertFalse(services.may_edit(viewer, self.films))
        self.assertEqual([f.key for f in services.fields_of(self.films, user=viewer) if f.key in ("f_notiz", "f_intern", "f_code")], [])


def PublicationReload(publication):
    return Publication.objects.get(pk=publication.pk)
