import importlib
import json
import shutil
import subprocess
import tempfile
import threading
from datetime import date, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import ProtectedError
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from archive import jobs, nightly, offsite, snapshot
from archive.container import ContainerError
from core import sealed, updates
from core.context_user import set_current_user
from core.models import AuditEvent, ChangeLog, Role, SchoolSettings, User
from devices.models import Asset
from masks.defaults import setup
from people.models import Attachment, ClassGroup, Person, SchoolYear
from people.services import RolloverRefused, rollover_commit
from transfer import importer
from transfer.models import ImportSession
from vault import crypto
from vault.models import Credential, Platform

from .base import AppTestCase
from .test_archive import FastPassword


COLUMNS = {"Vorname": "first_name", "Nachname": "last_name", "E-Mail": "email", "Passwort": "login:m365:password", "Id": "legacy_ninox_id"}
HEADERS = list(COLUMNS)
SECRET = "Geheim-Passwort-4711"


def import_session(rows, user=None):
    return ImportSession.objects.create(filename="x.csv", headers=HEADERS, rows=rows, mapping=COLUMNS, status=ImportSession.PREVIEWED, user=user)


class ImportFileTests(AppTestCase):
    def test_file_never_reaches_the_change_log(self):
        session = import_session([["Anna", "Neu", "", SECRET, "B1"]])
        importer.commit(session)
        logged = list(ChangeLog.objects.values_list("old_value", "new_value"))
        self.assertFalse([pair for pair in logged if SECRET in pair[0] or SECRET in pair[1]])
        self.assertFalse(ChangeLog.objects.filter(entity="transfer.importsession", field__in=["rows", "headers", "report", "undo"]).exists())

    def test_old_log_rows_are_removed(self):
        keep = ChangeLog.objects.create(entity="transfer.importsession", record_id=self.pupil.pk, field="status", action=ChangeLog.UPDATE, hlc="1", server_id="eu")
        ChangeLog.objects.create(entity="transfer.importsession", record_id=self.pupil.pk, field="rows", action=ChangeLog.CREATE, new_value=SECRET, hlc="2", server_id="eu")
        migration = importlib.import_module("transfer.migrations.0002_purge_import_files_from_changelog")
        migration.purge(apps, None)
        self.assertEqual(list(ChangeLog.objects.filter(entity="transfer.importsession")), [keep])

    def test_finished_import_keeps_no_names(self):
        session = import_session([["Anna", "Neu", "", "", "B1"]])
        session.report = importer.analyse(session)
        session.save()
        importer.commit(session)
        session.refresh_from_db()
        self.assertEqual(session.rows, [])
        self.assertNotIn("Anna", json.dumps(session.report))


