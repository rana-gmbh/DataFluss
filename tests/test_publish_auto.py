"""Publishing by itself (publish/auto.py), confirming when the role sees more, and the server's publications
for a connected computer (publish/api.py, publish/remote.py)."""
import json
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.context_user import set_current_user
from core.models import Role, UserRole
from publish import auto, service
from publish.models import Publication
from sync.models import Device, Journal
from sync.views import hash_token, schema
from tables import services
from tables.models import Record

from .test_publish import Synchronous
from .test_publish_safety import PRIVATE, PublishCase


def later(minutes):
    return timezone.now() + timedelta(minutes=minutes)


class AutoCase(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        Publication.objects.filter(pk=self.publication.pk).update(target=Publication.FOLDER, target_config={"path": str(self.folder)},
                                                                   auto=Publication.CHANGES, auto_quiet=15)
        service.start(self.confirmed(), service.PUBLISH)  # the first publish is by hand
        self.assertEqual(self.pub().status, Publication.DONE, self.pub().error)

    def pub(self):
        return Publication.objects.get(pk=self.publication.pk)

    def page(self, record=None):
        return (self.folder / f"filme/{(record or self.momo).pk}.html").read_text()

    def change(self, record, field, value):
        services.set_value(Record.objects.get(pk=record.pk), field, value)
        set_current_user(None)

    def role_rules(self, rules):
        row = UserRole.objects.get(key=Role.SCHULLEITUNG)
        row.fields = {"table:filme": rules}
        row.save()
        set_current_user(None)


class ChangeTests(AutoCase):
    def test_a_change_publishes_after_the_quiet_time(self):
        self.assertEqual(auto.tick(later(60)), 0)  # nothing changed since
        self.change(self.momo, self.year, "1987")
        self.assertEqual(auto.tick(later(5)), 0)  # still within the quiet time
        self.assertNotIn("1987", self.page())
        self.assertEqual(auto.tick(later(16)), 1)
        self.assertIn("1987", self.page())
        self.assertEqual(self.pub().auto_marker, auto.marker(self.pub())[0])
        self.assertEqual(auto.tick(later(60)), 0)  # done, nothing new

    def test_only_changes_of_chosen_tables_count(self):
        before = auto.marker(self.pub())[0]
        self.change(Record.objects.filter(table=self.files_table).first(), self.file_note, "anders")
        self.assertEqual(auto.marker(self.pub())[0], before)
        self.change(self.story, self.year, "1984")
        self.assertGreater(auto.marker(self.pub())[0], before)

    def test_changes_from_another_computer_count(self):
        """A change applied by sync writes the journal, not the record's save (sync/apply.py)."""
        before = auto.marker(self.pub())[0]
        Journal.objects.create(entity="tables.record", record_id=self.momo.pk, key="values.f_jahr", hlc="x", origin="mac")
        self.assertGreater(auto.marker(self.pub())[0], before)

    def test_never_before_the_first_publish_by_hand_nor_to_a_zip(self):
        Publication.objects.filter(pk=self.publication.pk).update(published_at=None)
        self.change(self.momo, self.year, "1987")
        self.assertEqual(auto.tick(later(60)), 0)
        Publication.objects.filter(pk=self.publication.pk).update(published_at=timezone.now(), target=Publication.ZIP)
        self.assertEqual(auto.tick(later(60)), 0)

    def test_a_failure_waits_half_an_hour(self):
        Publication.objects.filter(pk=self.publication.pk).update(target_config={"path": "/gibt/es/nicht"})
        self.change(self.momo, self.year, "1987")
        auto.tick(later(20))
        publication = self.pub()
        self.assertEqual(publication.status, Publication.FAILED)
        self.assertIsNotNone(publication.auto_retry_at)
        with mock.patch("publish.service.run") as run:
            auto.tick(later(25))
            run.assert_not_called()
            auto.tick(later(60))
            run.assert_called_once()

    def test_the_command(self):
        self.change(self.momo, self.year, "1987")
        Journal.objects.update(at=timezone.now() - timedelta(hours=1))
        call_command("publish_auto")
        self.assertIn("1987", self.page())


class DailyTests(AutoCase):
    def test_once_a_day_at_its_time(self):
        Publication.objects.filter(pk=self.publication.pk).update(auto=Publication.DAILY, auto_time=time(3, 0), auto_day=None)
        today = timezone.localdate()
        early = timezone.make_aware(datetime.combine(today, time(2, 0)))
        late = timezone.make_aware(datetime.combine(today, time(3, 30)))
        self.assertFalse(auto.due(self.pub(), early)[0])
        self.assertTrue(auto.due(self.pub(), late)[0])
        self.assertEqual(auto.tick(late), 1)
        self.assertEqual(self.pub().auto_day, today)
        self.assertFalse(auto.due(self.pub(), late + timedelta(hours=2))[0])


class ConfirmTests(AutoCase):
    def test_the_role_seeing_more_waits_for_a_confirmation(self):
        self.role_rules({})  # Intern is no longer hidden for the role, and it is chosen
        self.change(self.momo, self.year, "1987")
        self.assertEqual(auto.tick(later(60)), 0)
        self.assertIn("Wartet auf Bestätigung", self.pub().auto_note)
        self.assertNotIn("1987", self.page())
        self.assertTrue(service.needs_confirmation(self.pub()))
        self.assertEqual(service.grown(self.pub()), [("filme", "f_intern")])

        # By hand it asks too, and the page marks what is new.
        self.login(self.it)
        response = self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish"})
        self.assertRedirects(response, reverse("publication_confirm", args=[self.publication.pk]))
        page = self.client.get(reverse("publication_confirm", args=[self.publication.pk])).content.decode()
        self.assertIn("Intern · neu", page)

        # A list that changed after the page was shown is not confirmed unseen.
        stale = self.seen()
        self.role_rules({"f_jahr": "hidden"})
        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": stale})
        self.assertTrue(service.needs_confirmation(self.pub()))

        self.client.post(reverse("publication_confirm", args=[self.publication.pk]), {"kind": "publish", "confirm": "1", "seen": self.seen()})
        self.assertIn(PRIVATE["role_hidden"], self.page())
        self.assertEqual(self.pub().auto_note, "")

    def test_the_role_seeing_less_publishes_without_asking(self):
        self.role_rules({"f_intern": "hidden", "f_jahr": "hidden"})
        self.change(self.momo, self.note, "egal")  # any change of the table
        self.assertEqual(auto.tick(later(60)), 1)
        self.assertNotIn("1986", self.page())
        self.assertNotIn("f_jahr", self.pub().published_choice["fields"]["filme"])
        # Seeing it again is seeing more than confirmed.
        self.role_rules({"f_intern": "hidden"})
        self.assertTrue(service.needs_confirmation(self.pub()))

    def test_a_publish_never_shows_more_than_was_confirmed(self):
        """Even when the role came to see more while a publish was already on its way."""
        self.role_rules({})
        Publication.objects.filter(pk=self.publication.pk).update(busy_until=None)
        service.run(self.publication.pk, service.PUBLISH)  # past the check in start()
        self.assertNotIn(PRIVATE["role_hidden"], "\n".join(p.read_text() for p in self.folder.rglob("*.html")))

    def test_start_refuses_what_needs_a_confirmation(self):
        from django.core.exceptions import ValidationError

        Publication.objects.filter(pk=self.publication.pk).update(role=Role.SEKRETARIAT)
        with self.assertRaisesMessage(ValidationError, "bestätigen"):
            service.start(self.pub(), service.PUBLISH)


class ServerApiTests(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        self.publication.target = Publication.SERVER
        self.publication.save()
        self.device = Device.objects.create(node="mac-1", name="Mac", user=self.it, token_hash=hash_token("geheim-token"))

    def call(self, name, body=None, token="geheim-token"):
        return self.client.post(reverse(name), json.dumps(body or {}), content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_SCHULE_SCHEMA=schema())

    def test_list_and_publish_from_a_connected_computer(self):
        rows = self.call("sync_publish_list").json()
        self.assertTrue(rows["allowed"])
        self.assertEqual([r["name"] for r in rows["publications"]], ["Filmabend"])
        self.assertTrue(rows["publications"][0]["needs_confirmation"])
        refused = self.call("sync_publish_run", {"id": str(self.publication.pk)})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("/einstellungen/veroeffentlichen/", refused.json()["confirm_url"])

        self.confirmed()
        answer = self.call("sync_publish_run", {"id": str(self.publication.pk)}).json()["publication"]
        self.assertEqual(answer["status"], Publication.DONE)
        self.assertTrue(answer["url"].endswith("/p/filmabend/"))
        self.assertIsNotNone(self.publication.__class__.objects.get().published_at)

    def test_only_with_the_switch_and_a_valid_token(self):
        self.device.user = self.sl
        self.device.save()
        self.assertFalse(self.call("sync_publish_list").json()["allowed"])
        self.assertEqual(self.call("sync_publish_run", {"id": str(self.publication.pk)}).status_code, 403)
        self.assertEqual(self.call("sync_publish_list", token="falsch").status_code, 401)
        self.assertEqual(self.client.post(reverse("sync_publish_list")).status_code, 401)


class ComputerTests(Synchronous, PublishCase):
    """The page on a connected computer, with the server's answers made up."""

    def setUp(self):
        super().setUp()
        self.login(self.it)
        patcher = mock.patch("publish.remote.available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rows = [{"id": str(self.publication.pk), "name": "Auf dem Server", "target": "server", "target_label": "Dieser Server",
                      "status": "done", "progress": "", "error": "", "published_at": "2026-11-25T10:00:00", "url": "https://s.example/p/x/",
                      "manage_url": "https://s.example/einstellungen/veroeffentlichen/x/", "needs_confirmation": False, "auto": "Nach Änderungen",
                      "auto_note": ""}]

    def fake(self, path, body=None):
        if path == "list":
            return {"allowed": True, "publications": self.rows}
        return {"publication": self.rows[0]}

    def test_the_servers_publications_and_publishing_again(self):
        from sync import client

        worker = mock.Mock()
        worker.now.return_value = {"state": "ok"}
        with mock.patch("publish.remote._call", side_effect=self.fake) as call, mock.patch("publish.remote.publications",
                                                                                            return_value=("https://s.example", True, self.rows)), \
                mock.patch.object(client, "WORKER", worker):
            page = self.client.get(reverse("publication_remote")).content.decode()
            self.assertIn("Auf dem Server", page)
            self.assertIn("https://s.example/p/x/", page)
            self.client.post(reverse("publication_remote_run", args=[self.publication.pk]))
            worker.now.assert_called_once()  # this computer's changes go first
            call.assert_called_with("run", {"id": str(self.publication.pk)})
        self.assertIn('hx-get="/einstellungen/veroeffentlichen/server/"', self.client.get(reverse("publication_list")).content.decode())

    def test_an_unreachable_server_is_said(self):
        from sync.client import SyncError

        with mock.patch("publish.remote.publications", side_effect=SyncError("Der Server ist nicht erreichbar (x).")):
            page = self.client.get(reverse("publication_remote")).content.decode()
        self.assertIn("nicht erreichbar", page)
