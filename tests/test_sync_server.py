"""The server's sync rules, in one process. tests/sync_e2e/ runs real Macs against a real server."""
import uuid

from core import hlc
from core.models import Role, User
from devices.models import Asset, AssetCategory
from people.models import Person
from sync import server
from sync.models import Conflict, Device, FieldVersion, Holding, Journal
from sync.views import hash_token

from .base import AppTestCase


def device_for(user, node="mac-test"):
    return Device.objects.create(node=node, name="Test", user=user, token_hash=hash_token(node))


def held(device, model):
    return set(Holding.objects.filter(device=device, entity=model._meta.label_lower).values_list("record_id", flat=True))


def first_copy(device):
    """Pulls until done and confirms the last page, like a Mac does."""
    page = server.pull(device)
    while not page["done"]:
        page = server.pull(device, page["page"])
    server.pull(device, page["page"])
    device.refresh_from_db()
    return page


class PullTests(AppTestCase):
    def test_a_page_counts_only_when_confirmed(self):
        device = device_for(self.it)
        page = server.pull(device)
        self.assertIn(str(self.pupil.pk), {r["id"] for r in page["records"]})
        self.assertEqual(held(device, Person), set())  # not confirmed yet
        again = server.pull(device)  # no ack: the Mac lost it, so it comes again
        self.assertEqual({r["id"] for r in again["records"]}, {r["id"] for r in page["records"]})
        server.pull(device, again["page"])
        self.assertIn(self.pupil.pk, held(device, Person))

    def test_after_the_first_copy_only_changes_follow(self):
        device = device_for(self.it)
        first_copy(device)
        self.pupil.last_name = "Neu"
        self.pupil.save()
        page = server.pull(device, device.pending["page"])
        self.assertEqual(page["records"], [])
        self.assertEqual([(c["id"], c["k"], c["val"]) for c in page["changes"]], [(str(self.pupil.pk), "last_name", "Neu")])

    def test_a_device_does_not_get_its_own_changes_back(self):
        device = device_for(self.it)
        first_copy(device)
        stamp = hlc.tick()
        server.push(device, "b1", [{"e": "people.person", "id": str(self.pupil.pk), "op": "set", "k": "last_name", "val": "VomMac", "v": stamp,
                                    "base": FieldVersion.objects.get(record_id=self.pupil.pk, key="+").hlc}])
        page = server.pull(device, device.pending["page"])
        self.assertEqual(page["changes"], [])

    def test_a_role_without_the_vault_gets_no_logins_and_no_product_keys(self):
        category = AssetCategory.objects.create(name="Laptop", prefix="L")
        Asset.objects.create(category=category, number="L1", product_key_enc="v1:geheim")
        sl = device_for(self.sl)
        page = server.pull(sl)
        entities = {r["e"] for r in page["records"]}
        self.assertNotIn("vault.credential", entities)
        assets = [r for r in page["records"] if r["e"] == "devices.asset"]
        self.assertEqual(assets[0]["f"]["product_key_enc"], "")


