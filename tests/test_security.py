import json
import re
import urllib.error
from datetime import date, timedelta

import pyotp
from django.test import override_settings
from django.urls import reverse

from core import sealed
from core.models import AuditEvent, ChangeLog, SchoolSettings, User
from core.permissions import CAPABILITIES
from devices.models import Asset
from masks.models import FieldDefinition
from masks.services import set_value
from people.models import Person
from prints.models import PrintJob, PrintTemplate
from vault import crypto, generator
from vault.models import Credential
from vault.services import generate_login

from .base import AppTestCase


class VaultTests(AppTestCase):
    def test_roundtrip_and_context_binding(self):
        token = crypto.encrypt("Geheim-123", "a:1")
        self.assertTrue(token.startswith("v1:"))
        self.assertNotIn("Geheim", token)
        self.assertEqual(crypto.decrypt(token, "a:1"), "Geheim-123")
        with self.assertRaises(Exception):
            crypto.decrypt(token, "a:2")

    def test_same_plaintext_gives_different_ciphertext(self):
        self.assertNotEqual(crypto.encrypt("x", "c"), crypto.encrypt("x", "c"))

    def test_generated_passwords_avoid_lookalikes(self):
        for profile in generator.PROFILES:
            for _ in range(200):
                self.assertNotRegex(generator.generate(profile), r"[lI1O0]")

    def test_stored_credential_is_not_plaintext_and_log_is_masked(self):
        credential = Credential.get_for(self.m365, person=self.pupil)
        credential.values["username"] = "lena"
        credential.set_secret("password", "Klartext-Passwort")
        credential.save()
        stored = Credential.objects.get(pk=credential.pk)
        self.assertNotIn("Klartext", json.dumps(stored.secrets))
        self.assertEqual(stored.get_secret("password"), "Klartext-Passwort")
        logged = " ".join(ChangeLog.objects.filter(record_id=credential.pk).values_list("new_value", flat=True))
        self.assertNotIn("Klartext", logged)
        self.assertNotIn("v1:", logged)

    def test_moving_ciphertext_to_another_record_fails(self):
        a = Credential.get_for(self.m365, person=self.pupil)
        a.set_secret("password", "x")
        a.save()
        other = Person.objects.create(kind="pupil", first_name="B", last_name="C")
        b = Credential.get_for(self.m365, person=other)
        b.secrets["password"] = a.secrets["password"]
        with self.assertRaises(Exception):
            b.get_secret("password")

    def test_custom_secret_field_is_encrypted(self):
        fd = FieldDefinition.objects.create(entity="pupil", key="x_pin", label="PIN", type="secret")
        set_value(self.pupil, fd, "4711")
        self.assertNotIn("4711", json.dumps(Person.objects.get(pk=self.pupil.pk).custom))


class PermissionTests(AppTestCase):
    def _credential(self):
        return generate_login(self.pupil, self.m365)

    def test_school_management_cannot_reveal(self):
        credential = self._credential()
        self.login(self.sl)
        response = self.client.post(reverse("credential_reveal", args=[credential.pk, "password"]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("credentials")).status_code, 403)
        self.assertEqual(self.client.get(reverse("person_block", args=[self.pupil.pk, "logins"])).status_code, 403)

    def test_reveal_is_audited_and_returns_value(self):
        credential = self._credential()
        self.login(self.sek)
        response = self.client.post(reverse("credential_reveal", args=[credential.pk, "password"]))
        self.assertContains(response, credential.get_secret("password"))
        event = AuditEvent.objects.get(action="reveal")
        self.assertEqual(event.user, self.sek)
        self.assertNotIn(credential.get_secret("password"), event.summary)

    @override_settings(REVEAL_LIMIT_PER_MINUTE=2)
    def test_reveal_rate_limit(self):
        credential = self._credential()
        self.login(self.it)
        url = reverse("credential_reveal", args=[credential.pk, "password"])
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 429)

    def test_reveal_needs_post(self):
        credential = self._credential()
        self.login(self.it)
        self.assertEqual(self.client.get(reverse("credential_reveal", args=[credential.pk, "password"])).status_code, 405)

    def test_only_valid_secret_fields_can_be_revealed(self):
        credential = self._credential()
        self.login(self.it)
        self.assertEqual(self.client.post(reverse("credential_reveal", args=[credential.pk, "username"])).status_code, 404)

    def test_read_only_role_cannot_edit(self):
        self.login(self.sl)
        response = self.client.post(reverse("person_field", args=[self.pupil.pk, "first_name"]), {"value": "Hack"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Lena")

    def test_admin_pages_are_it_only(self):
        self.login(self.sek)
        for name in ("mask_list", "field_list", "users", "platforms", "school_settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_export_with_passwords_is_it_only_and_logged(self):
        self._credential()
        url = reverse("export_credentials", args=["m365"])
        self.login(self.sek)
        self.assertEqual(self.client.post(url, {"confirm": "1"}).status_code, 403)
        self.login(self.it)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"confirm": "1"}).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="export_secrets").exists())

    def test_anonymous_is_redirected_everywhere(self):
        for url in ("/", "/schueler/", "/api/personen/?entity=pupil", "/zugaenge/", "/berichte/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/anmelden/", response.headers["Location"])

    def test_every_capability_has_a_role(self):
        self.assertTrue(all(CAPABILITIES.values()))


