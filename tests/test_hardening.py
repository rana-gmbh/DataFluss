import base64
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

import pyotp
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import audit, health
from core.models import AuditEvent, SchoolSettings, UpdateState, User
from core.oidc import ExistingUsersOnlyBackend
from people.models import Attachment
from transfer import parsers
from vault import crypto

from .base import AppTestCase

TENANT = "11111111-2222-3333-4444-555555555555"


class ClientAddressTests(AppTestCase):
    def request(self, forwarded):
        return RequestFactory().get("/", HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR="10.0.0.1")

    def test_only_the_address_the_proxy_added_counts(self):
        self.assertEqual(audit.client_ip(self.request("1.2.3.4, 5.6.7.8")), "5.6.7.8")

    def test_rubbish_does_not_break_logging(self):
        self.assertIsNone(audit.client_ip(self.request("kein-ip")))


class LoginLimitTests(AppTestCase):
    def attempt(self, username, address="9.9.9.9"):
        return self.client.post(reverse("login"), {"username": username, "password": "falsch"}, headers={"X-Forwarded-For": address})

    def test_spraying_over_many_accounts_stops(self):
        for index in range(50):
            self.attempt(f"name{index}")
        with mock.patch("core.views.authenticate") as check:
            response = self.attempt("sek")
        check.assert_not_called()
        self.assertContains(response, "Zu viele")

    def test_a_stranger_cannot_lock_someone_out(self):
        for _ in range(8):
            self.attempt("sek")
        self.assertContains(self.attempt("sek"), "Zu viele")
        response = self.client.post(reverse("login"), {"username": "sek", "password": "x" * 14}, headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(response.status_code, 302)

    def test_password_typed_as_name_stays_out_of_the_log(self):
        self.attempt("mein-geheimes-passwort")
        event = AuditEvent.objects.get(action="login_failed")
        self.assertNotIn("geheimes", event.summary + event.target_id)


class ProductionSettingsTests(SimpleTestCase):
    """These settings are off in tests, so a fresh process shows them."""

    def values(self, **env):
        code = "import schule.settings as s; print(s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS > 0, s.SECURE_REDIRECT_EXEMPT, s.SECURE_PROXY_SSL_HEADER)"
        clean = {k: v for k, v in os.environ.items() if k not in ("TESTING", "DEBUG")}
        result = subprocess.run([sys.executable, "-c", code], env={**clean, "SECRET_KEY": "x", "DEBUG": "0", **env}, capture_output=True, text=True, check=True, cwd=Path(__file__).parent.parent)
        return result.stdout

    def test_https_is_enforced_except_for_the_health_check(self):
        out = self.values()
        self.assertTrue(out.startswith("True True"))
        self.assertIn("healthz", out)
        self.assertIn("HTTP_X_FORWARDED_PROTO", out)

    def test_plain_http_installation_can_switch_it_off(self):
        self.assertTrue(self.values(SECURE_COOKIES="0").startswith("False False"))


class TwoFactorTests(AppTestCase):
    def setUp(self):
        SchoolSettings.objects.update(require_2fa=True)
        self.secret = pyotp.random_base32()
        self.sek.totp_secret = crypto.encrypt(self.secret, f"totp:{self.sek.pk}")
        self.sek.totp_confirmed = True
        self.sek.save()

    def verify(self):
        self.client.force_login(self.sek)
        return self.client.post(reverse("totp_verify"), {"code": pyotp.TOTP(self.secret).now()})

    def test_a_code_works_once(self):
        self.assertRedirects(self.verify(), "/", fetch_redirect_response=False)
        self.client.logout()
        self.assertContains(self.verify(), "stimmt nicht")

    def test_code_from_the_setup_cannot_be_used_again(self):
        self.sek.totp_confirmed = False
        self.sek.save()
        self.client.force_login(self.sek)
        code = pyotp.TOTP(self.secret).now()
        self.client.post(reverse("totp_setup"), {"code": code})
        self.client.logout()
        self.client.force_login(self.sek)
        self.assertContains(self.client.post(reverse("totp_verify"), {"code": code}), "stimmt nicht")

    def test_session_key_changes_after_the_second_factor(self):
        self.client.force_login(self.sek)
        before = self.client.session.session_key
        self.client.post(reverse("totp_verify"), {"code": pyotp.TOTP(self.secret).now()})
        self.assertNotEqual(self.client.session.session_key, before)

    def test_confirmed_secret_is_not_shown_again(self):
        self.login(self.sek)
        self.assertRedirects(self.client.get(reverse("totp_setup")), "/", fetch_redirect_response=False)


class BodyLimitTests(AppTestCase):
    def test_huge_login_request_is_refused_unread(self):
        response = self.client.generic("POST", reverse("login"), b"", content_type="text/plain", headers={"Content-Length": "2000000"})
        self.assertEqual(response.status_code, 413)

    def test_signed_in_users_get_more(self):
        self.login(self.it)
        response = self.client.generic("POST", reverse("session_check"), b"", content_type="text/plain", headers={"Content-Length": "2000000"})
        self.assertNotEqual(response.status_code, 413)


@override_settings(MAX_UPLOAD_BYTES=10)
class UploadSizeTests(AppTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.login(self.it)

    def test_oversized_attachment_is_refused_with_a_message(self):
        response = self.client.post(reverse("person_attachment", args=[self.pupil.pk]), {"file": SimpleUploadedFile("a.pdf", b"x" * 11)})
        self.assertIn("20 MB", response["HX-Trigger"])
        self.assertFalse(Attachment.objects.exists())


class AttachmentHistoryTests(AppTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.login(self.it)
        self.client.post(reverse("person_attachment", args=[self.pupil.pk]), {"file": SimpleUploadedFile("a.pdf", b"inhalt")})
        self.item = Attachment.objects.get()

    def test_delete_is_logged_and_removes_the_file(self):
        path = Path(self.item.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("person_attachment", args=[self.pupil.pk]), {"delete": self.item.pk})
        self.assertFalse(path.exists())
        self.assertTrue(AuditEvent.objects.filter(action="attachment").exists())

    def test_download_is_logged(self):
        self.client.get(reverse("attachment_download", args=[self.pupil.pk, self.item.pk]))
        self.assertTrue(AuditEvent.objects.filter(action="download").exists())


class SpreadsheetLimitTests(AppTestCase):
    def test_file_that_unpacks_to_too_much_is_refused(self):
        from transfer import exporters

        content = exporters.to_xlsx(["A"], [["x"]])
        with mock.patch("transfer.parsers.MAX_UNPACKED_BYTES", 10), self.assertRaises(ValueError):
            parsers.read_table("x.xlsx", content)


class PrivacyHeaderTests(AppTestCase):
    def test_personal_pages_are_not_kept(self):
        self.login(self.sek)
        self.assertIn("no-store", self.client.get(reverse("pupils"))["Cache-Control"])

    def test_login_page_can_be(self):
        self.assertNotIn("no-store", self.client.get(reverse("login")).get("Cache-Control", ""))


class HealthTests(AppTestCase):
    def test_unwritable_uploads_fail_the_check(self):
        with mock.patch("core.views.tempfile.TemporaryFile", side_effect=OSError):
            self.assertEqual(self.client.get(reverse("healthz")).status_code, 503)

    def test_healthy(self):
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)


class StartupTests(AppTestCase):
    def test_wrong_key_stops_the_start_before_anything_is_touched(self):
        call_command("setup_school", stdout=StringIO())
        self.assertTrue(SchoolSettings.load().key_check)
        with override_settings(VAULT_KEY=base64.b64encode(b"z" * 32).decode()):
            crypto.reset_key_cache()
            self.addCleanup(crypto.reset_key_cache)
            with self.assertRaisesMessage(CommandError, "VAULT_KEY"):
                call_command("check_installation", stdout=StringIO())

    def test_right_key_passes(self):
        call_command("setup_school", stdout=StringIO())
        call_command("check_installation", stdout=StringIO())

    def test_database_from_before_the_key_check_passes(self):
        with mock.patch("masks.management.commands.check_installation.stored_fingerprint", return_value=""):
            call_command("check_installation", stdout=StringIO())

    def test_order_of_the_start_script(self):
        script = (Path(__file__).parent.parent / "deploy" / "start.sh").read_text()
        steps = [script.index(step) for step in ("check_installation", "dump_before_migrate", "migrate --noinput", "setup_school", "gunicorn")]
        self.assertEqual(steps, sorted(steps))

    @override_settings(REQUIRE_DATA_VOLUME=True)
    def test_missing_volume_stops_the_start(self):
        with mock.patch("masks.management.commands.check_installation.data_is_a_volume", return_value=False):
            with self.assertRaisesMessage(CommandError, "/data"):
                call_command("check_installation", stdout=StringIO())

    def test_volume_is_read_from_the_mount_table(self):
        from masks.management.commands import check_installation

        table = "1 2 0:1 / /data rw - ext4 /dev/sda rw\n"
        with mock.patch("masks.management.commands.check_installation.Path") as path:
            path.return_value.read_text.return_value = table
            self.assertTrue(check_installation.data_is_a_volume())
            path.return_value.read_text.return_value = table.replace("/data", "/other")
            self.assertFalse(check_installation.data_is_a_volume())


class DumpBeforeMigrateTests(AppTestCase):
    def test_nothing_to_do_without_new_migrations(self):
        with mock.patch("core.updates.backup_database") as dump:
            call_command("dump_before_migrate", stdout=StringIO())
        dump.assert_not_called()

    def test_running_update_gets_the_newer_dump(self):
        UpdateState.objects.update_or_create(pk=1, defaults={"deployment_status": "in_progress", "backup_name": "alt", "started_at": timezone.now() - timedelta(minutes=9)})
        plan = mock.Mock(return_value=[("m", False)])
        with mock.patch("core.management.commands.dump_before_migrate.MigrationExecutor") as executor, mock.patch("core.updates.backup_database", return_value="neu"):
            executor.return_value.migration_plan = plan
            executor.return_value.loader.applied_migrations = {("core", "0001"): 1}
            call_command("dump_before_migrate", stdout=StringIO())
        state = UpdateState.load()
        self.assertEqual(state.backup_name, "neu")
        self.assertLess(timezone.now() - state.started_at, timedelta(minutes=1))


@override_settings(OIDC_TENANT=TENANT, OIDC_RP_CLIENT_ID="c", OIDC_RP_CLIENT_SECRET="s", OIDC_RP_SCOPES="openid email profile")
class MicrosoftTests(AppTestCase):
    def setUp(self):
        self.backend = ExistingUsersOnlyBackend.__new__(ExistingUsersOnlyBackend)
        self.sek.email = "sek@schule.example"
        self.sek.save()

    def claims(self, **extra):
        return {"tid": TENANT, "oid": "abc", "email": "sek@schule.example", **extra}

    def test_other_tenants_are_refused(self):
        self.assertFalse(self.backend.verify_claims(self.claims(tid="99999999-2222-3333-4444-555555555555")))

    def test_own_tenant_passes(self):
        self.assertTrue(self.backend.verify_claims(self.claims()))

    def test_address_finds_the_account_once_and_the_id_after_that(self):
        user = self.backend.filter_users_by_claims(self.claims()).get()
        self.backend.update_user(user, self.claims())
        self.assertEqual(self.backend.filter_users_by_claims(self.claims(email="neu@schule.example")).get(), user)

    def test_address_alone_cannot_take_a_bound_account(self):
        self.backend.update_user(self.sek, self.claims())
        self.assertFalse(self.backend.filter_users_by_claims(self.claims(oid="fremd")).exists())


class StartPageWarningTests(AppTestCase):
    def test_it_sees_what_is_wrong(self):
        self.login(self.it)
        audit.log("server_error", "ValueError bei GET x")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Serverfehler in den letzten 24 Stunden: 1")
        self.assertContains(response, "BACKUP_PASSWORD")

    def test_others_do_not(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("dashboard")).context["warnings"], [])

    def test_low_disk(self):
        self.login(self.it)
        with mock.patch("core.health.shutil.disk_usage", return_value=mock.Mock(free=1024 ** 3, total=100 * 1024 ** 3)):
            self.assertContains(self.client.get(reverse("dashboard")), "nur noch 1 GB")

    def test_plenty_of_space_on_a_big_disk_is_no_warning(self):
        self.login(self.it)
        with mock.patch("core.health.shutil.disk_usage", return_value=mock.Mock(free=140 * 1024 ** 3, total=2000 * 1024 ** 3)):
            self.assertNotContains(self.client.get(reverse("dashboard")), "nur noch")

    def test_normal_refusals_are_not_crashes(self):
        self.login(self.sl)
        self.client.get("/personen/00000000-0000-0000-0000-000000000000/")
        self.client.get(reverse("users"))
        self.assertFalse(AuditEvent.objects.filter(action="server_error").exists())

    def test_crash_is_counted(self):
        self.login(self.sek)
        self.client.raise_request_exception = False
        with mock.patch("people.views.render", side_effect=ValueError("kaputt")):
            response = self.client.get(reverse("guardians"))
        self.assertEqual(response.status_code, 500)
        self.assertTrue(AuditEvent.objects.filter(action="server_error", summary__contains="ValueError").exists())


class ErrorPageTests(AppTestCase):
    def test_not_found_is_german_and_has_a_way_back(self):
        self.login(self.sek)
        response = self.client.get("/gibt-es-nicht/")
        self.assertContains(response, "Nicht gefunden", status_code=404)
        self.assertContains(response, "Zur Startseite", status_code=404)

    def test_forbidden_is_german(self):
        self.login(self.sl)
        self.assertContains(self.client.get(reverse("users")), "Kein Zugriff", status_code=403)

    def test_public_not_found_works_without_a_user(self):
        self.assertContains(self.client.get(reverse("school_logo")), "Nicht gefunden", status_code=404)

    def test_csrf_failure_says_what_to_do(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        self.assertContains(client.post(reverse("login"), {"username": "x"}), "nicht gespeichert", status_code=403)