class PushTests(AppTestCase):
    def setUp(self):
        self.device = device_for(self.it)
        first_copy(self.device)
        self.base = FieldVersion.objects.get(record_id=self.pupil.pk, key="+").hlc

    def set_item(self, key, value, base=None, stamp=None):
        return {"e": "people.person", "id": str(self.pupil.pk), "op": "set", "k": key, "val": value,
                "v": stamp or hlc.tick(), "base": self.base if base is None else base}

    def test_a_change_on_an_unchanged_field_applies(self):
        answer = server.push(self.device, "b1", [self.set_item("last_name", "VomMac")])
        self.pupil.refresh_from_db()
        self.assertEqual((answer["accepted"], answer["conflicts"], self.pupil.last_name), (1, 0, "VomMac"))
        self.assertTrue(Journal.objects.filter(record_id=self.pupil.pk, key="last_name", origin="mac-test").exists())

    def test_a_retried_batch_changes_nothing_twice(self):
        server.push(self.device, "b1", [self.set_item("last_name", "Erst")])
        self.pupil.last_name = "Danach"
        self.pupil.save()
        answer = server.push(self.device, "b1", [self.set_item("last_name", "Erst")])
        self.pupil.refresh_from_db()
        self.assertEqual((answer["accepted"], self.pupil.last_name), (1, "Danach"))

    def test_a_newer_edit_elsewhere_wins_and_waits_as_a_conflict(self):
        old = hlc.tick()
        self.pupil.last_name = "AmServer"
        self.pupil.save()  # newer than the Mac's edit below
        answer = server.push(self.device, "b1", [self.set_item("last_name", "AmMac", stamp=old)])
        self.pupil.refresh_from_db()
        self.assertEqual((answer["conflicts"], self.pupil.last_name), (1, "AmServer"))
        conflict = Conflict.objects.get()
        self.assertEqual((conflict.kept_value, conflict.lost_value), ("AmServer", "AmMac"))
        self.device.refresh_from_db()
        self.assertIn(["people.person", str(self.pupil.pk)], self.device.resend)

    def test_the_newer_edit_from_the_mac_wins(self):
        self.pupil.last_name = "AmServer"
        self.pupil.save()
        answer = server.push(self.device, "b1", [self.set_item("last_name", "AmMac")])
        self.pupil.refresh_from_db()
        self.assertEqual((answer["conflicts"], self.pupil.last_name), (1, "AmMac"))
        self.assertEqual(Conflict.objects.get().lost_value, "AmServer")

    def test_the_same_value_on_both_sides_is_no_conflict(self):
        self.pupil.last_name = "Gleich"
        self.pupil.save()
        answer = server.push(self.device, "b1", [self.set_item("last_name", "Gleich")])
        self.assertEqual((answer["conflicts"], Conflict.objects.count()), (0, 0))

    def test_a_role_may_not_change_what_it_may_not_edit(self):
        sl = device_for(self.sl, "mac-sl")
        first_copy(sl)
        answer = server.push(sl, "b1", [self.set_item("last_name", "VonDerLeitung")])
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.last_name, "Muster")
        self.assertEqual(answer["rejected"][0]["reason"], "not-allowed")

    def test_a_new_record_from_the_mac_is_created(self):
        pk = uuid.uuid4()
        stamp = hlc.tick()
        item = {"e": "people.person", "id": str(pk), "op": "upsert", "v": {"+": stamp},
                "f": {"kind": "pupil", "first_name": "Neu", "last_name": "VomMac", "custom": {}}}
        answer = server.push(self.device, "b1", [item])
        self.assertEqual(answer["rejected"], [])
        self.assertEqual(Person.objects.get(pk=pk).last_name, "VomMac")

    def test_a_clash_with_a_unique_value_refuses_only_that_record(self):
        AssetCategory.objects.create(name="Laptop", prefix="L")
        clash = {"e": "devices.assetcategory", "id": str(uuid.uuid4()), "op": "upsert", "v": {"+": hlc.tick()},
                 "f": {"name": "Laptop", "prefix": "X", "next_number": 1}}
        answer = server.push(self.device, "b1", [clash, self.set_item("last_name", "Trotzdem")])
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.last_name, "Trotzdem")
        self.assertEqual(answer["rejected"][0]["reason"], "clash")
        self.assertIn([clash["e"], clash["id"]], answer["evict"])

    def test_a_delete_from_the_mac_deletes(self):
        server.push(self.device, "b1", [{"e": "people.person", "id": str(self.pupil.pk), "op": "delete", "v": hlc.tick()}])
        self.assertFalse(Person.objects.filter(pk=self.pupil.pk).exists())

    def test_secret_fields_of_a_role_without_the_vault_stay_untouched(self):
        sl = User.objects.create_user("sek2", password="x" * 14, role=Role.SCHULLEITUNG)
        device = device_for(sl, "mac-sl2")
        category = AssetCategory.objects.create(name="Tablet", prefix="T")
        asset = Asset.objects.create(category=category, number="T1", product_key_enc="v1:geheim")
        first_copy(device)
        server.push(device, "b1", [{"e": "devices.asset", "id": str(asset.pk), "op": "set", "k": "product_key_enc", "val": "",
                                    "v": hlc.tick(), "base": ""}])
        asset.refresh_from_db()
        self.assertEqual(asset.product_key_enc, "v1:geheim")


class ApiTests(AppTestCase):
    def call(self, name, token=None, data=None, schema=None):
        from django.urls import reverse
        from sync.views import schema as current

        headers = {"HTTP_X_SCHULE_SCHEMA": schema or current()}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        import json

        return self.client.post(reverse(name), json.dumps(data or {}), content_type="application/json", **headers)

    def register(self, password="x" * 14):
        from sync.views import schema

        return self.call("sync_register", data={"username": "it", "password": password, "name": "Test-Mac", "schema": schema()})

    def test_no_token_no_data_even_with_a_session(self):
        self.login(self.it)
        self.assertEqual(self.call("sync_pull").status_code, 401)
        self.assertEqual(self.call("sync_pull", token="erfunden").status_code, 401)

    def test_a_wrong_password_connects_nothing(self):
        self.assertEqual(self.register("falsch-falsch-falsch").status_code, 403)
        self.assertFalse(Device.objects.exists())

    def test_a_connected_mac_pulls_with_its_token(self):
        from core.models import SchoolSettings

        SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
        answer = self.register().json()
        self.assertTrue(answer["vault_key"])
        page = self.call("sync_pull", token=answer["token"]).json()
        self.assertIn(str(self.pupil.pk), {r["id"] for r in page["records"]})
        self.assertEqual(Device.objects.get().token_hash, hash_token(answer["token"]))  # only the hash is kept

    def test_without_its_second_factor_no_mac_connects(self):
        from core.models import SchoolSettings

        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(require_2fa=True)
        self.it.totp_confirmed = False
        self.it.save()
        response = self.register()
        self.assertEqual(response.status_code, 403)
        self.assertIn("Zwei-Faktor", response.json()["error"])

    def test_a_revoked_mac_is_told_so(self):
        from django.utils import timezone

        device = device_for(self.it, "mac-weg")
        Device.objects.filter(pk=device.pk).update(revoked_at=timezone.now())
        response = self.call("sync_pull", token="mac-weg")
        self.assertEqual((response.status_code, response.json().get("revoked")), (401, True))

    def test_a_different_version_does_not_sync(self):
        device_for(self.it, "mac-alt")
        self.assertEqual(self.call("sync_pull", token="mac-alt", schema="0" * 16).status_code, 409)