class SearchAndExportSecrecy(AppTestCase):
    def test_passwords_not_in_grid_data_search_or_export(self):
        credential = generate_login(self.pupil, self.m365)
        secret = credential.get_secret("password")
        self.login(self.it)
        data = self.client.get("/api/personen/?entity=pupil").content.decode()
        self.assertNotIn(secret, data)
        self.assertNotIn("v1:", data)
        self.assertNotContains(self.client.get("/suche/", {"q": secret[:6]}), self.pupil.display_name)
        payload = {"entity": "pupil", "format": "csv", "columns": [{"key": "first_name", "title": "Vorname"}, {"key": "secrets", "title": "x"}], "ids": [str(self.pupil.pk)]}
        export = self.client.post(reverse("export_rows"), {"payload": json.dumps(payload)})
        self.assertNotIn(secret, export.content.decode())


class SignInTests(AppTestCase):
    def test_lockout_after_repeated_failures(self):
        for _ in range(8):
            self.client.post(reverse("login"), {"username": "it", "password": "falsch"})
        response = self.client.post(reverse("login"), {"username": "it", "password": "x" * 14})
        self.assertContains(response, "Zu viele Fehlversuche")

    def test_open_redirect_is_blocked(self):
        response = self.client.post(reverse("login") + "?next=https://evil.example/", {"username": "it", "password": "x" * 14, "next": "https://evil.example/"})
        self.assertEqual(response.headers["Location"], "/")

    def test_two_factor_is_enforced_then_works(self):
        SchoolSettings.objects.update(require_2fa=True)
        self.client.post(reverse("login"), {"username": "it", "password": "x" * 14})
        self.assertIn("einrichten", self.client.get("/").headers["Location"])
        page = self.client.get(reverse("totp_setup"))
        secret = re.search(r'mono mt-1 break-all">([A-Z2-7]+)<', page.content.decode()).group(1)
        self.client.post(reverse("totp_setup"), {"code": pyotp.TOTP(secret).now()})
        self.assertEqual(self.client.get("/").status_code, 200)
        self.client.post(reverse("logout"))
        self.client.post(reverse("login"), {"username": "it", "password": "x" * 14})
        self.assertIn("zwei-faktor", self.client.get("/").headers["Location"])
        self.client.post(reverse("totp_verify"), {"code": "000000"})
        self.assertIn("zwei-faktor", self.client.get("/").headers["Location"])
        User.objects.filter(pk=self.it.pk).update(totp_last_step=0)  # Otherwise the enrolment code would count as used.
        self.client.post(reverse("totp_verify"), {"code": pyotp.TOTP(secret).now()})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_second_factor_cannot_be_skipped_on_api(self):
        SchoolSettings.objects.update(require_2fa=True)
        self.client.post(reverse("login"), {"username": "sl", "password": "x" * 14})
        self.assertEqual(self.client.get("/api/personen/?entity=pupil").status_code, 302)

    def _enrol(self, user):
        secret = pyotp.random_base32()
        user.totp_secret = crypto.encrypt(secret, f"totp:{user.pk}")
        user.totp_confirmed = True
        user.save()
        return secret

    def test_switching_two_factor_off_skips_the_code_for_everyone(self):
        SchoolSettings.objects.update(require_2fa=True)
        self._enrol(self.sek)
        self.login(self.it)
        self.client.post(reverse("two_factor_switch"), {"action": "off"})
        self.assertFalse(SchoolSettings.load().require_2fa)
        self.assertTrue(AuditEvent.objects.filter(action="2fa_policy", summary__contains="ausgeschaltet").exists())
        self.client.post(reverse("logout"))
        self.client.post(reverse("login"), {"username": "sek", "password": "x" * 14})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_switching_two_factor_on_again_asks_open_sessions(self):
        SchoolSettings.objects.update(require_2fa=False)
        secret = self._enrol(self.sek)
        self.client.post(reverse("login"), {"username": "sek", "password": "x" * 14})
        self.assertEqual(self.client.get("/").status_code, 200)
        SchoolSettings.objects.update(require_2fa=True)
        self.assertEqual(self.client.get("/").headers["Location"], reverse("totp_verify"))
        self.client.post(reverse("totp_verify"), {"code": pyotp.TOTP(secret).now()})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_only_it_can_switch_two_factor(self):
        self.login(self.sek)
        self.assertEqual(self.client.post(reverse("two_factor_switch"), {"action": "off"}).status_code, 403)
        self.login(self.it)
        self.assertEqual(self.client.get(reverse("two_factor_switch")).status_code, 405)

    def test_school_logo_is_public_but_other_uploads_stay_private(self):
        from django.conf import settings
        from django.core.files.base import ContentFile

        self.assertEqual(self.client.get(reverse("school_logo")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), reverse("school_logo"))
        school = SchoolSettings.load()
        school.logo.save("test-logo.png", ContentFile(b"logo"))
        try:
            self.assertContains(self.client.get(reverse("login")), f'src="{reverse("school_logo")}"')
            response = self.client.get(reverse("school_logo"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/png")
            self.assertEqual(b"".join(response.streaming_content), b"logo")
            self.assertEqual(self.client.get(f"{settings.MEDIA_URL}{school.logo.name}").status_code, 302)
        finally:
            school.logo.delete()

    def test_a_new_installation_starts_with_the_server_setting(self):
        with override_settings(REQUIRE_2FA=True):
            self.assertTrue(SchoolSettings().require_2fa)
        with override_settings(REQUIRE_2FA=False):
            self.assertFalse(SchoolSettings().require_2fa)

    def test_totp_secret_is_encrypted(self):
        self.login(self.it)
        self.client.get(reverse("totp_setup"))
        self.assertTrue(User.objects.get(pk=self.it.pk).totp_secret.startswith("v1:"))

    def test_microsoft_sign_in_never_creates_accounts(self):
        from core.oidc import ExistingUsersOnlyBackend

        backend = ExistingUsersOnlyBackend.__new__(ExistingUsersOnlyBackend)
        self.assertIsNone(backend.create_user({"email": "new@example.com"}))
        self.assertEqual(backend.filter_users_by_claims({"email": "new@example.com", "tid": "t", "oid": "o"}).count(), 0)

    def test_security_headers(self):
        response = self.client.get(reverse("login"))
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


class MediaTests(AppTestCase):
    def test_uploaded_files_need_sign_in_and_stay_inside_media(self):
        from pathlib import Path

        from django.conf import settings
        from django.core.signals import request_finished
        from django.db import close_old_connections

        root = Path(settings.MEDIA_ROOT)
        root.mkdir(exist_ok=True)
        (root / "test-private.txt").write_bytes(sealed.seal(b"x"))
        try:
            self.assertEqual(self.client.get("/media/test-private.txt").status_code, 302)
            self.login(self.sl)
            response = self.client.get("/media/test-private.txt")
            self.assertEqual(response.status_code, 200)
            # Windows cannot delete a file that is still open. Closed as the test client closes
            # responses itself, without the signal that would close the test's database connection.
            request_finished.disconnect(close_old_connections)
            try:
                response.close()
            finally:
                request_finished.connect(close_old_connections)
            self.assertEqual(self.client.get("/media/../manage.py").status_code, 404)
            self.assertEqual(self.client.get("/media/%2e%2e/manage.py").status_code, 404)
        finally:
            (root / "test-private.txt").unlink()


class UpdateTests(AppTestCase):
    def setUp(self):
        from unittest import mock

        self.mock = mock
        self.override = self.settings(COOLIFY_API_URL="https://coolify.example", COOLIFY_API_TOKEN="t", COOLIFY_APP_UUID="abc", APP_VERSION="1111111",
                                      UPDATE_SOURCE="commits")  # the releases mode: test_releases_compare_the_version
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.calls = []
        self.answers = {}
        patcher = mock.patch("core.updates._request", side_effect=self.fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)
        backup = mock.patch("core.updates.backup_database", return_value="pre-update-1111111.dump")
        self.backup = backup.start()
        self.addCleanup(backup.stop)
        watcher = mock.patch("core.updates._watch_in_background")
        self.watcher = watcher.start()
        self.addCleanup(watcher.stop)

    def fake_request(self, url, token="", method="GET"):
        self.calls.append((method, url, token))
        for part, answer in self.answers.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise OSError("unexpected")

    def deploy_ok(self):
        self.answers["/api/v1/deploy?"] = {"deployments": [{"deployment_uuid": "d1", "message": "queued"}]}

    def test_releases_compare_the_version(self):
        from core import updates

        self.answers["/releases/latest"] = {"tag_name": "v9.1.0", "name": "DataFluss 9.1.0", "published_at": "2027-01-05T10:00:00Z"}
        with self.settings(UPDATE_SOURCE="releases", DATAFLUSS_VERSION="9.0.3", GITHUB_REPOSITORY="rana-gmbh/DataFluss"):
            state = updates.check_for_update()
            self.assertEqual((state.behind, state.latest["sha"]), (1, "9.1.0"))
            self.assertIn("/repos/rana-gmbh/DataFluss/releases/latest", self.calls[-1][1])
            self.assertEqual(updates.public_status()["app_version"], "9.0.3")
        with self.settings(UPDATE_SOURCE="releases", DATAFLUSS_VERSION="9.1.0"):
            self.assertEqual(updates.check_for_update().behind, 0)
        with self.settings(UPDATE_SOURCE="releases", DATAFLUSS_VERSION="9.10.0"):  # numbers, not text
            self.assertEqual(updates.check_for_update().behind, 0)

    def test_only_it_can_see_or_start_updates(self):
        self.deploy_ok()
        for user in (self.sek, self.sl):
            self.login(user)
            self.assertEqual(self.client.get(reverse("updates")).status_code, 403)
            self.assertEqual(self.client.get(reverse("update_status")).status_code, 403)
            self.assertEqual(self.client.post(reverse("update_request"), {"action": "apply"}).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_start_backs_up_first_then_deploys_and_is_audited(self):
        self.deploy_ok()
        self.login(self.it)
        response = self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "running")
        self.backup.assert_called_once()
        self.assertEqual(self.calls[-1], ("POST", "https://coolify.example/api/v1/deploy?uuid=abc", "t"))
        self.assertTrue(AuditEvent.objects.filter(action="update", user=self.it).exists())

    def test_failed_backup_means_no_deploy(self):
        from core.updates import UpdateError

        self.backup.side_effect = UpdateError("Die Datenbank konnte nicht gesichert werden.")
        self.deploy_ok()
        self.login(self.it)
        response = self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.calls, [])

    def test_unreachable_coolify_and_odd_answers_are_told_apart(self):
        self.login(self.it)
        cases = [
            (urllib.error.URLError("timed out"), "nicht erreichbar"),
            (TimeoutError("timed out"), "unerwartet"),
            ({"message": "ok"}, "unerwartet"),
        ]
        for answer, text in cases:
            self.answers["/api/v1/deploy?"] = answer
            response = self.client.post(reverse("update_request"), {"action": "apply"})
            self.assertEqual(response.status_code, 502)
            self.assertIn(text, response.json()["error"])

    def test_get_cannot_start_an_update(self):
        self.login(self.it)
        self.assertEqual(self.client.get(reverse("update_request"), {"action": "apply"}).status_code, 405)

    def test_unknown_actions_are_refused(self):
        self.login(self.it)
        self.assertEqual(self.client.post(reverse("update_request"), {"action": "x"}).status_code, 400)

    def test_second_update_is_refused_while_running(self):
        self.deploy_ok()
        self.answers["/deployments/d1"] = {"status": "in_progress", "logs": "[]"}
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(self.client.post(reverse("update_request"), {"action": "apply"}).status_code, 409)

    def test_rejected_token_is_reported(self):
        import urllib.error

        self.answers["/api/v1/deploy?"] = urllib.error.HTTPError("u", 403, "x", {}, None)
        self.login(self.it)
        response = self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Token", response.json()["error"])

    def test_unreachable_coolify_during_switch_keeps_waiting(self):
        self.deploy_ok()
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(self.client.get(reverse("update_status")).json()["state"], "running")

    def test_finished_is_only_success_when_the_version_really_changed(self):
        self.deploy_ok()
        self.answers["/deployments/d1"] = {"status": "finished", "logs": "[]"}
        self.answers["api.github.com"] = {"sha": "2222222aaa", "commit": {"message": "Neu", "committer": {"date": ""}}, "ahead_by": 1, "commits": []}
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})
        self.assertEqual(self.client.get(reverse("update_status")).json()["result"], "failed")
        with self.settings(APP_VERSION="2222222"):
            status = self.client.get(reverse("update_status")).json()
        self.assertEqual(status["result"], "ok")

    def fail_deployment(self):
        self.deploy_ok()
        self.answers["/deployments/d1"] = {"status": "failed"}
        self.answers["api.github.com"] = {"sha": "2222222aaa", "commit": {"message": "Neu", "committer": {"date": ""}}, "ahead_by": 1, "commits": []}
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})

    def test_failed_deployment_with_foreign_migrations_restores_the_backup(self):
        self.fail_deployment()
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database") as restore:
            status = self.client.get(reverse("update_status")).json()
            self.client.get(reverse("update_status"))
        restore.assert_called_once_with("pre-update-1111111.dump")
        self.assertEqual(status["result"], "failed")
        self.assertIn("zurückgesetzt", status["message"])
        self.assertTrue(AuditEvent.objects.filter(summary__contains="wiederhergestellt").exists())

    def test_failed_deployment_without_database_changes_restores_nothing(self):
        self.fail_deployment()
        with self.mock.patch("core.updates.unknown_migrations", return_value=[]), self.mock.patch("core.updates.restore_database") as restore:
            status = self.client.get(reverse("update_status")).json()
        restore.assert_not_called()
        self.assertNotIn("zurückgesetzt", status["message"])

    def test_failed_restore_tells_the_user_what_to_do(self):
        import subprocess

        self.fail_deployment()
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database", side_effect=subprocess.CalledProcessError(1, "x")):
            status = self.client.get(reverse("update_status")).json()
        self.assertIn("von Hand", status["message"])
        self.assertIn("pre-update-1111111.dump", status["message"])

    def test_restored_database_keeps_the_update_progress(self):
        from core.models import UpdateState

        def wipe(name):
            UpdateState.objects.filter(pk=1).update(deployment_status="", deployment_uuid="", backup_name="")

        self.fail_deployment()
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database", side_effect=wipe):
            self.client.get(reverse("update_status"))
        state = UpdateState.load()
        self.assertEqual((state.deployment_status, state.restored), ("failed", "ok"))

    def age_update(self, **delta):
        from django.utils import timezone

        from core.models import UpdateState

        UpdateState.objects.filter(pk=1).update(started_at=timezone.now() - timedelta(**delta))

    def test_update_is_followed_without_any_open_page(self):
        from core import updates
        from core.models import UpdateState

        self.deploy_ok()
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})
        self.watcher.assert_called_once()
        states = iter(["in_progress", "failed"])
        self.answers["/deployments/d1"] = {"status": "in_progress"}

        def wait(_):
            self.answers["/deployments/d1"] = {"status": next(states)}

        self.answers["api.github.com"] = {"sha": "2222222aaa", "commit": {"message": "Neu", "committer": {"date": ""}}, "ahead_by": 1, "commits": []}
        with self.mock.patch("core.updates.time.sleep", side_effect=wait), self.mock.patch("core.updates.connection.close"), self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database") as restore:
            updates._watch()
        restore.assert_called_once()
        self.assertEqual(UpdateState.load().restored, "ok")

    def test_failure_found_late_waits_for_it(self):
        self.fail_deployment()
        self.age_update(minutes=16)
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database") as restore:
            status = self.client.get(reverse("update_status")).json()
            restore.assert_not_called()
            self.assertTrue(status["can_restore"])
            self.assertIn("geht dabei verloren", status["message"])
            status = self.client.post(reverse("update_request"), {"action": "restore"}).json()
        restore.assert_called_once_with("pre-update-1111111.dump")
        self.assertFalse(status["can_restore"])
        self.assertIn("zurückgesetzt", status["message"])

    def test_restore_on_request_runs_once(self):
        self.fail_deployment()
        self.age_update(minutes=16)
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database") as restore:
            self.client.get(reverse("update_status"))
            self.client.post(reverse("update_request"), {"action": "restore"})
            second = self.client.post(reverse("update_request"), {"action": "restore"})
        restore.assert_called_once()
        self.assertEqual(second.status_code, 409)

    def test_restore_nobody_asked_for_is_refused(self):
        self.fail_deployment()
        self.login(self.it)
        self.assertEqual(self.client.post(reverse("update_request"), {"action": "restore"}).status_code, 409)

    def test_only_it_can_ask_for_a_restore(self):
        self.login(self.sek)
        self.assertEqual(self.client.post(reverse("update_request"), {"action": "restore"}).status_code, 403)

    def start_update(self):
        self.deploy_ok()
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})

    def test_deployment_coolify_forgot_ends_after_a_while(self):
        from core import updates

        self.start_update()
        self.answers["/deployments/d1"] = OSError("404")
        self.assertEqual(self.client.get(reverse("update_status")).json()["state"], "running")
        self.age_update(hours=2)
        self.assertFalse(updates.blocks_restore())
        with self.mock.patch("core.updates.unknown_migrations", return_value=[]):
            status = self.client.get(reverse("update_status")).json()
        self.assertEqual(status["state"], "idle")
        self.assertIn("keine Auskunft", status["message"])

    def test_lost_deployment_with_changed_database_lets_it_decide(self):
        self.start_update()
        self.answers["/deployments/d1"] = OSError("404")
        self.age_update(hours=2)
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]), self.mock.patch("core.updates.restore_database") as restore:
            status = self.client.get(reverse("update_status")).json()
        restore.assert_not_called()
        self.assertTrue(status["can_restore"])

    def test_late_answer_from_coolify_still_counts(self):
        self.start_update()
        self.answers["/deployments/d1"] = OSError("404")
        self.age_update(hours=2)
        with self.mock.patch("core.updates.unknown_migrations", return_value=[("people", "0099_neu")]):
            self.client.get(reverse("update_status"))
            self.answers["/deployments/d1"] = {"status": "failed"}
            self.answers["api.github.com"] = {"sha": "2222222aaa", "commit": {"message": "Neu", "committer": {"date": ""}}, "ahead_by": 1, "commits": []}
            status = self.client.get(reverse("update_status")).json()
        self.assertTrue(status["can_restore"])
        self.assertIn("nicht übernommen", status["message"])

    def test_long_deployment_coolify_still_reports_stays_followed(self):
        from core import updates

        self.start_update()
        self.answers["/deployments/d1"] = {"status": "in_progress"}
        self.age_update(hours=2)
        status = self.client.get(reverse("update_status")).json()
        self.assertEqual(status["state"], "running")
        self.assertTrue(updates.is_running())
        self.assertFalse(updates.blocks_restore())

    def test_failed_restore_stays_visible_when_coolify_is_silent(self):
        from core import updates
        from core.models import UpdateState

        self.start_update()
        UpdateState.objects.filter(pk=1).update(deployment_status="unknown", restored="failed")
        self.assertIn("von Hand", updates.public_status()["message"])

    def test_interrupted_restore_does_not_vanish(self):
        from core import updates
        from core.models import UpdateState

        self.start_update()
        UpdateState.objects.filter(pk=1).update(deployment_status="failed", restored="working")
        self.assertIn("von Hand", updates.public_status()["message"])

    def test_page_links_to_the_log_in_coolify(self):
        self.deploy_ok()
        self.answers["/deployments/d1"] = {"status": "in_progress", "deployment_url": "/project/1/application/2/deployment/d1"}
        self.login(self.it)
        self.client.post(reverse("update_request"), {"action": "apply"})
        with self.settings(COOLIFY_API_URL="http://coolify:8080", COOLIFY_DASHBOARD_URL="https://coolify.example"):
            status = self.client.get(reverse("update_status")).json()
        self.assertEqual(status["deployment_url"], "https://coolify.example/project/1/application/2/deployment/d1")
        self.assertNotIn("log", status)

    def test_log_link_is_hidden_without_a_browser_address(self):
        from core import updates
        from core.models import UpdateState

        UpdateState.objects.update_or_create(pk=1, defaults={"deployment_url": "/project/1/deployment/d1"})
        self.assertEqual(updates.public_status()["deployment_url"], "")
        with self.settings(COOLIFY_DASHBOARD_URL="https://coolify.example"):
            UpdateState.objects.update(deployment_url="http://coolify:8080/project/1/deployment/d1")
            self.assertEqual(updates.public_status()["deployment_url"], "")

    def test_check_lists_new_commits(self):
        self.answers["/commits/main"] = {"sha": "2222222aaa", "commit": {"message": "Neue Funktion\n\nDetails", "committer": {"date": "2026-11-01T10:00:00Z"}}}
        self.answers["/compare/"] = {"ahead_by": 1, "commits": [{"sha": "2222222aaa", "commit": {"message": "Neue Funktion", "committer": {"date": ""}}}]}
        self.login(self.it)
        status = self.client.post(reverse("update_request"), {"action": "check"}).json()
        self.assertEqual(status["behind"], 1)
        self.assertEqual(status["latest"]["subject"], "Neue Funktion")
        self.assertEqual(status["commits"][0]["sha"], "2222222")

    def test_current_version_means_nothing_to_do(self):
        self.answers["/commits/main"] = {"sha": "1111111aaa", "commit": {"message": "Aktuell", "committer": {"date": ""}}}
        self.login(self.it)
        self.assertEqual(self.client.post(reverse("update_request"), {"action": "check"}).json()["behind"], 0)

    def test_rejected_github_token_is_reported(self):
        import urllib.error

        self.answers["api.github.com"] = urllib.error.HTTPError("u", 404, "x", {}, None)
        self.login(self.it)
        self.assertIn("GITHUB_TOKEN", self.client.post(reverse("update_request"), {"action": "check"}).json()["check_error"])

    def test_dashboard_mentions_update_for_it_only(self):
        from django.utils import timezone

        from core.models import UpdateState

        UpdateState.objects.update_or_create(pk=1, defaults={"behind": 2, "checked_at": timezone.now()})
        self.login(self.it)
        self.assertContains(self.client.get("/"), "neue Version")
        self.login(self.sek)
        self.assertNotContains(self.client.get("/"), "neue Version")

    def test_not_configured(self):
        with self.settings(COOLIFY_API_URL=""):
            self.login(self.it)
            self.assertContains(self.client.get(reverse("updates")), "noch nicht eingerichtet")
            self.assertEqual(self.client.post(reverse("update_request"), {"action": "apply"}).status_code, 400)

    def test_all_our_migrations_are_known(self):
        from core.updates import unknown_migrations

        self.assertEqual(unknown_migrations(), [])

    def test_tokens_never_appear_in_status(self):
        with self.settings(COOLIFY_API_TOKEN="geheimer-token", GITHUB_TOKEN="noch-geheimer"):
            self.login(self.it)
            page = self.client.get(reverse("updates")).content.decode()
        self.assertNotIn("geheimer-token", page)
        self.assertNotIn("noch-geheimer", page)