class ImportUndoTests(AppTestCase):
    def setUp(self):
        self.login(self.sek)
        self.session = import_session([["Anna", "Neu", "", "", "B1"]], self.sek)
        importer.commit(self.session)
        self.session.refresh_from_db()
        self.anna = Person.objects.get(legacy_ninox_id="B1")

    def undo(self):
        return self.client.post(reverse("transfer_undo", args=[self.session.pk]), follow=True)

    def test_fresh_import_can_be_taken_back(self):
        self.undo()
        self.assertFalse(Person.objects.filter(pk=self.anna.pk).exists())

    def test_undo_puts_class_custom_values_and_logins_back(self):
        platform = Platform.objects.get(key="m365")
        credential = Credential.get_for(platform, person=self.pupil)
        credential.values = {"username": "alt"}
        credential.save()
        self.pupil.legacy_ninox_id = "B7"
        self.pupil.custom = {"alt": "bleibt"}
        self.pupil.save()
        columns = {**COLUMNS, "Klasse": "klasse", "Login": "login:m365:username"}
        session = ImportSession.objects.create(
            filename="x.csv", headers=list(columns), mapping=columns, status=ImportSession.PREVIEWED,
            rows=[["Lena", "Muster", "", "", "B7", "7x", "neu"]],
        )
        importer.commit(session)
        session.refresh_from_db()
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.current_class.name, "7x")
        importer.rollback(session)
        self.pupil.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(self.pupil.current_class, self.group)
        self.assertEqual(self.pupil.custom, {"alt": "bleibt"})
        self.assertEqual(credential.values, {"username": "alt"})
        self.assertFalse(ClassGroup.objects.filter(name="7x").exists())

    def test_later_login_change_blocks_the_undo(self):
        platform = Platform.objects.get(key="m365")
        credential = Credential.get_for(platform, person=self.pupil)
        self.session.undo = {**self.session.undo, "logins": {str(credential.pk): {"values": {}, "secrets": {}}}}
        self.session.save()
        credential.values = {"username": "später"}
        credential.save()
        self.assertIn("Zugänge", importer.undo_blocker(self.session))

    def test_later_edits_block_the_undo(self):
        self.anna.first_name = "Annika"
        self.anna.save()
        self.assertIn("geändert", importer.undo_blocker(self.session))

    def test_old_import_stays(self):
        ImportSession.objects.filter(pk=self.session.pk).update(updated_at=timezone.now() - timedelta(hours=25))
        self.session.refresh_from_db()
        self.assertIn("24 Stunden", importer.undo_blocker(self.session))
        self.undo()
        self.assertTrue(Person.objects.filter(pk=self.anna.pk).exists())


class ImportValueTests(AppTestCase):
    def test_blank_cells_keep_what_is_stored(self):
        self.pupil.legacy_ninox_id = "B1"
        self.pupil.save()
        session = import_session([["Lena", "Muster", "", "", "B1"]])
        report = importer.analyse(session)
        self.assertEqual(report["same"], 1)
        importer.commit(session)
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.email, "lena@schule.example")

    def test_preview_shows_old_and_new_value(self):
        self.login(self.sek)
        self.pupil.legacy_ninox_id = "B1"
        self.pupil.save()
        session = import_session([["Lena", "Muster", "neu@schule.example", "", "B1"]])
        session.report = importer.analyse(session)
        session.save()
        response = self.client.get(reverse("transfer_preview", args=[session.pk]))
        self.assertContains(response, "lena@schule.example")
        self.assertContains(response, "neu@schule.example")

    def test_second_commit_finds_it_finished(self):
        self.login(self.sek)
        session = import_session([["Anna", "Neu", "", "", "B1"]])
        self.client.post(reverse("transfer_commit", args=[session.pk]))
        response = self.client.post(reverse("transfer_commit", args=[session.pk]), follow=True)
        self.assertContains(response, "bereits bearbeitet")
        self.assertEqual(Person.objects.filter(legacy_ninox_id="B1").count(), 1)


class ImportRaceTests(TransactionTestCase):
    def test_two_commits_at_once_import_once(self):
        crypto.ensure_key_file()
        setup()
        rows = [[f"Kind{i}", "Neu", "", "", f"B{i}"] for i in range(30)]
        session = import_session(rows)
        outcomes = []

        def run():
            try:
                outcomes.append(importer.commit(ImportSession.objects.get(pk=session.pk))["created"])
            except importer.ImportClosed:
                outcomes.append("refused")
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(map(str, outcomes)), ["30", "refused"])
        self.assertEqual(Person.objects.count(), 30)


