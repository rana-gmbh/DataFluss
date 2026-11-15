import base64
import gzip
import io
import json
import os
import shutil
import tarfile
import tempfile
from datetime import date, datetime, timezone as utc
from pathlib import Path
from unittest import mock

import pyotp
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from archive import jobs, snapshot
from archive import container
from archive.container import BLOCK, ContainerError, Reader, Writer
from core import sealed
from core.context_user import set_current_user
from core.models import AuditEvent, ChangeLog, Role, SchoolSettings, UpdateState, User
from devices.models import Asset
from masks.defaults import setup
from masks.models import FieldDefinition
from masks.services import reveal_custom, set_value
from people.models import ClassGroup, Person, SchoolYear
from vault import crypto
from vault.models import Credential, Platform


PASSWORD = "richtig-pferd-batterie"
OTHER_KEY = base64.b64encode(b"k" * 32).decode()


class FastPassword:
    """The real password hardening takes a second, which adds up over many tests."""

    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(container, "OPS", container.KDF.OPSLIMIT_MIN), mock.patch.object(container, "MEM", container.KDF.MEMLIMIT_MIN)):
            patcher.start()
            self.addCleanup(patcher.stop)


class ContainerTests(FastPassword, TransactionTestCase):
    def sealed(self, data):
        raw = io.BytesIO()
        writer = Writer(raw, PASSWORD)
        writer.write(data)
        writer.close()
        return raw.getvalue()

    def opened(self, raw, password=PASSWORD):
        reader = Reader(io.BytesIO(raw), password)
        data = reader.read()
        reader.verify_end()
        return data

    def test_roundtrip_across_block_borders(self):
        for size in (0, 10, BLOCK, BLOCK * 2 + 17):
            data = os.urandom(size)
            self.assertEqual(self.opened(self.sealed(data)), data)

    def test_content_is_not_readable(self):
        self.assertNotIn(b"Lena Muster", self.sealed(b"Lena Muster" * 100))

    def test_wrong_password_is_refused(self):
        with self.assertRaises(ContainerError):
            self.opened(self.sealed(b"daten"), "falsches-passwort")

    def test_other_file_is_refused(self):
        with self.assertRaisesMessage(ContainerError, "keine Sicherungsdatei"):
            self.opened(b"PK\x03\x04" + b"0" * 200)

    def test_changed_byte_is_noticed(self):
        raw = bytearray(self.sealed(b"daten" * 1000))
        raw[-5] ^= 1
        with self.assertRaises(ContainerError):
            self.opened(bytes(raw))

    def test_cut_off_file_is_noticed(self):
        raw = self.sealed(os.urandom(BLOCK * 2))
        with self.assertRaises(ContainerError):
            self.opened(raw[:BLOCK + 100])

    def test_missing_last_block_is_noticed(self):
        raw = self.sealed(os.urandom(BLOCK + 5))
        with self.assertRaises(ContainerError):
            self.opened(raw[:-30])