class StartupTests(AppTestCase):
    def test_health_check_is_public_and_silent(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_service_worker_keeps_nothing(self):
        self.assertNotContains(self.client.get("/sw.js"), "respondWith")

    def test_every_reference_in_the_static_files_can_be_hashed(self):
        """The image build fails on a reference to a missing file, so this is noticed here first."""
        import tempfile
        from io import StringIO

        from django.contrib.staticfiles.storage import staticfiles_storage
        from django.core.management import call_command
        from django.utils.functional import empty

        with tempfile.TemporaryDirectory() as folder:
            storages = {"staticfiles": {"BACKEND": "core.storage.VersionedStaticFilesStorage"}, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
            with override_settings(STATIC_ROOT=folder, STORAGES=storages):
                staticfiles_storage._wrapped = empty
                try:
                    call_command("collectstatic", "--noinput", stdout=StringIO())
                    self.assertRegex(staticfiles_storage.url("js/panel.js"), r"panel\.[0-9a-f]{12}\.js$")
                finally:
                    staticfiles_storage._wrapped = empty

    def test_missing_vault_key_stops_the_start_instead_of_making_a_new_one(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.core.management import call_command

        with self.settings(VAULT_KEY="", VAULT_KEY_FILE="/nonexistent/vault.key"):
            crypto.reset_key_cache()
            with self.assertRaises(ImproperlyConfigured):
                call_command("setup_school")
        crypto.reset_key_cache()