class RolloverTests(AppTestCase):
    def setUp(self):
        self.login(self.it)
        patcher = mock.patch("core.updates.backup_database", return_value="dump")
        self.backup = patcher.start()
        self.addCleanup(patcher.stop)

    def run_it(self, name="2027/28"):
        return self.client.post(reverse("rollover"), {"name": name, "confirm": "1"}, follow=True)

    def test_backup_comes_first(self):
        self.run_it()
        self.backup.assert_called_once_with("vor-schuljahreswechsel")
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.current_class.name, "6a")

    def test_repeat_moves_nobody(self):
        self.run_it()
        response = self.run_it()
        self.assertContains(response, "nicht noch einmal")
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.current_class.name, "6a")

    def test_year_that_has_not_begun_blocks_the_next_one(self):
        self.run_it()
        self.assertContains(self.run_it("2028/29"), "noch nicht begonnen")
        self.assertFalse(SchoolYear.objects.filter(name="2028/29").exists())

    def test_failed_backup_stops_it(self):
        self.backup.side_effect = updates.UpdateError("Die Datenbank konnte nicht gesichert werden.")
        self.assertContains(self.run_it(), "nicht gesichert")
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.current_class.name, "5a")

    def test_prepared_empty_year_can_be_used(self):
        SchoolYear.objects.create(name="2027/28", start=date(2027, 8, 1), end=date(2028, 7, 31))
        moved, left = rollover_commit("2027/28", date(2027, 8, 1), date(2028, 7, 31))
        self.assertEqual(moved, 1)

    def test_service_refuses_too(self):
        rollover_commit("2027/28", date(2027, 8, 1), date(2028, 7, 31))
        with self.assertRaises(RolloverRefused):
            rollover_commit("2027/28", date(2027, 8, 1), date(2028, 7, 31))


class SeedGuardTests(AppTestCase):
    @override_settings(ALLOW_DEMO_DATA=False)
    def test_refuses_without_the_switch(self):
        with self.assertRaisesMessage(CommandError, "ALLOW_DEMO_DATA"):
            call_command("seed_demo", stdout=StringIO())
        self.assertFalse(Asset.objects.exists())

    def test_reset_needs_the_school_name(self):
        with self.assertRaisesMessage(CommandError, "--confirm-school"):
            call_command("seed_demo", "--reset", stdout=StringIO())
        self.assertTrue(Person.objects.filter(pk=self.pupil.pk).exists())


class DemoAccountTests(AppTestCase):
    def test_needs_a_real_it_account_first(self):
        User.objects.filter(role=Role.IT).update(username="it")
        with self.assertRaises(CommandError):
            call_command("remove_demo_accounts", stdout=StringIO())

    def test_locks_demo_accounts(self):
        admin = User.objects.create_user("verwaltung", password="x" * 14, role=Role.IT)
        call_command("remove_demo_accounts", stdout=StringIO())
        self.it.refresh_from_db()
        admin.refresh_from_db()
        self.assertFalse(self.it.is_active)
        self.assertFalse(self.it.has_usable_password())
        self.assertTrue(admin.is_active)


class SuggestTests(AppTestCase):
    def test_serial_cannot_inject_markup(self):
        self.login(self.it)
        Asset.objects.create(number="L1", serial='<i x-init="alert(1)">')
        content = self.client.get(reverse("asset_suggest")).content.decode()
        self.assertNotIn("<i ", content)
        self.assertIn("&lt;i", content)


class UploadTests(AppTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.login(self.it)

    def stored(self, name, content):
        path = Path(self.media) / "attachments" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(sealed.seal(content))
        return self.client.get(f"/media/attachments/{name}")

    def test_html_is_saved_and_not_opened(self):
        response = self.stored("x.html", b"<script>alert(1)</script>")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("sandbox", response["Content-Security-Policy"])

    def test_svg_is_saved_and_not_opened(self):
        self.assertIn("attachment", self.stored("x.svg", b"<svg/>")["Content-Disposition"])

    def test_picture_opens(self):
        response = self.stored("x.png", b"png")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(response["Content-Type"], "image/png")

    def picture(self):
        buffer = BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "PNG")
        return SimpleUploadedFile("anders.html", buffer.getvalue())

    def test_logo_is_named_after_its_content(self):
        self.client.post(reverse("school_settings"), {"name": "Schule", "short_name": "S", "logo": self.picture()})
        self.assertTrue(SchoolSettings.load().logo.name.endswith(".png"))

    def test_logo_must_be_a_picture(self):
        response = self.client.post(reverse("school_settings"), {"name": "Schule", "short_name": "S", "logo": SimpleUploadedFile("l.png", b"<svg/>")}, follow=True)
        self.assertContains(response, "muss ein Bild sein")
        self.assertFalse(SchoolSettings.load().logo)