class ArchiveCase(FastPassword, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.media = self.folder / "media"
        self.backups = self.folder / "backups"
        self.media.mkdir()
        overrides = override_settings(MEDIA_ROOT=self.media, BACKUP_ROOT=self.backups)
        overrides.enable()
        self.addCleanup(overrides.disable)
        spawn = mock.patch("archive.jobs._spawn", lambda target, *args: target(*args))
        spawn.start()
        self.addCleanup(spawn.stop)
        self.addCleanup(crypto.reset_key_cache)
        crypto.ensure_key_file()
        crypto.reset_key_cache()
        setup()
        self.it = User.objects.create_user("it", password="x" * 14, role=Role.IT)
        self.sek = User.objects.create_user("sek", password="x" * 14, role=Role.SEKRETARIAT)
        self.year = SchoolYear.objects.create(name="2026/27", start=date(2026, 8, 1), end=date(2027, 7, 31), is_current=True)
        self.group = ClassGroup.objects.create(name="5a", level=5, school_year=self.year)
        self.pupil = Person.objects.create(kind="pupil", first_name="Lena", last_name="Muster", school_type="GY", grade_level=5, current_class=self.group)

    def tearDown(self):
        set_current_user(None)

    def login(self, user):
        client = Client()
        client.force_login(user)
        session = client.session
        session["second_factor_ok"] = True
        session.save()
        return client

    def write_media(self, name, content):
        path = self.media / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sealed.seal(content))

    def read_media(self, name):
        return sealed.unseal((self.media / name).read_bytes())

    def fill_secrets(self):
        self.totp = pyotp.random_base32()
        self.it.totp_secret = crypto.encrypt(self.totp, f"totp:{self.it.pk}")
        self.it.totp_confirmed = True
        self.it.save()
        self.platform = Platform.objects.get(key="m365")
        self.credential = Credential.get_for(self.platform, person=self.pupil)
        secret_field = self.platform.secret_keys[0]
        self.credential.set_secret(secret_field, "Geheim-Passwort-1")
        self.credential.save()
        self.asset = Asset.objects.create(number="B-1")
        self.asset.product_key_enc = crypto.encrypt("ABCD-1234", f"asset:{self.asset.pk}:key")
        self.asset.save()
        self.field = FieldDefinition.objects.create(key="safe_code", label="Tresorcode", entity="pupil", type="secret")
        set_value(self.pupil, self.field, "4711")
        self.pupil.save()

    def container(self, name="test.svbackup"):
        target = jobs.root() / name
        snapshot.create(target, PASSWORD)
        return target

    def restore(self, path, password=PASSWORD):
        manifest = snapshot.read_manifest(path, password)
        snapshot.restore(path, password, manifest)


