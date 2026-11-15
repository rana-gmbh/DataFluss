"""The regular backup of a database in the desktop client (archive/plan.py)."""
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from archive import jobs, nightly, plan, snapshot

from .test_archive import PASSWORD, ArchiveCase

CLIENT = {"SCHULE_CLIENT_DB_NAME": "Meine Schule", "ALLOW_DEMO_DATA": "", "SYNC_TOKEN": ""}


class PlanCase(ArchiveCase):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict("os.environ", CLIENT)
        environment.start()
        self.addCleanup(environment.stop)
        self.target = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.target, ignore_errors=True)

    def own_files(self):
        return sorted(p.name for p in self.target.glob(f"*{snapshot.EXTENSION}"))


class PlanTests(PlanCase):
    def test_password_is_stored_encrypted(self):
        plan.save(self.target, "day", 7, PASSWORD)
        raw = (jobs.root() / "plan.json").read_text()
        self.assertNotIn(PASSWORD, raw)
        self.assertEqual(plan._password(), PASSWORD)
        self.assertNotIn("password", plan.load())

    def test_changing_the_plan_keeps_the_password(self):
        plan.save(self.target, "day", 7, PASSWORD)
        plan.save(self.target, "week", 3)
        self.assertEqual(plan.load()["every"], "week")
        self.assertEqual(plan._password(), PASSWORD)

    def test_refusals(self):
        with self.assertRaisesMessage(ValueError, "Passwort"):
            plan.save(self.target, "day", 7)
        with self.assertRaisesMessage(ValueError, "12"):
            plan.save(self.target, "day", 7, "kurz")
        with self.assertRaisesMessage(ValueError, "nicht erreichbar"):
            plan.save(self.target / "fehlt", "day", 7, PASSWORD)
        with self.assertRaisesMessage(ValueError, "außerhalb der Datenbank"):
            plan.save(self.backups, "day", 7, PASSWORD)
        with self.assertRaises(ValueError):
            plan.save(self.target, "hourly", 7, PASSWORD)
        self.assertFalse(plan.is_set_up())

    def test_run_writes_a_readable_container_and_keeps_only_the_newest(self):
        plan.save(self.target, "day", 3, PASSWORD)
        for minute in range(5):
            with mock.patch("archive.plan.timezone.localtime", return_value=timezone.localtime() + timedelta(minutes=minute)):
                result = plan.run()
            self.assertEqual(result["state"], "ok")
        files = self.own_files()
        self.assertEqual(len(files), 3)
        manifest = snapshot.read_manifest(self.target / files[-1], PASSWORD)
        self.assertEqual(manifest["rows"]["people.person"], 1)
        self.assertEqual(plan.summary()["level"], "ok")

    def test_files_of_others_in_the_folder_stay(self):
        (self.target / "andere-datenbank-x-2020-01-01-000000.svbackup").write_bytes(b"x")
        (self.target / "notizen.txt").write_text("bleibt")
        plan.save(self.target, "day", 3, PASSWORD)
        for minute in range(4):
            with mock.patch("archive.plan.timezone.localtime", return_value=timezone.localtime() + timedelta(minutes=minute)):
                plan.run()
        self.assertTrue((self.target / "andere-datenbank-x-2020-01-01-000000.svbackup").exists())
        self.assertTrue((self.target / "notizen.txt").exists())

    def test_missing_folder_fails_and_waits_an_hour(self):
        plan.save(self.target, "day", 7, PASSWORD)
        shutil.rmtree(self.target)
        result = plan.run()
        self.assertEqual(result["state"], "failed")
        self.assertIn("nicht erreichbar", result["problem"])
        self.assertEqual(plan.summary()["level"], "bad")
        self.assertFalse(plan.is_due())
        self.assertTrue(plan.is_due(timezone.now() + timedelta(hours=1, minutes=1)))

    def test_due_after_the_interval(self):
        plan.save(self.target, "week", 7, PASSWORD)
        self.assertTrue(plan.is_due())
        plan.run()
        self.assertFalse(plan.is_due())
        self.assertFalse(plan.is_due(timezone.now() + timedelta(days=3)))
        self.assertTrue(plan.is_due(timezone.now() + timedelta(days=7)))

    def test_overdue_is_bad(self):
        plan.save(self.target, "day", 7, PASSWORD)
        plan.run()
        last = json.loads((jobs.root() / "plan-last.json").read_text())
        last["ok_at"] = (timezone.now() - timedelta(days=3)).isoformat()
        (jobs.root() / "plan-last.json").write_text(json.dumps(last))
        self.assertEqual(plan.summary()["level"], "bad")

    def test_scheduler_round_backs_up_when_due_and_empties_bins_once_a_day(self):
        plan.save(self.target, "day", 7, PASSWORD)
        scheduler = plan.Scheduler()
        with mock.patch("archive.nightly.empty_bins") as bins:
            scheduler.tick()
            scheduler.tick()
        self.assertEqual(bins.call_count, 1)
        self.assertEqual(len(self.own_files()), 1)

    def test_only_a_database_of_its_own(self):
        self.assertTrue(plan.applies())
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": "t"}):
            self.assertFalse(plan.applies())
            self.assertIn("Kopie vom Server", nightly.summary()["text"])
        with mock.patch.dict("os.environ", {"ALLOW_DEMO_DATA": "1"}):
            self.assertFalse(plan.applies())
        self.assertIn("keine regelmäßige Sicherung", nightly.summary()["text"])


class PlanPageTests(PlanCase):
    def test_set_up_run_and_turn_off_on_the_page(self):
        client = self.login(self.it)
        page = client.get(reverse("backups"))
        self.assertContains(page, "Regelmäßige Sicherung")
        self.assertContains(page, "Auf einen Server übertragen")
        answer = client.post(reverse("backup_plan"), {"folder": str(self.target), "every": "day", "keep": "7", "password": PASSWORD, "repeat": "anders-als-vorher"})
        self.assertEqual(answer.status_code, 400)
        answer = client.post(reverse("backup_plan"), {"folder": str(self.target), "every": "day", "keep": "7", "password": PASSWORD, "repeat": PASSWORD})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(client.post(reverse("backup_plan_run")).status_code, 200)
        self.assertEqual(len(self.own_files()), 1)
        self.assertContains(client.get(reverse("backups")), "Jetzt sichern")
        client.post(reverse("backup_plan_off"))
        self.assertFalse(plan.is_set_up())
        self.assertEqual(len(self.own_files()), 1)

    def test_secretary_may_not(self):
        client = self.login(self.sek)
        self.assertEqual(client.post(reverse("backup_plan"), {"folder": str(self.target)}).status_code, 403)

    def test_server_page_has_no_plan(self):
        client = self.login(self.it)
        with mock.patch.dict("os.environ", {"SCHULE_CLIENT_DB_NAME": ""}):
            page = client.get(reverse("backups"))
            self.assertNotContains(page, "Regelmäßige Sicherung")
            self.assertEqual(client.post(reverse("backup_plan"), {"folder": str(self.target)}).status_code, 400)

    def test_connected_copy_cannot_restore_or_create(self):
        client = self.login(self.it)
        with mock.patch.dict("os.environ", {"SYNC_TOKEN": "t"}):
            page = client.get(reverse("backups"))
        self.assertContains(page, "Vollständige Sicherung vom Server")
        self.assertNotContains(page, "Neue Sicherung erstellen")
        self.assertNotContains(page, "data-restore")

    def test_dashboard_checklist_names_the_plan(self):
        client = self.login(self.it)
        self.assertContains(client.get(reverse("dashboard")), "Regelmäßige Sicherung einrichten")