class PasswordTests(AppTestCase):
    NEW = "ein-ganz-neues-passwort"

    def change(self, current="x" * 14, new=NEW, repeat=None):
        return self.client.post(reverse("password_change"), {"current": current, "new": new, "repeat": repeat or new})

    def test_change_keeps_the_session_and_replaces_the_password(self):
        self.login(self.sek)
        self.assertEqual(self.change().status_code, 302)
        self.sek.refresh_from_db()
        self.assertTrue(self.sek.check_password(self.NEW))
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)

    def test_needs_the_current_password(self):
        self.login(self.sek)
        self.assertContains(self.change(current="falsch"), "stimmt nicht")
        self.sek.refresh_from_db()
        self.assertFalse(self.sek.check_password(self.NEW))

    def test_new_password_has_to_be_strong_and_different(self):
        self.login(self.sek)
        self.assertEqual(self.change(new="kurz").status_code, 200)
        self.assertContains(self.change(new="x" * 14), "unterscheiden")

    def test_guessing_stops(self):
        self.login(self.sek)
        for _ in range(9):
            response = self.change(current="falsch")
        self.assertContains(response, "Zu viele")
        self.assertContains(self.change(), "Zu viele")

    def test_new_account_must_change_it(self):
        self.login(self.it)
        self.client.post(reverse("users"), {"username": "neu", "password": "ein-startpasswort-1", "role": Role.SEKRETARIAT})
        user = User.objects.get(username="neu")
        self.assertTrue(user.must_change_password)
        self.login(user)
        self.assertRedirects(self.client.get(reverse("pupils")), reverse("password_change"))
        self.client.post(reverse("password_change"), {"current": "ein-startpasswort-1", "new": self.NEW, "repeat": self.NEW})
        self.assertEqual(self.client.get(reverse("pupils")).status_code, 200)

    def test_it_can_issue_a_new_one(self):
        self.login(self.it)
        response = self.client.post(reverse("user_update", args=[self.sek.pk]), {"action": "password"})
        password = response.context["temporary"]["password"]
        self.sek.refresh_from_db()
        self.assertTrue(self.sek.check_password(password))
        self.assertTrue(self.sek.must_change_password)

    def test_it_ends_the_old_session(self):
        other = Client()
        other.force_login(self.sek)
        self.login(self.it)
        self.client.post(reverse("user_update", args=[self.sek.pk]), {"action": "password"})
        self.assertEqual(other.get(reverse("pupils")).status_code, 302)

    def test_only_it_can_issue_one(self):
        self.login(self.sek)
        self.assertEqual(self.client.post(reverse("user_update", args=[self.sl.pk]), {"action": "password"}).status_code, 403)

    def test_microsoft_accounts_stay_without_a_password(self):
        self.sl.set_unusable_password()
        self.sl.save()
        self.login(self.it)
        self.client.post(reverse("user_update", args=[self.sl.pk]), {"action": "password"})
        self.sl.refresh_from_db()
        self.assertFalse(self.sl.has_usable_password())


class SignedOutTests(AppTestCase):
    def test_script_requests_get_401_and_no_redirect(self):
        url = f"/api/personen/{self.pupil.pk}/feld/email/"
        self.assertEqual(self.client.post(url, headers={"X-Requested-With": "fetch"}).status_code, 401)
        self.assertEqual(self.client.post(url, headers={"HX-Request": "true"}).status_code, 401)

    def test_pages_still_lead_to_the_login(self):
        self.assertEqual(self.client.get(reverse("pupils")).status_code, 302)

    def test_missing_second_factor_counts_for_scripts_too(self):
        SchoolSettings.objects.update(require_2fa=True)
        self.client.force_login(self.sek)
        self.assertEqual(self.client.get(reverse("session_check"), headers={"X-Requested-With": "fetch"}).status_code, 401)

    def test_session_check(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("session_check")).status_code, 204)

    def test_refused_message_looks_like_an_error(self):
        self.login(self.sek)
        session = import_session([["Anna", "Neu", "", "", "B1"]])
        importer.commit(session)
        response = self.client.post(reverse("transfer_commit", args=[session.pk]), follow=True)
        self.assertContains(response, 'role="alert"')