class RoundTripTests(ArchiveCase):
    def test_everything_comes_back(self):
        self.fill_secrets()
        self.write_media("photos/lena.jpg", b"foto")
        self.write_media("attachments/2026/zeugnis.pdf", b"pdf-inhalt")
        self.pupil.photo = "photos/lena.jpg"
        self.pupil.save()
        Person.objects.filter(pk=self.pupil.pk).update(created_at=datetime(2020, 3, 4, 5, 6, 7, 891234, tzinfo=utc.utc))
        created_at = Person.objects.get(pk=self.pupil.pk).created_at
        updated_at = Person.objects.get(pk=self.pupil.pk).updated_at
        changes = ChangeLog.objects.count()
        school = SchoolSettings.load()
        school.name = "Schule Musterstadt"
        school.save()
        path = self.container()

        Person.objects.create(kind="pupil", first_name="Später", last_name="Dazu")
        self.pupil.first_name = "Geändert"
        self.pupil.save()
        self.it.delete()
        SchoolSettings.objects.update(name="Anders")
        self.write_media("photos/spaeter.jpg", b"neu")
        self.write_media("photos/lena.jpg", b"verdorben")

        self.restore(path)

        pupil = Person.objects.get(pk=self.pupil.pk)
        self.assertEqual(pupil.first_name, "Lena")
        self.assertEqual(pupil.created_at, created_at)
        self.assertEqual(pupil.updated_at, updated_at)
        self.assertEqual(pupil.current_class, self.group)
        self.assertEqual(Person.objects.count(), 1)
        self.assertGreaterEqual(ChangeLog.objects.count(), changes)
        self.assertEqual(SchoolSettings.load().name, "Schule Musterstadt")
        self.assertTrue(User.objects.get(username="it").check_password("x" * 14))
        self.assertEqual(self.read_media("photos/lena.jpg"), b"foto")
        self.assertEqual(self.read_media("attachments/2026/zeugnis.pdf"), b"pdf-inhalt")
        self.assertFalse((self.media / "photos/spaeter.jpg").exists())
        self.assertEqual([p.name for p in self.media.iterdir() if p.name.startswith(".")], [])

    def test_secrets_move_to_an_instance_with_another_key(self):
        self.fill_secrets()
        secret_field = self.platform.secret_keys[0]
        path = self.container()
        with override_settings(VAULT_KEY=OTHER_KEY):
            crypto.reset_key_cache()
            self.restore(path)
            user = User.objects.get(username="it")
            self.assertEqual(crypto.decrypt(user.totp_secret, f"totp:{user.pk}"), self.totp)
            self.assertEqual(Credential.objects.get(pk=self.credential.pk).get_secret(secret_field), "Geheim-Passwort-1")
            asset = Asset.objects.get(pk=self.asset.pk)
            self.assertEqual(crypto.decrypt(asset.product_key_enc, f"asset:{asset.pk}:key"), "ABCD-1234")
            pupil = Person.objects.get(pk=self.pupil.pk)
            self.assertEqual(reveal_custom(pupil, FieldDefinition.objects.get(key="safe_code")), "4711")
        crypto.reset_key_cache()

    def test_secrets_are_not_kept_in_plain_text_or_with_the_server_key(self):
        self.fill_secrets()
        raw = self.container().read_bytes()
        self.assertNotIn(b"Geheim-Passwort", raw)
        self.assertNotIn(crypto._key(), raw)
        reader = Reader(io.BytesIO(raw), PASSWORD)
        self.assertNotIn(base64.b64encode(crypto._key()), reader.read())

    def test_new_records_get_free_numbers_after_an_import(self):
        SchoolSettings.load()
        self.restore(self.container())
        self.assertEqual(SchoolSettings.objects.create(name="Zweite").pk, 2)

    def test_sessions_and_update_state_stay_out(self):
        self.login(self.it)
        UpdateState.objects.update_or_create(pk=1, defaults={"deployment_uuid": "alt"})
        path = self.container()
        manifest = snapshot.read_manifest(path, PASSWORD)
        self.assertNotIn("sessions.session", manifest["rows"])
        self.assertNotIn("core.updatestate", manifest["rows"])
        self.restore(path)
        self.assertEqual(Session.objects.count(), 0)
        self.assertEqual(UpdateState.load().deployment_uuid, "")

    def test_old_files_of_a_failed_import_do_not_stay(self):
        self.write_media("photos/lena.jpg", b"foto")
        (self.media / ".restore-alt").mkdir()
        self.restore(self.container())
        self.assertFalse((self.media / ".restore-alt").exists())

    def test_changing_a_file_during_the_backup_cannot_break_it(self):
        self.write_media("photos/lena.jpg", b"foto")
        self.write_media("photos/weg.jpg", b"weg")
        original = snapshot._manifest

        def vanish(*args):
            manifest = original(*args)
            (self.media / "photos/weg.jpg").unlink()  # Listed, but gone before it is packed.
            return manifest

        with mock.patch("archive.snapshot._manifest", vanish):
            path = self.container()
        self.restore(path)
        self.assertTrue((self.media / "photos/lena.jpg").exists())
        self.assertFalse((self.media / "photos/weg.jpg").exists())


