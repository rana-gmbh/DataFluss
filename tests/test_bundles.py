"""Signed module bundles (schule/bundles.py), handing them out (sync/views.py) and fetching them (sync/client.py)."""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from nacl.signing import SigningKey

from core.models import SchoolSettings
from schule import bundles
from sync import client

from .base import PREMIUM, AppTestCase

KEY = SigningKey.generate()
PUBLIC = base64.b64encode(bytes(KEY.verify_key)).decode()
FILES = {"zeugnisbuch/__init__.py": b"", "zeugnisbuch/apps.py": b"from django.apps import AppConfig\n\n\nclass Config(AppConfig):\n    name = 'zeugnisbuch'\n"}


def make_bundle(folder, version=None, files=None, key=KEY, manifest_extra=None, extra_member=None, module_id="zeugnisbuch"):
    files = dict(FILES if files is None else files)
    manifest = {"format": bundles.FORMAT, "id": module_id, "version": version or bundles.platform_version(), "title": "Zeugnisbuch",
                "requires": [], "apps": ["zeugnisbuch"], "stylesheet": "",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, **(manifest_extra or {})}
    raw = json.dumps(manifest).encode()
    path = Path(folder) / f"{module_id}-{manifest['version']}{bundles.EXTENSION}"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", raw)
        zf.writestr("manifest.sig", base64.b64encode(key.sign(raw).signature))
        for name, data in files.items():
            zf.writestr(name, data)
        if extra_member:
            zf.writestr(extra_member, b"x")
    return path


class BundleCase(SimpleTestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = mock.patch.dict(os.environ, {"TESTING": "1", "DATAFLUSS_MODULE_KEYS": PUBLIC, "DATAFLUSS_MODULE_PATH": str(self.folder)})
        patcher.start()
        self.addCleanup(patcher.stop)
        bundles.reset()
        self.addCleanup(bundles.reset)


class ArchiveTests(BundleCase):
    def test_a_signed_bundle_is_read(self):
        self.assertEqual(bundles.read_archive(make_bundle(self.folder))["id"], "zeugnisbuch")

    def test_a_foreign_key_is_refused(self):
        with self.assertRaisesMessage(bundles.BundleError, "trusted"):
            bundles.read_archive(make_bundle(self.folder, key=SigningKey.generate()))

    def test_the_test_key_counts_only_in_tests(self):
        with mock.patch.dict(os.environ, {"TESTING": ""}), self.assertRaises(bundles.BundleError):
            bundles.read_archive(make_bundle(self.folder))

    def test_a_changed_file_is_refused(self):
        path = make_bundle(self.folder)
        with zipfile.ZipFile(path) as zf:
            members = {name: zf.read(name) for name in zf.namelist()}
        members["zeugnisbuch/apps.py"] += b"import os; os.system('boese')\n"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        with self.assertRaisesMessage(bundles.BundleError, "does not match"):
            bundles.read_archive(path)

    def test_files_outside_the_apps_or_unlisted_are_refused(self):
        with self.assertRaisesMessage(bundles.BundleError, "outside"):
            bundles.read_archive(make_bundle(self.folder, files={**FILES, "../schule/settings.py": b"x"}))
        with self.assertRaisesMessage(bundles.BundleError, "does not list"):
            bundles.read_archive(make_bundle(self.folder, extra_member="zeugnisbuch/extra.py"))

    def test_unpacking_keeps_the_archive_for_handing_out(self):
        incoming = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, incoming, True)
        target = bundles.unpack(make_bundle(incoming), self.folder)
        self.assertEqual(target, self.folder / "zeugnisbuch" / bundles.platform_version())
        self.assertTrue((target / "zeugnisbuch" / "apps.py").is_file())
        self.assertEqual(bundles.archive_for("zeugnisbuch", bundles.platform_version()).parent, self.folder)


