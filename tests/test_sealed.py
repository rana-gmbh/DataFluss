"""Uploads encrypted on the disk when MEDIA_KEY is set, as on the desktop client."""
import base64
import io
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from archive import snapshot
from archive.container import ContainerError, Reader
from core import sealed
from core.models import SchoolSettings
from people.models import Person
from prints.engine import data_uri

from .base import AppTestCase
from .test_archive import PASSWORD, ArchiveCase


KEY = base64.b64encode(b"m" * 32).decode()
OTHER = base64.b64encode(b"n" * 32).decode()


def storage(backend):
    return {**settings.STORAGES, "default": {"BACKEND": backend}}


KEYED = {"MEDIA_KEY": KEY, "STORAGES": storage("core.sealed.SealedStorage")}
PLAIN = {"MEDIA_KEY": "", "STORAGES": storage("django.core.files.storage.FileSystemStorage")}


def picture(size=(400, 300)):
    buffer = BytesIO()
    Image.new("RGB", size, "blue").save(buffer, "PNG")
    return buffer.getvalue()


def body(response):
    return b"".join(response.streaming_content)


class SealTests(SimpleTestCase):
    def test_without_a_key_nothing_changes(self):
        with override_settings(MEDIA_KEY=""):
            self.assertEqual(sealed.seal(b"foto"), b"foto")
            self.assertEqual(sealed.unseal(b"foto"), b"foto")

    @override_settings(MEDIA_KEY=KEY)
    def test_roundtrip_hides_the_content(self):
        box = sealed.seal(b"Lena Muster" * 10)
        self.assertTrue(box.startswith(sealed.MAGIC))
        self.assertNotIn(b"Lena Muster", box)
        self.assertEqual(len(box), 110 + sealed.OVERHEAD)
        self.assertNotEqual(box, sealed.seal(b"Lena Muster" * 10))
        self.assertEqual(sealed.unseal(box), b"Lena Muster" * 10)
        self.assertEqual(sealed.unseal(sealed.seal(b"")), b"")

    @override_settings(MEDIA_KEY=KEY)
    def test_plain_damaged_or_foreign_files_are_refused(self):
        with self.assertRaises(sealed.Unreadable):
            sealed.unseal(b"\x89PNG plain picture")
        with self.assertRaises(sealed.Unreadable):
            sealed.unseal(sealed.MAGIC)
        box = bytearray(sealed.seal(b"foto"))
        box[-1] ^= 1
        with self.assertRaises(sealed.Unreadable):
            sealed.unseal(bytes(box))
        box = sealed.seal(b"foto")
        with override_settings(MEDIA_KEY=OTHER), self.assertRaises(sealed.Unreadable):
            sealed.unseal(box)

    def test_bad_key_is_a_configuration_error(self):
        for key in ("kurz", base64.b64encode(b"x" * 16).decode(), "nicht base64!"):
            with override_settings(MEDIA_KEY=key), self.assertRaises(ImproperlyConfigured):
                sealed.seal(b"foto")