class CompatibilityTests(ArchiveCase):
    def rewrite(self, path, change):
        """Stands in for a container that another version wrote."""
        members = {}
        with path.open("rb") as raw:
            _, tar = snapshot._open(raw, PASSWORD)
            for member in tar:
                members[member.name] = tar.extractfile(member).read()
        change(members)
        with path.open("wb") as raw:
            sink = Writer(raw, PASSWORD)
            with tarfile.open(fileobj=sink, mode="w|") as tar:
                for name, data in members.items():
                    snapshot._add_bytes(tar, name, data)
            sink.close()

    def test_older_backup_runs_through_the_newer_migrations(self):
        SchoolSettings.objects.update(name="Alte Schule", require_2fa=False)

        def to_older_version(members):
            manifest = json.loads(members["manifest.json"])
            manifest["migrations"] = [m for m in manifest["migrations"] if m != ["core", "0003_two_factor_switch"]]
            members["manifest.json"] = json.dumps(manifest).encode()
            name = "data/core.schoolsettings.0000.jsonl.gz"
            row = json.loads(gzip.decompress(members[name]))
            del row["require_2fa"]
            members[name] = gzip.compress(json.dumps(row).encode())

        path = self.container()
        self.rewrite(path, to_older_version)
        with override_settings(REQUIRE_2FA=True):
            self.restore(path)
            self.assertEqual(SchoolSettings.load().name, "Alte Schule")
            self.assertTrue(SchoolSettings.load().require_2fa)
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from django_migrations where name = '0003_two_factor_switch'")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_backup_of_a_newer_version_is_refused(self):
        def to_newer_version(members):
            manifest = json.loads(members["manifest.json"])
            manifest["migrations"].append(["people", "9999_from_the_future"])
            members["manifest.json"] = json.dumps(manifest).encode()

        path = self.container()
        self.rewrite(path, to_newer_version)
        self.pupil.first_name = "Unverändert"
        self.pupil.save()
        manifest = snapshot.read_manifest(path, PASSWORD)
        self.assertIn("neueren Version", snapshot.incompatible(manifest))
        with self.assertRaisesMessage(ContainerError, "neueren Version"):
            snapshot.restore(path, PASSWORD, manifest)
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Unverändert")

    def test_escape_from_the_media_folder_is_refused(self):
        def escape(members):
            members["media/../../boese.txt"] = b"x"

        path = self.container()
        self.rewrite(path, escape)
        with self.assertRaisesMessage(ContainerError, "ungültige Einträge"):
            self.restore(path)
        self.assertFalse((self.folder / "boese.txt").exists())
        self.assertTrue(Person.objects.filter(pk=self.pupil.pk).exists())


class SafetyTests(ArchiveCase):
    def assert_untouched(self):
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Heute")
        self.assertEqual(self.read_media("photos/lena.jpg"), b"heute")
        self.assertEqual([p.name for p in self.media.iterdir() if p.name.startswith(".")], [])

    def prepare(self):
        self.write_media("photos/lena.jpg", b"damals")
        path = self.container()
        self.pupil.first_name = "Heute"
        self.pupil.save()
        self.write_media("photos/lena.jpg", b"heute")
        return path

    def test_failure_late_in_the_import_changes_nothing(self):
        path = self.prepare()
        with mock.patch("archive.snapshot._seal_secrets", side_effect=RuntimeError("kaputt")):
            with self.assertRaises(RuntimeError):
                self.restore(path)
        self.assert_untouched()

    def test_damaged_file_changes_nothing(self):
        path = self.prepare()
        raw = bytearray(path.read_bytes())
        raw[-20] ^= 1
        path.write_bytes(raw)
        with self.assertRaises(ContainerError):
            self.restore(path)
        self.assert_untouched()

    def test_cut_off_file_changes_nothing(self):
        path = self.prepare()
        path.write_bytes(path.read_bytes()[:-40])
        with self.assertRaises(ContainerError):
            self.restore(path)
        self.assert_untouched()

    def test_unfinished_backup_never_appears(self):
        with mock.patch("archive.snapshot._add_rows", side_effect=RuntimeError("kaputt")):
            with self.assertRaises(RuntimeError):
                self.container()
        self.assertEqual(list(jobs.root().iterdir()), [])