class LoadTests(BundleCase):
    def test_this_versions_bundle_becomes_importable(self):
        make_bundle(self.folder)
        loaded = bundles.load(built_in=lambda name: False)
        self.assertIn("zeugnisbuch", loaded)
        self.assertIn(str(loaded["zeugnisbuch"]["path"]), sys.path)

    def test_another_version_and_a_built_in_module_are_left_alone(self):
        make_bundle(self.folder, version="0.0.1")
        make_bundle(self.folder, module_id="premium", manifest_extra={"apps": ["zeugnisbuch"]})
        loaded = bundles.load(built_in=lambda name: name == "premium")
        self.assertEqual(loaded, {})
        self.assertTrue((self.folder / "zeugnisbuch" / "0.0.1").is_dir())  # kept for the update to that version

    def test_a_bundle_changed_after_unpacking_is_not_loaded(self):
        make_bundle(self.folder)
        bundles.load(built_in=lambda name: False)
        bundles.reset()
        (self.folder / "zeugnisbuch" / bundles.platform_version() / "zeugnisbuch" / "apps.py").write_text("changed")
        self.assertEqual(bundles.load(built_in=lambda name: False), {})


class HandOutTests(AppTestCase):
    """The server hands out the bundle of a module it runs, to the user's own sign-in or a device token."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = mock.patch.dict(os.environ, {"DATAFLUSS_MODULE_PATH": str(self.folder)})
        patcher.start()
        self.addCleanup(patcher.stop)
        SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
        self.archive = self.folder / f"premium-{bundles.platform_version()}{bundles.EXTENSION}"
        self.archive.write_bytes(b"PK-bundle")

    def ask(self, password="x" * 14, module="premium", **headers):
        body = {"id": module} if "HTTP_AUTHORIZATION" in headers else {"id": module, "username": "it", "password": password}
        return self.client.post(reverse("sync_module"), json.dumps(body), content_type="application/json", **headers)

    @override_settings(DATAFLUSS_VERSION=bundles.platform_version())
    def test_with_the_users_password(self):
        response = self.ask()
        if PREMIUM:
            self.assertEqual((response.status_code, b"".join(response.streaming_content)), (200, b"PK-bundle"))
        else:
            self.assertEqual(response.status_code, 404)  # only modules the server runs

    def test_a_wrong_password_gets_nothing(self):
        self.assertEqual(self.ask(password="falsch-falsch-falsch").status_code, 403)

    def test_an_unknown_module_or_device_gets_nothing(self):
        self.assertEqual(self.ask(module="buchhaltung").status_code, 404)
        self.assertEqual(self.ask(HTTP_AUTHORIZATION="Bearer erfunden").status_code, 401)


class FetchTests(BundleCase):
    def test_a_fetched_bundle_is_checked_and_unpacked(self):
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source, True)
        data = make_bundle(source).read_bytes()
        with mock.patch.object(client, "_request", return_value=(200, data)) as request:
            self.assertTrue(client.fetch_module("https://server.example", "zeugnisbuch", {"username": "it", "password": "p", "code": ""}))
        self.assertEqual(request.call_args.kwargs["body"]["id"], "zeugnisbuch")
        self.assertTrue((self.folder / "zeugnisbuch" / bundles.platform_version()).is_dir())

    def test_a_bad_bundle_from_the_server_is_refused(self):
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source, True)
        data = make_bundle(source, key=SigningKey.generate()).read_bytes()
        with mock.patch.object(client, "_request", return_value=(200, data)), self.assertRaises(client.SyncError):
            client.fetch_module("https://server.example", "zeugnisbuch", token="t")
        self.assertFalse((self.folder / "zeugnisbuch").exists())
        self.assertEqual(list(self.folder.glob("*.part")), [])

    def test_a_missing_module_is_fetched_when_connecting(self):
        info = {"version": bundles.platform_version(), "modules": {"zeugnisbuch": bundles.platform_version()}, "schema": "s"}
        with mock.patch.object(client, "server_info", return_value=info), \
                mock.patch.object(client, "fetch_module", return_value=True) as fetch, \
                self.assertRaisesMessage(client.SyncError, "noch einmal verbinden"):
            client._check_server("https://server.example", {"username": "it", "password": "p", "code": ""})
        fetch.assert_called_once()



class PrefetchTests(BundleCase):
    def state(self):
        return mock.Mock(server_url="https://server.example")

    def test_a_used_module_is_kept_for_the_servers_version(self):
        info = {"version": "9.9.9", "modules": {"platform": "9.9.9", "premium": "9.9.9"}}
        with mock.patch.object(client, "server_info", return_value=info), \
                mock.patch("core.modules.manifest", return_value={"platform": "x", "premium": "x"}), \
                mock.patch.object(client, "fetch_module") as fetch:
            client.prefetch(self.state(), "token", force=True)
            client.prefetch(self.state(), "token")  # within the hour: nothing
        fetch.assert_called_once_with("https://server.example", "premium", token="token")

    def test_a_module_the_database_does_not_use_or_already_has_is_not_fetched(self):
        (self.folder / "premium-9.9.9.dfmodule").write_bytes(b"kept")
        info = {"version": "9.9.9", "modules": {"premium": "9.9.9", "buchhaltung": "9.9.9"}}
        with mock.patch.object(client, "server_info", return_value=info), \
                mock.patch("core.modules.manifest", return_value={"premium": "x"}), \
                mock.patch.object(client, "fetch_module") as fetch:
            client.prefetch(self.state(), "token", force=True)
        fetch.assert_not_called()


class OwnerTests(BundleCase):
    def test_an_app_is_named_by_a_kept_bundle_of_another_version(self):
        bundles.unpack(make_bundle(self.folder, version="0.0.1"), self.folder)
        self.assertEqual(bundles.owner_of("zeugnisbuch"), "zeugnisbuch")
        self.assertIsNone(bundles.owner_of("people"))


class SidecarFetchTests(SimpleTestCase):
    """A connected database opened by an app version that lacks its module fetches it from the server; the
    sidecar then ends with code 4 and the shell starts it once more (client/shell/src-tauri/src/main.rs)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import importlib.util

        spec = importlib.util.spec_from_file_location("datafluss_sidecar", Path(__file__).resolve().parent.parent / "client" / "sidecar" / "sidecar.py")
        cls.sidecar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.sidecar)

    def fetch(self, state, missing=("premium",), offered=("platform", "schulverwaltung", "premium"), **fetch):
        info = {"version": "9.9.9", "modules": {name: "9.9.9" for name in offered}}
        with mock.patch.dict(os.environ, {"SYNC_TOKEN": "t"}), \
                mock.patch("sync.models.ClientState.load", return_value=state), \
                mock.patch.object(client, "server_info", return_value=info), \
                mock.patch("core.modules.manifest", return_value={"platform": "x", "schulverwaltung": "x"}), \
                mock.patch.object(client, "fetch_module", **fetch) as fetch_module:
            return self.sidecar._fetch_missing(set(missing)), fetch_module

    def test_apps_of_an_unknown_module_fetch_what_the_server_has(self):
        reason, fetch_module = self.fetch(mock.Mock(server_url="https://s", revoked=False), missing=("daz", "teaching"), return_value=True)
        self.assertEqual(reason, "")
        fetch_module.assert_called_once_with("https://s", "premium", token="t")
        self.assertIn("nicht", self.fetch(mock.Mock(server_url="https://s", revoked=False), offered=("platform",), return_value=True)[0])

    def test_fetched_with_the_device_token(self):
        reason, fetch_module = self.fetch(mock.Mock(server_url="https://server.example", revoked=False), return_value=True)
        self.assertEqual(reason, "")
        fetch_module.assert_called_once_with("https://server.example", "premium", token="t")

    def test_a_reason_when_it_cannot_be_fetched(self):
        refused = client.SyncError("Dieses Modul gibt der Server nicht heraus.")
        self.assertEqual(self.fetch(mock.Mock(server_url="https://s", revoked=False), side_effect=refused)[0], str(refused))
        self.assertIn("nicht mehr", self.fetch(None, return_value=True)[0])
        self.assertIn("Ordner", self.fetch(mock.Mock(server_url="https://s", revoked=False), return_value=False)[0])