class ConnectedMacSignInTests(AppTestCase):
    """A Mac connected by one account lets only that account sign in, and says so."""

    def setUp(self):
        super().setUp()
        from unittest import mock

        from core.models import SchoolSettings

        SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
        patcher = mock.patch.dict("os.environ", {"SYNC_TOKEN": "token-des-macs"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_another_account_hears_which_account_may_sign_in(self):
        from django.urls import reverse

        stub = User.objects.create(username="lehrer", role=Role.SEKRETARIAT, is_active=False, password="!")
        response = self.client.post(reverse("login"), {"username": stub.username, "password": "irgendwas-langes"})
        self.assertContains(response, "nur das Konto anmelden, mit dem diese Datenbank verbunden wurde")
        self.assertContains(response, self.it.username)

    def test_a_wrong_password_of_the_owner_is_just_wrong(self):
        from django.urls import reverse

        response = self.client.post(reverse("login"), {"username": self.it.username, "password": "falsch-falsch-falsch"})
        self.assertContains(response, "Benutzername oder Passwort ist falsch.")

    def test_a_password_to_change_does_not_lock_the_mac(self):
        User.objects.filter(pk=self.it.pk).update(must_change_password=True)
        self.login(self.it)
        self.assertEqual(self.client.get("/").status_code, 200)


class RegisterWithNewPasswordTests(AppTestCase):
    register = ApiTests.register
    call = ApiTests.call

    def test_a_password_still_to_change_connects_no_mac(self):
        from core.models import SchoolSettings

        SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
        User.objects.filter(pk=self.it.pk).update(must_change_password=True)
        response = self.register()
        self.assertEqual(response.status_code, 403)
        self.assertIn("Webversion", response.json()["error"])
        self.assertFalse(Device.objects.exists())


class HandshakeTests(ApiTests):
    """DataFluss 0.4: versions and modules in the handshake, and a stop after a restore on the server."""

    def connect(self):
        from core.models import SchoolSettings

        SchoolSettings.objects.filter(pk=1).update(require_2fa=False)
        return self.register().json()

    def test_info_names_version_and_modules(self):
        from django.conf import settings
        from django.urls import reverse

        from core import modules

        info = self.client.get(reverse("sync_info")).json()
        self.assertEqual(info["version"], settings.DATAFLUSS_VERSION)
        self.assertEqual(info["modules"], modules.manifest())

    def test_the_device_reports_its_version_and_modules(self):
        import json

        from django.urls import reverse

        answer = self.connect()
        self.client.post(reverse("sync_pull"), json.dumps({}), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {answer['token']}",
                         HTTP_X_SCHULE_SCHEMA=self._schema(), HTTP_X_DATAFLUSS_VERSION="9.9.9", HTTP_X_DATAFLUSS_MODULES="platform=9.9.9,premium=9.9.9")
        device = Device.objects.get()
        self.assertEqual((device.app_version, device.modules), ("9.9.9", {"platform": "9.9.9", "premium": "9.9.9"}))

    def test_a_different_schema_names_both_versions(self):
        from django.conf import settings
        from django.urls import reverse

        answer = self.connect()
        response = self.client.post(reverse("sync_pull"), "{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {answer['token']}",
                                    HTTP_X_SCHULE_SCHEMA="anders", HTTP_X_DATAFLUSS_VERSION="0.1.0")
        self.assertEqual(response.status_code, 409)
        self.assertIn(f"DataFluss 0.1.0, der Server {settings.DATAFLUSS_VERSION}", response.json()["error"])

    def test_after_a_restore_the_device_stops(self):
        from django.urls import reverse

        from sync.models import Dataset

        answer = self.connect()
        self.assertEqual(answer["dataset"], Dataset.current())
        headers = {"HTTP_AUTHORIZATION": f"Bearer {answer['token']}", "HTTP_X_SCHULE_SCHEMA": self._schema(), "HTTP_X_DATAFLUSS_DATASET": answer["dataset"]}
        page = self.client.post(reverse("sync_pull"), "{}", content_type="application/json", **headers)
        self.assertEqual((page.status_code, page.json()["dataset"]), (200, answer["dataset"]))
        Dataset.renew()
        refused = self.client.post(reverse("sync_pull"), "{}", content_type="application/json", **headers)
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.json()["restored"])

    def _schema(self):
        from sync.views import schema

        return schema()
