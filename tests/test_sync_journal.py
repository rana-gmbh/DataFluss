"""The journal is what sync sends, so every change of a tracked record must land in it."""
from datetime import date

from django.test import TestCase

from people.models import ClassGroup, Person, SchoolYear
from sync import journal
from sync.models import CREATED, DELETED, FieldVersion, Journal


def keys(record):
    return list(Journal.objects.filter(record_id=record.pk).order_by("id").values_list("key", flat=True))


class JournalTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="2026/27", start=date(2026, 8, 1), end=date(2027, 7, 31))
        self.group = ClassGroup.objects.create(name="5a", level=5, school_year=self.year)
        self.person = Person.objects.create(kind="pupil", first_name="Lena", last_name="Muster", custom={"telefon": "1", "hobby": "Lesen"})

    def test_a_new_record_is_one_entry(self):
        self.assertEqual(keys(self.person), [CREATED])
        self.assertTrue(FieldVersion.objects.filter(record_id=self.person.pk, key=CREATED).exists())

    def test_only_changed_fields_are_journaled_with_their_base(self):
        created = FieldVersion.objects.get(record_id=self.person.pk, key=CREATED).hlc
        self.person.last_name = "Neu"
        self.person.first_name = "Lena"  # unchanged
        self.person.save()
        entry = Journal.objects.filter(record_id=self.person.pk).latest("id")
        self.assertEqual(keys(self.person), [CREATED, "last_name"])
        self.assertEqual(entry.base, created)
        self.assertGreater(entry.hlc, created)

    def test_a_date_given_as_text_is_no_change(self):
        self.person.birth_date = date(2015, 3, 1)
        self.person.save()
        self.person.birth_date = "2015-03-01"
        self.person.save()
        self.assertEqual(keys(self.person), [CREATED, "birth_date"])

    def test_dictionary_keys_are_journaled_one_by_one(self):
        self.person.custom = {"telefon": "2", "hobby": "Lesen", "neu": "x"}
        self.person.save()
        self.assertEqual(keys(self.person)[1:], ["custom.neu", "custom.telefon"])
        self.person.custom = {"telefon": "2", "neu": "x"}
        self.person.save()
        self.assertEqual(keys(self.person)[-1], "custom.hobby")

    def test_the_base_is_the_version_of_that_key(self):
        self.person.custom = {**self.person.custom, "telefon": "2"}
        self.person.save()
        first = FieldVersion.objects.get(record_id=self.person.pk, key="custom.telefon").hlc
        self.person.custom = {**self.person.custom, "telefon": "3"}
        self.person.save()
        self.assertEqual(Journal.objects.filter(record_id=self.person.pk, key="custom.telefon").latest("id").base, first)

    def test_a_deleted_record_leaves_a_tombstone_only(self):
        pk = self.person.pk
        self.person.delete()
        self.assertEqual(Journal.objects.filter(record_id=pk).latest("id").key, DELETED)
        self.assertEqual(list(FieldVersion.objects.filter(record_id=pk).values_list("key", flat=True)), [DELETED])

    def test_bulk_update_journals_the_rows_that_changed(self):
        other = Person.objects.create(kind="pupil", first_name="Max", last_name="Muster", current_class=self.group)
        journal.update(Person.objects.filter(pk__in=[self.person.pk, other.pk]), current_class=self.group)
        self.assertEqual(keys(self.person)[-1], "current_class")
        self.assertEqual(keys(other), [CREATED])  # already in that class

    def test_bulk_update_of_a_dictionary_journals_its_keys(self):
        journal.update(Person.objects.filter(pk=self.person.pk), custom={"telefon": "1"})
        self.assertEqual(keys(self.person)[-1:], ["custom.hobby"])

    def test_bulk_create_and_delete(self):
        made = journal.bulk_create(Person, [Person(kind="pupil", first_name="A", last_name="B"), Person(kind="pupil", first_name="C", last_name="D")])
        self.assertEqual([keys(p) for p in made], [[CREATED], [CREATED]])
        pks = [p.pk for p in made]
        journal.delete(Person.objects.filter(pk__in=pks))
        self.assertEqual([Journal.objects.filter(record_id=pk).latest("id").key for pk in pks], [DELETED, DELETED])
