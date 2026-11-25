"""Publications (publish/, docs/publish.md): settings pages, rights, targets, the sites under /p/<slug>/ and
republishing only what changed."""
import io
import json
import shutil
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.urls import reverse

from core.context_user import set_current_user
from core.models import Role, UserRole
from publish import render, service, targets
from publish.models import Publication
from tables import links, services
from tables.models import Record

from .test_publish_safety import PublishCase, picture_with_exif


class Synchronous:
    """Runs publications in the request: a thread would not see the test's transaction."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(service, "IN_THREAD", False)
        patcher.start()
        self.addCleanup(patcher.stop)


class PageTests(Synchronous, PublishCase):
    def test_only_with_the_switch(self):
        self.login(self.sl)
        self.assertEqual(self.client.get(reverse("publication_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("publication_detail", args=[self.publication.pk])).status_code, 403)
        row = UserRole.objects.get(key=Role.SCHULLEITUNG)
        row.extras = [*row.extras, "publish.manage"]
        row.save()
        self.assertEqual(self.client.get(reverse("publication_list")).status_code, 200)

    def test_create_choose_and_publish_on_this_server(self):
        self.login(self.it)
        response = self.client.post(reverse("publication_list"), {"name": "Unsere Filme"})
        publication = Publication.objects.get(name="Unsere Filme")
        self.assertRedirects(response, reverse("publication_detail", args=[publication.pk]))
        self.assertEqual(publication.slug, "unsere-filme")
        page = self.client.get(reverse("publication_detail", args=[publication.pk])).content.decode()
        self.assertIn('name="t:filme"', page)
        self.assertNotIn('name="t:personalakten"', page)  # the role may not read it, so it is not offered

        self.client.post(reverse("publication_detail", args=[publication.pk]), {"section": "settings", "name": "Unsere Filme", "slug": "filme-online",
                                                                                  "language": "de", "role": Role.SCHULLEITUNG})
        self.client.post(reverse("publication_detail", args=[publication.pk]), {
            "section": "content", "t:filme": "1", "f:filme": ["f_jahr", "f_notiz", "f_code"], "layout:filme": "gallery"})
        publication.refresh_from_db()
        self.assertEqual(publication.choice["filme"]["fields"], ["f_jahr", "f_name", "f_notiz"])  # the title always, secrets never
        self.assertFalse(publication.indexable)
        self.client.post(reverse("publication_detail", args=[publication.pk]), {"section": "target", "target": "server"})

        # The first publish asks first.
        self.assertRedirects(self.client.post(reverse("publication_confirm", args=[publication.pk]), {"kind": "publish"}),
                             reverse("publication_confirm", args=[publication.pk]))
        confirm = self.client.get(reverse("publication_confirm", args=[publication.pk])).content.decode()
        self.assertIn("Notiz", confirm)
        self.assertIn("Jeder, der die Adresse kennt", confirm)
        self.client.post(reverse("publication_confirm", args=[publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen(publication)})
        publication.refresh_from_db()
        self.assertEqual(publication.status, Publication.DONE, publication.error)
        self.assertIsNotNone(publication.published_at)
        self.assertFalse(service.needs_confirmation(publication))

        self.client.logout()
        home = self.client.get("/p/filme-online/")
        self.assertEqual(home.status_code, 200)
        body = b"".join(home.streaming_content).decode()
        self.assertIn("Unsere Filme", body)
        self.assertIn("noindex", body)
        self.assertEqual(home["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("script-src 'self'", home["Content-Security-Policy"])
        self.assertNotIn("unsafe-eval", home["Content-Security-Policy"])
        record = self.client.get(f"/p/filme-online/filme/{self.momo.pk}.html")
        self.assertEqual(record.status_code, 200)
        self.assertEqual(self.client.get("/p/filme-online/filme/").status_code, 200)
        self.assertEqual(self.client.get("/p/filme-online/robots.txt").status_code, 200)
        self.assertEqual(self.client.get("/p/filme-online/datafluss-manifest.json").status_code, 404)
        self.assertEqual(self.client.get("/p/filme-online/../../etc/passwd").status_code, 404)
        self.assertEqual(self.client.get("/p/filme-online/%2e%2e/x").status_code, 404)
        self.assertEqual(self.client.get("/p/gibt-es-nicht/").status_code, 404)
        # The settings, the preview and the ZIP stay behind the login.
        self.assertEqual(self.client.get(reverse("publication_preview", args=[publication.pk])).status_code, 302)

    def test_republish_without_asking_until_the_choice_changes(self):
        self.login(self.it)
        self.publication.target = Publication.SERVER
        self.publication.save()
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen()})
        self.publication.refresh_from_db()
        self.assertFalse(service.needs_confirmation(self.publication))
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish"})
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.DONE)
        self.client.post(reverse("publication_detail", args=[self.publication.pk]), {"section": "content", "t:filme": "1", "f:filme": ["f_jahr"]})
        self.publication.refresh_from_db()
        self.assertTrue(service.needs_confirmation(self.publication))

    def test_preview_inside_the_app(self):
        self.login(self.it)
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "preview"})
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.DONE, self.publication.error)
        self.assertIsNone(self.publication.published_at)  # a preview publishes nothing
        page = self.client.get(reverse("publication_preview", args=[self.publication.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Filmabend", b"".join(page.streaming_content).decode())
        css = self.client.get(reverse("publication_preview_file", args=[self.publication.pk, "_assets/site.css"]))
        self.assertEqual(css["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(self.client.get(f"/p/{self.publication.slug}/").status_code, 404)

    def test_zip(self):
        self.login(self.it)
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen()})
        response = self.client.get(reverse("publication_zip", args=[self.publication.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as archive:
            names = archive.namelist()
        self.assertIn("index.html", names)
        self.assertIn(f"filme/{self.momo.pk}.html", names)
        self.assertIn("_assets/app.css", names)

    def test_withdraw_and_delete_take_the_site_off_this_server(self):
        self.login(self.it)
        self.publication.target = Publication.SERVER
        self.publication.save()
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen()})
        self.assertTrue(default_storage.exists("published/filmabend/index.html"))
        self.client.post(reverse("publication_withdraw", args=[self.publication.pk]))
        self.assertFalse(default_storage.exists("published/filmabend/index.html"))
        self.assertEqual(self.client.get("/p/filmabend/").status_code, 404)
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen()})
        self.client.post(reverse("publication_detail", args=[self.publication.pk]), {"section": "settings", "name": "Filmabend", "slug": "kino",
                                                                                       "language": "de", "role": Role.SCHULLEITUNG})
        self.assertFalse(default_storage.exists("published/filmabend/index.html"))  # moved: the old address is gone
        self.client.post(reverse("publication_delete", args=[self.publication.pk]))
        self.assertFalse(Publication.objects.exists())

    def test_password_is_kept_encrypted(self):
        self.login(self.it)
        self.client.post(reverse("publication_detail", args=[self.publication.pk]), {
            "section": "target", "target": "webdav", "url": "https://dav.example.org/site/", "user": "anna", "password": "Pferd-Batterie-9"})
        self.publication.refresh_from_db()
        self.assertNotIn("Pferd-Batterie-9", self.publication.target_secret)
        self.assertNotIn("Pferd-Batterie-9", json.dumps(self.publication.target_config))
        self.assertEqual(targets.password(self.publication), "Pferd-Batterie-9")
        page = self.client.get(reverse("publication_detail", args=[self.publication.pk])).content.decode()
        self.assertNotIn("Pferd-Batterie-9", page)

    def test_a_failure_is_shown_and_nothing_counts_as_published(self):
        self.publication.target = Publication.FOLDER
        self.publication.target_config = {"path": "/gibt/es/nicht"}
        self.publication.save()
        service.start(self.confirmed(), service.PUBLISH)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.FAILED)
        self.assertIn("/gibt/es/nicht", self.publication.error)
        self.assertIsNone(self.publication.published_at)
        self.assertIsNone(self.publication.busy_until)

    def test_one_run_at_a_time(self):
        from django.utils import timezone

        publication = self.confirmed()
        Publication.objects.filter(pk=self.publication.pk).update(busy_until=timezone.now() + service.LOCK)
        with self.assertRaisesMessage(ValidationError, "läuft gerade"):
            service.start(publication, service.PUBLISH)


class FolderTests(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.publication.target = Publication.FOLDER
        self.publication.target_config = {"path": str(self.folder)}
        self.publication.save()

    def publish(self):
        publication = Publication.objects.get(pk=self.publication.pk)
        site = render.build(publication)
        result = targets.send(publication, site, targets.target_for(publication))
        publication.manifest = {"target": targets.fingerprint(publication), "files": site.files}
        publication.save()
        return result

    def test_only_changes_are_sent_and_leftovers_removed(self):
        (self.folder / "eigene-datei.txt").write_text("bleibt")
        sent, gone = self.publish()
        self.assertGreater(sent, 5)
        self.assertEqual(gone, 0)
        self.assertTrue((self.folder / "index.html").is_file())
        self.assertTrue((self.folder / "datafluss-manifest.json").is_file())
        self.assertEqual(self.publish(), (0, 0))  # nothing changed, nothing sent

        record = Record.objects.get(pk=self.story.pk)
        services.set_value(record, self.year, "1984")
        set_current_user(None)
        sent, gone = self.publish()
        # Its page, the pages that show its year (the list, the linked film) and the search index.
        self.assertLessEqual(sent, 5)
        self.assertIn("1984", (self.folder / f"filme/{self.story.pk}.html").read_text())

        Record.objects.get(pk=self.story.pk).recycle()
        sent, gone = self.publish()
        self.assertEqual(gone, 1)
        self.assertFalse((self.folder / f"filme/{self.story.pk}.html").exists())
        self.assertEqual((self.folder / "eigene-datei.txt").read_text(), "bleibt")  # never anything it did not make

    def test_a_field_hidden_later_takes_its_files_away(self):
        self.publish()
        pictures = list((self.folder / "_media").iterdir())
        self.assertTrue(pictures)
        row = UserRole.objects.get(key=Role.SCHULLEITUNG)
        row.fields = {"table:filme": {"f_intern": "hidden", "f_poster": "hidden"}}
        row.save()
        set_current_user(None)
        self.publish()
        self.assertFalse((self.folder / "_media").exists())

    def test_another_target_gets_everything(self):
        self.publish()
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        Publication.objects.filter(pk=self.publication.pk).update(target_config={"path": str(other)})
        sent, _gone = self.publish()
        self.assertTrue((other / "index.html").is_file())
        self.assertGreater(sent, 5)

    def test_paths_cannot_leave_the_folder(self):
        target = targets.FolderTarget(str(self.folder))
        for path in ("../x", "/etc/passwd", "a/../../x", "a//b", ""):
            with self.subTest(path), self.assertRaises(ValidationError):
                target.put(path, b"x")


class FakeDav(BaseHTTPRequestHandler):
    files = {}
    folders = set()
    auth = None

    def log_message(self, *args):
        pass

    def _ok(self):
        if self.headers.get("Authorization") != FakeDav.auth:
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def _path(self):
        return self.path.split("/site/", 1)[-1]

    def do_PROPFIND(self):
        if self._ok():
            self.send_response(207)
            self.end_headers()

    def do_MKCOL(self):
        if self._ok():
            path = self._path().rstrip("/")
            self.send_response(405 if path in FakeDav.folders else 201)
            FakeDav.folders.add(path)
            self.end_headers()

    def do_PUT(self):
        if self._ok():
            path = self._path()
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent and parent not in FakeDav.folders:
                self.send_response(409)
            else:
                FakeDav.files[path] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(201)
            self.end_headers()

    def do_GET(self):
        if self._ok():
            path = self._path()
            if path in FakeDav.files:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(FakeDav.files[path])
            else:
                self.send_response(404)
                self.end_headers()

    def do_DELETE(self):
        if self._ok():
            FakeDav.deleted.append(self._path())
            FakeDav.files.pop(self._path(), None)
            self.send_response(204)
            self.end_headers()


class WebDavTests(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        from base64 import b64encode

        FakeDav.files, FakeDav.folders, FakeDav.deleted = {}, set(), []
        FakeDav.auth = "Basic " + b64encode(b"anna:geheim").decode()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.publication.target = Publication.WEBDAV
        self.publication.target_config = {"url": f"http://127.0.0.1:{self.server.server_port}/site/", "user": "anna"}
        targets.set_password(self.publication, "geheim")
        self.publication.save()

    def test_publish_and_republish(self):
        service.start(self.confirmed(), service.PUBLISH)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.DONE, self.publication.error)
        self.assertIn("index.html", FakeDav.files)
        self.assertIn("datafluss-manifest.json", FakeDav.files)
        self.assertIn(f"filme/{self.momo.pk}.html", FakeDav.files)
        Record.objects.get(pk=self.story.pk).recycle()
        service.start(Publication.objects.get(pk=self.publication.pk), service.PUBLISH)
        self.assertNotIn(f"filme/{self.story.pk}.html", FakeDav.files)

    def test_wrong_password_and_plain_http_elsewhere(self):
        targets.set_password(self.publication, "falsch")
        self.publication.save()
        with self.assertRaisesMessage(ValidationError, "Anmeldung abgelehnt"):
            targets.target_for(self.publication).test()
        with self.assertRaisesMessage(ValidationError, "https://"):
            targets.WebDavTarget("http://dav.example.org/", "anna", "x")

    def test_the_connection_test(self):
        self.login(self.it)
        page = self.client.post(reverse("publication_test", args=[self.publication.pk])).content.decode()
        self.assertIn("badge-ok", page)
        self.assertIn("datafluss-test.txt", [c for c in FakeDav.deleted])  # written and removed again
        self.assertNotIn("datafluss-test.txt", FakeDav.files)


class FakeFtp:
    instances = []

    def __init__(self, *args, **kwargs):
        self.files, self.dirs, self.calls = {}, set(), []
        FakeFtp.instances.append(self)

    def connect(self, host, port):
        self.calls.append(("connect", host, port))

    def login(self, user, secret):
        if secret != "geheim":
            import ftplib

            raise ftplib.error_perm("530 Login incorrect.")

    def prot_p(self):
        self.calls.append(("prot_p",))

    def cwd(self, folder):
        self.folder = folder

    def mkd(self, path):
        self.dirs.add(path)

    def storbinary(self, command, handle):
        self.files[command.removeprefix("STOR ")] = handle.read()

    def retrbinary(self, command, write):
        import ftplib

        path = command.removeprefix("RETR ")
        if path not in self.files:
            raise ftplib.error_perm("550 No such file")
        write(self.files[path])

    def delete(self, path):
        self.files.pop(path, None)

    def quit(self):
        pass

    def close(self):
        pass


class FtpTests(Synchronous, PublishCase):
    def test_ftps_by_default(self):
        FakeFtp.instances = []
        self.publication.target = Publication.FTP
        self.publication.target_config = {"host": "ftp.example.org", "port": 21, "user": "anna", "path": "/htdocs/kino", "tls": True}
        targets.set_password(self.publication, "geheim")
        self.publication.save()
        with mock.patch("publish.targets.ftplib.FTP_TLS", FakeFtp), mock.patch("publish.targets.ftplib.FTP", side_effect=AssertionError("plain FTP")):
            service.start(self.confirmed(), service.PUBLISH)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.DONE, self.publication.error)
        ftp = FakeFtp.instances[-1]
        self.assertIn(("prot_p",), ftp.calls)
        self.assertIn("/htdocs/kino/index.html", ftp.files)
        self.assertIn("/htdocs/kino/filme", ftp.dirs)

    def test_wrong_password(self):
        self.publication.target = Publication.FTP
        self.publication.target_config = {"host": "ftp.example.org", "user": "anna", "path": "/"}
        targets.set_password(self.publication, "falsch")
        self.publication.save()
        with mock.patch("publish.targets.ftplib.FTP_TLS", FakeFtp), self.assertRaisesMessage(ValidationError, "530"):
            targets.target_for(self.publication).test()


class ImageTests(PublishCase):
    def test_gallery_and_record_head(self):
        self.films.look = {"cover": "f_poster", "layout": "gallery", "subtitle": ["f_jahr"]}
        self.films.save()
        site = render.build(Publication.objects.get(pk=self.publication.pk))
        listing = site.read("filme/index.html").decode()
        self.assertIn('class="gallery shape-poster site-gallery"', listing)
        self.assertIn("../_media/", listing)
        page = site.read(f"filme/{self.momo.pk}.html").decode()
        self.assertIn("record-hero", page)
        self.assertIn("1986", page)
        self.assertIn('href="#i-chevron"', page)
        self.assertIn('<symbol id="i-chevron"', page)

    def test_unchanged_pictures_are_not_made_again(self):
        from publish import snapshot

        render.build(self.publication)
        with mock.patch("publish.snapshot._make", wraps=snapshot._make) as make:
            render.build(self.publication)
        self.assertEqual(make.call_count, 0)
        links.add_file(Record.objects.get(pk=self.story.pk), self.poster, picture_with_exif("neu.jpg"), self.it)
        set_current_user(None)
        with mock.patch("publish.snapshot._make", wraps=snapshot._make) as make:
            render.build(self.publication)
        self.assertEqual(make.call_count, 2)  # the new poster, large and small

    def test_the_same_data_gives_the_same_files(self):
        first = render.build(self.publication).files
        second = render.build(Publication.objects.get(pk=self.publication.pk)).files
        self.assertEqual(first, second)
