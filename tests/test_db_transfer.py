"""Moving a computer's database to a server, and a server's full backup to a computer (archive/transfer.py).

Server and computer share the test database here, so the computer's calls to the server go through
Django's test client (with the server's own backup folder) instead of the network.
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import pyotp
from django.test import Client, override_settings
from django.urls import reverse

from archive import jobs, snapshot, transfer
from core.models import Role, SchoolSettings, User
from sync import client as sync_client
from sync.models import Device
from sync.views import hash_token
from vault import crypto

from .test_archive import PASSWORD, ArchiveCase

SERVER = "https://server.example"
CLIENT = {"SCHULE_CLIENT_DB_NAME": "Meine Schule", "ALLOW_DEMO_DATA": "", "SYNC_TOKEN": ""}


class TransferCase(ArchiveCase):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict("os.environ", CLIENT)
        environment.start()
        self.addCleanup(environment.stop)
        self.server_backups = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.server_backups, ignore_errors=True)
        for patcher in (mock.patch("sync.client._request", self.fake_request), mock.patch("archive.transfer._download", self.fake_download),
                        mock.patch.object(transfer, "POLL_SECONDS", 0), mock.patch.object(transfer, "CHUNK", 64 * 1024)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.secret = pyotp.random_base32()
        self.it.totp_secret = crypto.encrypt(self.secret, f"totp:{self.it.pk}")
        self.it.totp_confirmed = True
        self.it.set_password(PASSWORD)
        self.it.save()
        SchoolSettings.objects.filter(pk=SchoolSettings.load().pk).update(require_2fa=True)
        self.calls = []

    def code(self):
        """A fresh code: each one counts once, so the last used step is forgotten first."""
        User.objects.filter(pk=self.it.pk).update(totp_last_step=0)
        return pyotp.TOTP(self.secret).now()

    def server(self):
        return override_settings(BACKUP_ROOT=self.server_backups)

    def fake_request(self, method, url, token=None, body=None, raw=None, timeout=120, extra=None):
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self.calls.append(parts.path)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        with self.server():
            server = Client()
            if method == "GET":
                response = server.get(path, **headers)
            elif raw is not None:
                response = server.post(path, data=raw, content_type="application/octet-stream", **headers)
            else:
                response = server.post(path, data=json.dumps(body or {}), content_type="application/json", **headers)
        content = b"".join(response.streaming_content) if response.streaming else response.content
        if response.status_code >= 400:
            answer = json.loads(content or b"{}")
            error = sync_client.SyncError(answer.get("error", f"{response.status_code}"))
            error.answer = answer
            raise error
        return response.status_code, content

    def fake_download(self, url, ticket, target):
        _status, content = self.fake_request("GET", url, ticket)
        target.write_bytes(content)


class ServerApiTests(TransferCase):
    def start(self, **extra):
        data = {"username": "it", "password": PASSWORD, "code": self.code(), "outline": transfer.outline(), **extra}
        with self.server():
            return Client().post("/sync/v1/transfer/start", data=json.dumps(data), content_type="application/json")

    def test_start_names_what_the_server_holds(self):
        answer = self.start().json()
        self.assertTrue(answer["has_data"])
        self.assertEqual(answer["holdings"]["people"], 1)
        self.assertTrue(answer["ticket"])

    def test_wrong_code_and_wrong_role_are_refused(self):
        self.assertEqual(self.start(code="000000").status_code, 403)
        self.it.role = Role.SEKRETARIAT
        self.it.save()
        response = self.start()
        self.assertEqual(response.status_code, 403)
        self.assertIn("IT", response.json()["error"])

    def test_newer_backup_is_refused_before_the_upload(self):
        outline = transfer.outline()
        outline["migrations"].append(["core", "9999_aus_der_zukunft"])
        response = self.start(outline=outline)
        self.assertEqual(response.status_code, 409)
        self.assertIn("neueren Version", response.json()["error"])

    def test_pieces_need_a_valid_ticket(self):
        with self.server():
            response = Client().post("/sync/v1/transfer/chunk?offset=0&total=10", data=b"SVBACKUP12", content_type="application/octet-stream",
                                     HTTP_AUTHORIZATION="Bearer gefälscht")
        self.assertEqual(response.status_code, 401)

    def test_a_backup_ticket_does_not_upload(self):
        ticket = transfer._ticket("backup", name="x", device="y")
        with self.server():
            response = Client().post("/sync/v1/transfer/chunk?offset=0&total=10", data=b"SVBACKUP12", content_type="application/octet-stream",
                                     HTTP_AUTHORIZATION=f"Bearer {ticket}")
        self.assertEqual(response.status_code, 401)

    def test_first_piece_must_be_a_container(self):
        ticket = self.start().json()["ticket"]
        with self.server():
            response = Client().post("/sync/v1/transfer/chunk?offset=0&total=10", data=b"PK\x03\x04000000", content_type="application/octet-stream",
                                     HTTP_AUTHORIZATION=f"Bearer {ticket}")
        self.assertEqual(response.status_code, 400)

    def test_empty_server(self):
        self.assertFalse(transfer.has_data({"people": 0, "assets": 0, "credentials": 0, "users": 1}))
        self.assertTrue(transfer.has_data({"people": 0, "assets": 0, "credentials": 0, "users": 2}))


class TransferTests(TransferCase):
    def check(self):
        return transfer.check("server.example", "it", PASSWORD, self.code())

    def test_whole_transfer_replaces_the_server_after_a_backup_of_it(self):
        answer = self.check()
        self.assertEqual(answer["url"], SERVER)
        self.assertTrue(answer["has_data"])
        job = transfer.start(answer["url"], answer["ticket"], PASSWORD, replace=True)
        self.assertEqual(jobs.current()["state"], "done", jobs.current()["message"])
        self.assertIn("Arbeiten Sie ab jetzt mit dem Server", jobs.current()["message"])
        self.assertEqual(job["kind"], "transfer")
        self.assertEqual(transfer.transferred()["url"], SERVER)
        names = sorted(p.name for p in self.server_backups.glob(f"*{snapshot.EXTENSION}"))
        self.assertTrue(any(n.startswith("vor-dem-einspielen-") for n in names), names)
        self.assertTrue(any(n.startswith("uebertragung-") for n in names), names)
        self.assertFalse(list(jobs.root().glob(f".uebertragung-*")), "the computer's packed copy is gone")
        self.assertIn("/sync/v1/transfer/chunk", self.calls)

    def test_server_with_data_needs_the_confirmation(self):
        answer = self.check()
        transfer.start(answer["url"], answer["ticket"], PASSWORD, replace=False)
        self.assertEqual(jobs.current()["state"], "failed")
        self.assertIn("inzwischen Daten", jobs.current()["message"])
        self.assertFalse(transfer.transferred())

    def test_old_server_is_named(self):
        with mock.patch("sync.client.server_info", return_value={"version": "1.0.0"}):
            with self.assertRaisesMessage(sync_client.SyncError, "1.1 oder neuer"):
                self.check()

    def test_page_runs_both_steps(self):
        page = self.login(self.it)
        answer = page.post(reverse("backup_transfer_check"), {"url": "server.example", "username": "it", "password": PASSWORD, "code": self.code(),
                                                              "backup_password": PASSWORD, "backup_repeat": PASSWORD})
        self.assertEqual(answer.status_code, 200, answer.content)
        body = answer.json()
        self.assertNotIn("chunk", body)
        started = page.post(reverse("backup_transfer_run"), {"url": body["url"], "ticket": body["ticket"], "backup_password": PASSWORD, "replace": "1"})
        self.assertEqual(started.status_code, 200)
        self.assertEqual(jobs.current()["state"], "done", jobs.current()["message"])

    def test_page_refuses_a_short_password_before_asking_the_server(self):
        page = self.login(self.it)
        answer = page.post(reverse("backup_transfer_check"), {"url": "server.example", "username": "it", "password": PASSWORD, "code": "1",
                                                              "backup_password": "kurz", "backup_repeat": "kurz"})
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.calls, [])


class ServerBackupTests(TransferCase):
    def setUp(self):
        super().setUp()
        self.token = "geraete-token"
        self.device = Device.objects.create(node="mac-1", name="Mac von IT", user=self.it, token_hash=hash_token(self.token))
        from sync.models import ClientState

        ClientState.objects.update_or_create(pk=1, defaults={"server_url": SERVER})

    def test_connected_copy_fetches_the_full_backup(self):
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": self.token}):
            transfer.fetch_server_backup(PASSWORD, self.code(), PASSWORD)
        self.assertEqual(jobs.current()["state"], "done", jobs.current()["message"])
        here = [row for row in jobs.containers() if row["name"].startswith("server-")]
        self.assertEqual(len(here), 1)
        self.assertEqual(here[0]["details"]["origin"], "server")
        self.assertEqual(snapshot.read_manifest(jobs.root() / here[0]["name"], PASSWORD)["rows"]["people.person"], 1)

    def test_token_alone_is_not_enough(self):
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": self.token}):
            with self.assertRaisesMessage(sync_client.SyncError, "Code"):
                transfer.fetch_server_backup(PASSWORD, "000000", PASSWORD)

    def test_revoked_computer_gets_nothing(self):
        from django.utils import timezone

        Device.objects.filter(pk=self.device.pk).update(revoked_at=timezone.now())
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": self.token}):
            with self.assertRaisesMessage(sync_client.SyncError, "getrennt"):
                transfer.fetch_server_backup(PASSWORD, self.code(), PASSWORD)

    def test_page_on_the_connected_copy(self):
        page = self.login(self.it)
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": self.token}):
            answer = page.post(reverse("backup_from_server"), {"password": PASSWORD, "code": self.code(), "backup_password": PASSWORD, "backup_repeat": PASSWORD})
            self.assertEqual(answer.status_code, 200, answer.content)
            self.assertEqual(page.post(reverse("backup_restore"), {"name": "x"}).status_code, 403)
