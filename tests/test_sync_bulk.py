"""Bulk updates and deletes skip save(), so they go through the journal helpers. These prove it end to end."""
import json
from datetime import date, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from devices.models import Asset, Assignment
from masks.models import Mask, MaskVersion
from people.models import Card, ClassGroup, Enrollment, Guardian, Guardianship, Person, SchoolYear
from sync.models import CREATED, DELETED, Journal
from transfer.models import ImportSession
from vault.models import Credential

from .base import AppTestCase
from .test_transfer import NINOX


def keys(record_id):
    return list(Journal.objects.filter(record_id=record_id).order_by("id").values_list("key", flat=True))


class BulkJournalTests(AppTestCase):
    def test_class_change_closes_the_enrollment_in_the_journal(self):
        self.login(self.it)
        person = Person.objects.create(kind="pupil", first_name="Tom", last_name="Test")
        person.move_to_class(self.group)
        enrollment = Enrollment.objects.get(person=person)
        response = self.client.post(reverse("bulk_action"), {"ids": [str(person.pk)], "action": "class", "value": str(self.top.pk)}, content_type="application/json")
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(keys(enrollment.pk), [CREATED, "end"])

    def test_rollover_journals_the_old_current_year(self):
        self.login(self.it)
        with mock.patch("core.updates.backup_database", return_value="dump"):
            self.client.post(reverse("rollover"), {"name": "2027/28", "confirm": "1"})
        self.assertEqual(SchoolYear.current().name, "2027/28")
        self.assertEqual(keys(self.year.pk)[-1], "is_current")

    def test_revoked_card_is_journaled(self):
        self.login(self.sek)
        self.client.post(reverse("person_card", args=[self.pupil.pk]), {"raw": "ff"})
        first = Card.objects.get(person=self.pupil)
        self.client.post(reverse("person_card", args=[self.pupil.pk]), {"raw": "fe"})
        self.assertEqual(keys(first.pk), [CREATED, "active"])
        second = Card.objects.get(person=self.pupil, active=True)
        self.client.post(reverse("person_card", args=[self.pupil.pk]), {"action": "revoke"})
        self.assertEqual(keys(second.pk), [CREATED, "active"])

    def test_unlinked_guardian_leaves_a_tombstone(self):
        self.login(self.sek)
        link = Guardianship.objects.create(person=self.pupil, guardian=Guardian.objects.create(first_name="Eva", last_name="Muster"))
        self.client.post(reverse("person_guardian", args=[self.pupil.pk]), {"action": "unlink", "link": str(link.pk)})
        self.assertFalse(Guardianship.objects.filter(pk=link.pk).exists())
        self.assertEqual(keys(link.pk)[-1], DELETED)

    def test_publishing_a_mask_journals_the_retired_version_and_the_dropped_draft(self):
        self.login(self.it)
        mask = Mask.objects.get(entity="pupil", is_default=True)
        published = mask.published
        tree = {"tabs": [{"id": "t", "title": "Neu", "sections": []}]}
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": tree}), content_type="application/json")
        draft = MaskVersion.objects.get(mask=mask, status=MaskVersion.DRAFT)
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": tree, "publish": True}), content_type="application/json")
        self.assertEqual(keys(published.pk)[-1], "status")
        self.assertEqual(keys(draft.pk)[-1], DELETED)
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": tree}), content_type="application/json")
        draft = MaskVersion.objects.get(mask=mask, status=MaskVersion.DRAFT)
        self.client.post(reverse("mask_action", args=[mask.pk]), {"action": "discard"})
        self.assertEqual(keys(draft.pk)[-1], DELETED)

    def test_import_undo_leaves_tombstones(self):
        self.login(self.sek)
        self.client.post(reverse("transfer_upload"), {"entity": "pupil", "file": SimpleUploadedFile("ninox.csv", NINOX)})
        session = ImportSession.objects.get()
        post = {f"map_{i}": session.mapping.get(h, "") for i, h in enumerate(session.headers)}
        post["match_key"] = "legacy_ninox_id"
        self.client.post(reverse("transfer_mapping", args=[session.pk]), post)
        self.client.post(reverse("transfer_commit", args=[session.pk]))
        ben = Person.objects.get(legacy_ninox_id="B2105")
        created = {
            "asset": Asset.objects.get(number="SuS1149").pk,
            "assignment": Assignment.objects.get(person=ben).pk,
            "credential": Credential.objects.get(person=ben).pk,
            "class": ClassGroup.objects.get(name="10b").pk,
            "person": ben.pk,
        }
        for pk in created.values():
            self.assertEqual(keys(pk)[0], CREATED)
        self.client.post(reverse("transfer_undo", args=[session.pk]))
        self.assertFalse(Asset.objects.filter(pk=created["asset"]).exists())
        for name, pk in created.items():
            self.assertEqual(keys(pk)[-1], DELETED, name)

    def test_stale_import_session_leaves_a_tombstone(self):
        self.login(self.sek)
        self.client.post(reverse("transfer_upload"), {"entity": "pupil", "file": SimpleUploadedFile("ninox.csv", NINOX)})
        session = ImportSession.objects.get()
        ImportSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.client.get(reverse("transfer"))
        self.assertFalse(ImportSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(keys(session.pk)[-1], DELETED)

    def test_an_unchanged_row_is_not_journaled(self):
        """journal.update only records values that really changed."""
        other = SchoolYear.objects.create(name="2025/26", start=date(2025, 8, 1), end=date(2026, 7, 31))
        before = keys(other.pk)
        with mock.patch("core.updates.backup_database", return_value="dump"):
            self.login(self.it)
            self.client.post(reverse("rollover"), {"name": "2027/28", "confirm": "1"})
        self.assertEqual(keys(other.pk), before)