class PageTests(ArchiveCase):
    def setUp(self):
        super().setUp()
        self.client = self.login(self.it)

    def post_backup(self, password=PASSWORD, repeat=PASSWORD):
        return self.client.post(reverse("backup_create"), {"password": password, "repeat": repeat})

    def only_container(self):
        return jobs.containers()[0]["name"]

    def test_only_it_may_enter(self):
        secretary = self.login(self.sek)
        self.assertEqual(secretary.get(reverse("backups")).status_code, 403)
        self.assertEqual(secretary.post(reverse("backup_create"), {"password": PASSWORD, "repeat": PASSWORD}).status_code, 403)
        self.assertEqual(secretary.get(reverse("backup_download", args=["x.svbackup"])).status_code, 403)
        self.assertEqual(secretary.post(reverse("backup_restore"), {"name": "x.svbackup"}).status_code, 403)
        self.assertNotContains(secretary.get(reverse("dashboard")), reverse("backups"))
        self.assertContains(self.client.get(reverse("dashboard")), reverse("backups"))

    def test_backup_needs_a_long_repeated_password(self):
        self.assertEqual(self.post_backup("kurz", "kurz").status_code, 400)
        self.assertEqual(self.post_backup(PASSWORD, "etwas-anderes-hier").status_code, 400)
        self.assertEqual(jobs.containers(), [])

    def test_backup_appears_with_its_contents_and_can_be_downloaded(self):
        response = self.post_backup()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("backup_status")).json()["state"], "done")
        name = self.only_container()
        page = self.client.get(reverse("backups"))
        self.assertContains(page, name)
        self.assertContains(page, "1 Personen")
        download = self.client.get(reverse("backup_download", args=[name]))
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(b"".join(download.streaming_content), (jobs.root() / name).read_bytes())
        self.assertTrue(AuditEvent.objects.filter(action="backup", summary__contains="heruntergeladen").exists())

    def test_password_is_not_stored(self):
        self.post_backup()
        for path in jobs.root().iterdir():
            if path.is_file():
                self.assertNotIn(PASSWORD.encode(), path.read_bytes())

    def test_only_container_names_can_be_reached(self):
        (jobs.root() / "pre-update-1.dump").write_text("dump")
        for name in ("pre-update-1.dump", "job.json", "..svbackup"):
            self.assertEqual(self.client.get(reverse("backup_download", args=[name])).status_code, 404)
            self.assertEqual(self.client.post(reverse("backup_delete"), {"name": name}).status_code, 404)
        self.assertTrue((jobs.root() / "pre-update-1.dump").exists())

    def test_check_shows_the_contents_and_refuses_a_wrong_password(self):
        before = timezone.localtime().strftime("%d.%m.%Y %H:%M")
        self.post_backup()
        name = self.only_container()
        wrong = self.client.post(reverse("backup_check"), {"name": name, "password": "falsches-passwort"})
        self.assertEqual(wrong.status_code, 400)
        body = self.client.post(reverse("backup_check"), {"name": name, "password": PASSWORD}).json()
        self.assertEqual(body["people"], 1)
        self.assertEqual(body["problem"], "")
        self.assertIn(body["created_at"], {before, timezone.localtime().strftime("%d.%m.%Y %H:%M")})  # the minute may change meanwhile

    def test_restore_needs_the_confirmation_word(self):
        self.post_backup()
        name = self.only_container()
        response = self.client.post(reverse("backup_restore"), {"name": name, "password": PASSWORD, "confirm": "ja"})
        self.assertEqual(response.status_code, 400)

    def test_restore_keeps_the_current_state_and_signs_everyone_out(self):
        self.post_backup()
        name = self.only_container()
        self.pupil.first_name = "Vorher"
        self.pupil.save()
        response = self.client.post(reverse("backup_restore"), {"name": name, "password": PASSWORD, "confirm": "EINSPIELEN"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(jobs.current()["state"], "done", jobs.current()["message"])
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Lena")
        status = self.client.get(reverse("backup_status"))
        self.assertEqual(status.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(summary__contains="eingespielt von it").exists())

        safety = next(c for c in jobs.containers() if c["details"]["origin"] == "automatisch")
        self.restore(jobs.root() / safety["name"])
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Vorher")

    def test_wrong_password_at_restore_changes_nothing(self):
        self.post_backup()
        name = self.only_container()
        self.client.post(reverse("backup_restore"), {"name": name, "password": "falsches-passwort", "confirm": "EINSPIELEN"})
        job = jobs.current()
        self.assertEqual(job["state"], "failed")
        self.assertIn("Passwort", job["message"])
        self.assertEqual(len(jobs.containers()), 1)

    def test_no_second_job_while_one_runs(self):
        with mock.patch("archive.jobs._spawn"):
            self.assertEqual(self.post_backup().status_code, 200)
            self.assertEqual(self.post_backup().status_code, 409)

    def test_interrupted_job_does_not_block_forever(self):
        with mock.patch("archive.jobs._spawn"):
            self.post_backup()
        job = json.loads(jobs._state_file().read_text())
        job["beat"] -= jobs.STALE_AFTER + 1
        jobs._save(job)
        self.assertEqual(jobs.current()["state"], "failed")
        self.assertEqual(self.post_backup().status_code, 200)

    def test_old_leftovers_are_cleared_and_recent_ones_stay(self):
        old, recent = jobs.root() / "alt.svbackup.part", jobs._uploads() / "neu.part"
        old.write_text("x")
        recent.write_text("x")
        os.utime(old, (0, 0))
        self.client.get(reverse("backups"))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_delete_removes_the_file(self):
        self.post_backup()
        name = self.only_container()
        self.client.post(reverse("backup_delete"), {"name": name})
        self.assertEqual(jobs.containers(), [])


class UploadTests(ArchiveCase):
    ID = "a" * 32

    def setUp(self):
        super().setUp()
        self.client = self.login(self.it)
        self.raw = self.container("quelle.svbackup").read_bytes()
        (jobs.root() / "quelle.svbackup").unlink()

    def send(self, offset, piece, total=None, upload_id=None):
        return self.client.post(reverse("backup_upload"), {
            "id": upload_id or self.ID, "offset": offset, "total": total or len(self.raw),
            "chunk": SimpleUploadedFile("chunk", piece),
        })

    def finish(self, name="Meine Sicherung.svbackup", total=None):
        return self.client.post(reverse("backup_upload"), {"id": self.ID, "offset": 0, "total": total or len(self.raw), "name": name})

    def test_pieces_become_a_container_that_can_be_restored(self):
        middle = len(self.raw) // 2
        self.assertEqual(self.send(0, self.raw[:middle]).json()["offset"], middle)
        self.assertEqual(self.send(middle, self.raw[middle:]).json()["offset"], len(self.raw))
        name = self.finish().json()["name"]
        self.assertEqual(name, "Meine_Sicherung.svbackup")
        self.assertEqual((jobs.root() / name).read_bytes(), self.raw)
        self.assertContains(self.client.get(reverse("backups")), "Noch nicht geprüft")
        self.assertEqual(self.client.post(reverse("backup_check"), {"name": name, "password": PASSWORD}).json()["people"], 1)
        self.assertContains(self.client.get(reverse("backups")), "1 Personen")

    def test_repeated_piece_does_not_count_twice(self):
        middle = len(self.raw) // 2
        self.send(0, self.raw[:middle])
        self.assertEqual(self.send(0, self.raw[:middle]).json()["offset"], middle)
        self.send(middle, self.raw[middle:])
        self.assertEqual((jobs._uploads() / f"{self.ID}.part").read_bytes(), self.raw)

    def test_other_files_are_refused_at_once(self):
        self.assertEqual(self.send(0, b"Das ist ein Foto").status_code, 400)
        self.assertFalse((jobs._uploads() / f"{self.ID}.part").exists())

    def test_incomplete_upload_does_not_become_a_container(self):
        self.send(0, self.raw[:100])
        self.assertEqual(self.finish().status_code, 400)
        self.assertEqual(jobs.containers(), [])

    def test_bad_ids_and_names_stay_out_of_the_folder(self):
        self.assertEqual(self.send(0, self.raw, upload_id="../../boese").status_code, 400)
        self.send(0, self.raw)
        name = self.finish(name="../../boese.svbackup").json()["name"]
        self.assertEqual(name, "boese.svbackup")
        self.assertTrue((jobs.root() / name).exists())

    def test_name_of_an_existing_container_is_not_overwritten(self):
        (jobs.root() / "boese.svbackup").write_bytes(b"alt")
        self.send(0, self.raw)
        self.assertEqual(self.finish(name="boese.svbackup").json()["name"], "boese-2.svbackup")
        self.assertEqual((jobs.root() / "boese.svbackup").read_bytes(), b"alt")

    def test_only_it_may_upload(self):
        self.client = self.login(self.sek)
        self.assertEqual(self.send(0, self.raw).status_code, 403)