class DumpFolder:
    def setUp(self):
        super().setUp()
        set_current_user(None)
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        override = override_settings(BACKUP_ROOT=self.folder, MEDIA_ROOT=tempfile.mkdtemp(dir=self.folder))
        override.enable()
        self.addCleanup(override.disable)
        crypto.ensure_key_file()


@skipUnless(connection.vendor == "postgresql", "pg_dump belongs to the server update, not the desktop client")
class RealDumpTests(FastPassword, DumpFolder, TransactionTestCase):
    """With the real pg_dump and psql, because mocks cannot show what they do to a real schema."""

    def setUp(self):
        super().setUp()
        setup()
        self.person = Person.objects.create(kind="pupil", first_name="Lena", last_name="Vorher", email="vorher@schule.example")

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS neu_mit_fk, neu_ohne_fk CASCADE")

    def test_dump_on_disk_is_encrypted(self):
        name = updates.backup_database()
        content = (Path(self.folder) / name).read_bytes()
        self.assertTrue(content.startswith(b"SVBACKUP"))
        self.assertNotIn(b"vorher@schule.example", content)
        self.assertFalse(list(Path(self.folder).glob("*.part")))

    def test_old_unencrypted_dumps_go_away(self):
        (Path(self.folder) / "pre-update-1111111.dump").write_text("alt")
        updates.backup_database()
        self.assertFalse((Path(self.folder) / "pre-update-1111111.dump").exists())

    def test_only_a_few_dumps_stay(self):
        for index in range(7):
            with mock.patch("core.updates.timezone.localtime", return_value=timezone.now() + timedelta(minutes=index)):
                updates.backup_database()
        self.assertEqual(len(list(Path(self.folder).glob(f"*{updates.DUMP_EXTENSION}"))), updates.KEEP_BACKUPS)

    def test_restore_undoes_new_tables_and_later_rows(self):
        name = updates.backup_database()
        Person.objects.filter(pk=self.person.pk).update(last_name="Nachher")
        Person.objects.create(kind="pupil", first_name="Später", last_name="Dazu")
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE neu_mit_fk (id serial PRIMARY KEY, person_id uuid REFERENCES people_person(id))")
            cursor.execute("CREATE TABLE neu_ohne_fk (id serial PRIMARY KEY)")
        updates.restore_database(name)
        self.assertEqual(Person.objects.get().last_name, "Vorher")
        tables = connection.introspection.table_names()
        self.assertNotIn("neu_mit_fk", tables)
        self.assertNotIn("neu_ohne_fk", tables)
        Person.objects.create(kind="pupil", first_name="Neu", last_name="Danach")

    def test_damaged_dump_changes_nothing(self):
        name = updates.backup_database()
        path = Path(self.folder) / name
        path.write_bytes(path.read_bytes()[:-40])
        Person.objects.filter(pk=self.person.pk).update(last_name="Nachher")
        with self.assertRaises(ContainerError):
            updates.restore_database(name)
        self.assertEqual(Person.objects.get().last_name, "Nachher")

    def test_error_after_the_tables_are_gone_changes_nothing(self):
        name = updates.backup_database()
        Person.objects.filter(pk=self.person.pk).update(last_name="Nachher")
        with mock.patch("core.updates.COMMIT", b"SELECT no_such_function();\nCOMMIT;\n"):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                updates.restore_database(name)
        self.assertIn(b"no_such_function", caught.exception.stderr)
        self.assertEqual(Person.objects.get().last_name, "Nachher")

    def test_input_that_stops_early_is_not_committed(self):
        """What psql sees when the worker feeding it is killed."""
        name = updates.backup_database()
        Person.objects.filter(pk=self.person.pk).update(last_name="Nachher")
        with mock.patch("core.updates.COMMIT", b""):
            updates.restore_database(name)
        self.assertEqual(Person.objects.get().last_name, "Nachher")

    def test_sql_failure_in_the_middle_reports_the_real_cause(self):
        name = updates.backup_database()
        with mock.patch("core.updates.CLEAR_TABLES", b"BEGIN;\nSELECT no_such_function();\n"):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                updates.restore_database(name)
        self.assertIn(b"no_such_function", caught.exception.stderr)

    def test_command_needs_confirmation(self):
        name = updates.backup_database()
        Person.objects.filter(pk=self.person.pk).update(last_name="Nachher")
        with self.assertRaises(CommandError):
            call_command("restore_dump", name, stdout=StringIO())
        self.assertEqual(Person.objects.get().last_name, "Nachher")
        call_command("restore_dump", name, "--confirm", stdout=StringIO())
        self.assertEqual(Person.objects.get().last_name, "Vorher")

    def test_command_brings_an_older_dump_up_to_this_version(self):
        name = updates.backup_database()
        with mock.patch("core.management.commands.restore_dump.call_command") as migrate:
            call_command("restore_dump", name, "--confirm", stdout=StringIO())
        migrate.assert_called_once()
        self.assertEqual(migrate.call_args.args, ("migrate",))


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def upload_file(self, source, bucket, key, Callback=None):
        self.objects[key] = Path(source).stat().st_size
        if Callback:
            Callback(1)

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.objects[Key]}

    def delete_object(self, Bucket, Key):
        del self.objects[Key]

    def get_paginator(self, name):
        return self

    def paginate(self, Bucket, Prefix):
        return [{"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}]


@override_settings(BACKUP_PASSWORD="nachts-sicher-genug")
class NightlyTests(FastPassword, DumpFolder, TransactionTestCase):
    def setUp(self):
        super().setUp()
        setup()
        Person.objects.create(kind="pupil", first_name="Lena", last_name="Muster")

    def bucket(self):
        fake = FakeBucket()
        patcher = mock.patch("archive.offsite._client", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        override = override_settings(BACKUP_S3_BUCKET="b", BACKUP_S3_ACCESS_KEY="a", BACKUP_S3_SECRET_KEY="s", BACKUP_S3_PREFIX="schule")
        override.enable()
        self.addCleanup(override.disable)
        return fake

    def test_makes_a_complete_file(self):
        result = nightly.run()
        self.assertEqual((result["state"], result["offsite"]), ("ok", "off"))
        path = Path(self.folder) / result["name"]
        snapshot.verify(path, "nachts-sicher-genug")
        self.assertEqual(jobs.containers()[0]["details"]["origin"], "automatisch")
        self.assertTrue(AuditEvent.objects.filter(summary__contains="erstellt und geprüft").exists())

    def test_file_is_not_readable_without_the_password(self):
        path = Path(self.folder) / nightly.run()["name"]
        with self.assertRaises(ContainerError):
            snapshot.verify(path, "ein-anderes-passwort")

    def test_cut_off_file_is_noticed(self):
        path = Path(self.folder) / nightly.run()["name"]
        path.write_bytes(path.read_bytes()[:-30])
        with self.assertRaises(ContainerError):
            snapshot.verify(path, "nachts-sicher-genug")

    def test_bad_file_is_thrown_away_and_reported(self):
        with mock.patch("archive.nightly.snapshot.verify", side_effect=ContainerError("x")):
            result = nightly.run()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(jobs.containers(), [])
        self.assertEqual(nightly.summary()["level"], "bad")

    def test_too_little_space_is_reported(self):
        with mock.patch("archive.nightly.shutil.disk_usage", return_value=mock.Mock(free=1)):
            result = nightly.run()
        self.assertIn("nicht genug Platz", result["problem"])

    def test_copies_off_the_server_and_counts_only_when_all_arrived(self):
        fake = self.bucket()
        result = nightly.run()
        self.assertEqual(result["offsite"], "ok")
        self.assertEqual(list(fake.objects), [f"schule/{result['name']}"])
        self.assertEqual(nightly.summary()["level"], "ok")

    def test_short_upload_is_a_failure(self):
        fake = self.bucket()
        with mock.patch.object(fake, "head_object", return_value={"ContentLength": 1}):
            result = nightly.run()
        self.assertEqual((result["state"], result["offsite"]), ("ok", "failed"))
        self.assertEqual(nightly.summary()["level"], "bad")
        with mock.patch.object(fake, "head_object", return_value={"ContentLength": 1}), self.assertRaises(CommandError):
            call_command("nightly_backup", stdout=StringIO())

    @mock.patch("archive.nightly.FIRST_HOUR", 0)  # is_due() waits until 02:00; the test must not depend on the hour.
    def test_failed_copy_is_sent_again_without_a_new_file(self):
        fake = self.bucket()
        with mock.patch.object(fake, "upload_file", side_effect=OSError("weg")):
            first = nightly.run()
        self.assertTrue(nightly.is_due())
        second = nightly.run()
        self.assertEqual(second["offsite"], "ok")
        self.assertEqual(second["name"], first["name"])
        self.assertEqual(len(jobs.containers()), 1)
        self.assertFalse(nightly.is_due())

    def test_old_files_go_away_here_and_there(self):
        fake = self.bucket()
        for index in range(nightly.KEEP_HERE + 2):
            with mock.patch("archive.jobs.new_name", return_value=f"nacht-2026-01-{index + 10:02}-020000.svbackup"):
                nightly.run()
        self.assertEqual(len(jobs.containers()), nightly.KEEP_HERE)
        for index in range(40):
            fake.objects[f"schule/nacht-2025-01-{index:02}.svbackup"] = 1
        offsite.prune(nightly.KEEP_ELSEWHERE)
        self.assertEqual(len(fake.objects), nightly.KEEP_ELSEWHERE)

    def test_other_files_stay(self):
        (Path(self.folder) / "eigene.svbackup").write_text("x")
        for index in range(nightly.KEEP_HERE + 1):
            with mock.patch("archive.jobs.new_name", return_value=f"nacht-2026-01-{index + 10:02}-020000.svbackup"):
                nightly.run()
        self.assertTrue((Path(self.folder) / "eigene.svbackup").exists())

    def night(self, hour):
        return timezone.localtime().replace(hour=hour)

    def test_due_after_two_and_only_once(self):
        with mock.patch("archive.nightly.timezone.localtime", return_value=self.night(1)):
            self.assertFalse(nightly.is_due())
        with mock.patch("archive.nightly.timezone.localtime", return_value=self.night(3)):
            self.assertTrue(nightly.is_due())
            nightly.run()
            self.assertFalse(nightly.is_due())

    def test_failed_night_tries_again(self):
        with mock.patch("archive.nightly.timezone.localtime", return_value=self.night(3)):
            with mock.patch("archive.nightly.snapshot.verify", side_effect=ContainerError("x")):
                nightly.run()
            self.assertTrue(nightly.is_due())

    @override_settings(BACKUP_PASSWORD="")
    def test_stays_quiet_until_set_up(self):
        out = StringIO()
        call_command("nightly_backup", "--if-due", stdout=out)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(nightly.summary()["level"], "warn")
        with self.assertRaises(CommandError):
            call_command("nightly_backup", stdout=StringIO())

    def test_waits_for_another_backup(self):
        with mock.patch("archive.jobs.is_running", return_value=True):
            self.assertEqual(nightly.run(), {})

    def test_old_result_is_a_warning(self):
        nightly.run()
        result = nightly.last()
        result["at"] = (timezone.now() - timedelta(hours=40)).isoformat()
        nightly._save(result)
        self.assertEqual(nightly.summary()["level"], "bad")

    def test_page_says_how_it_stands(self):
        user = User.objects.create_user("admin", password="x" * 14, role=Role.IT)
        SchoolSettings.objects.update(require_2fa=False)
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("backups")), "noch keine automatische Sicherung")