class UploadTests(AppTestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media, **KEYED)
        override.enable()
        self.addCleanup(override.disable)
        self.login(self.it)

    def assert_sealed(self, name, plain):
        raw = (self.media / name).read_bytes()
        self.assertTrue(raw.startswith(sealed.MAGIC))
        self.assertNotIn(plain[:64], raw)
        self.assertEqual(sealed.unseal(raw), plain)

    def test_photo_is_sealed_and_comes_back_intact(self):
        image = picture()
        self.client.post(reverse("person_photo", args=[self.pupil.pk]), {"photo": SimpleUploadedFile("p.png", image)})
        photo = Person.objects.get(pk=self.pupil.pk).photo
        self.assertEqual(photo.name, f"photos/{self.pupil.pk}.png")
        self.assert_sealed(photo.name, image)
        self.assertEqual(photo.size, len(image))
        response = self.client.get(f"/media/{photo.name}")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Content-Length"], str(len(image)))
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))
        self.assertEqual(body(response), image)
        self.assertEqual(data_uri(photo), "data:image/png;base64," + base64.b64encode(image).decode())

    def test_something_else_than_a_picture_is_still_refused(self):
        self.client.post(reverse("person_photo", args=[self.pupil.pk]), {"photo": SimpleUploadedFile("p.png", b"<svg/>")})
        self.assertFalse(Person.objects.get(pk=self.pupil.pk).photo)

    def test_thumbnail_is_sealed_too(self):
        self.pupil.photo.save("x.png", ContentFile(picture((800, 1000))))
        response = self.client.get(self.pupil.thumb_url)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertLessEqual(max(Image.open(BytesIO(body(response))).size), 128)
        [small] = (self.media / ".thumbs").iterdir()
        raw = small.read_bytes()
        self.assertTrue(raw.startswith(sealed.MAGIC))
        self.assertNotIn(b"WEBP", raw)
        self.assertEqual(body(self.client.get(self.pupil.thumb_url)), sealed.unseal(raw))

    def test_attachment_is_sealed_and_downloads_intact(self):
        content = b"%PDF-1.7 Zeugnis von Lena Muster"
        self.client.post(reverse("person_attachment", args=[self.pupil.pk]), {"file": SimpleUploadedFile("zeugnis.pdf", content)})
        item = self.pupil.attachments.get()
        self.assert_sealed(item.file.name, content)
        response = self.client.get(reverse("attachment_download", args=[self.pupil.pk, item.pk]))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(body(response), content)
        response = self.client.get(f"/media/{item.file.name}")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["Content-Security-Policy"], "sandbox; default-src 'none'")
        self.assertEqual(body(response), content)

    def test_logo_is_sealed_and_public_intact(self):
        image = picture((4, 4))
        self.client.post(reverse("school_settings"), {"name": "Schule", "short_name": "S", "logo": SimpleUploadedFile("l.png", image)})
        logo = SchoolSettings.load().logo
        self.assert_sealed(logo.name, image)
        self.client.logout()
        response = self.client.get(reverse("school_logo"))
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")
        self.assertEqual(body(response), image)


class BackupTests(ArchiveCase):
    def test_backup_of_an_encrypted_client_restores_on_a_plain_server(self):
        with override_settings(**KEYED):
            self.write_media("photos/lena.jpg", b"foto-von-lena")
            self.assertNotIn(b"foto-von-lena", (self.media / "photos/lena.jpg").read_bytes())
            path = self.container()
        # The container holds plain text, protected by its own password.
        self.assertIn(b"foto-von-lena", Reader(io.BytesIO(path.read_bytes()), PASSWORD).read())
        with override_settings(**PLAIN):
            self.assertEqual(snapshot.read_manifest(path, PASSWORD)["bytes"], len(b"foto-von-lena"))
            self.restore(path)
        self.assertEqual((self.media / "photos/lena.jpg").read_bytes(), b"foto-von-lena")

    def test_backup_of_a_plain_server_restores_sealed_on_a_client(self):
        with override_settings(**PLAIN):
            self.write_media("photos/lena.jpg", b"foto-von-lena")
            path = self.container()
        with override_settings(**KEYED):
            self.restore(path)
            self.assertEqual(self.read_media("photos/lena.jpg"), b"foto-von-lena")
        raw = (self.media / "photos/lena.jpg").read_bytes()
        self.assertTrue(raw.startswith(sealed.MAGIC))
        self.assertNotIn(b"foto-von-lena", raw)

    def test_a_file_that_cannot_be_decrypted_stops_the_backup(self):
        (self.media / "photos").mkdir()
        (self.media / "photos/lena.jpg").write_bytes(b"plain")
        with override_settings(**KEYED), self.assertRaisesMessage(ContainerError, "photos/lena.jpg"):
            self.container()

