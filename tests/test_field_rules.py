"""Field rules of roles (core/fieldrules.py): a hidden field is gone everywhere, a read-only one is never
written, names and titles cannot be hidden."""
import json

from django.urls import reverse

from core.context_user import set_current_user
from core.models import Role, User, UserRole
from masks import services as ms
from masks.views import create_field
from tables import formula, services
from tables.models import Record, Table

from .base import AppTestCase


class PersonFieldTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        self.health = create_field("pupil", "Gesundheit", "text", in_grid=True)
        self.note = create_field("pupil", "Notiz", "text", in_grid=True)
        from masks.models import Mask

        version = Mask.objects.get(entity="pupil", table=None, is_default=True).published
        version.tree["tabs"][0]["sections"][0]["items"] += [{"type": "field", "field": self.health.key, "w": 6}, {"type": "field", "field": self.note.key, "w": 6}]
        version.save()
        ms.set_value(self.pupil, self.health, "Asthma-Spray")
        ms.set_value(self.pupil, self.note, "nett")
        role = UserRole.objects.get(key=Role.SEKRETARIAT)
        role.fields = {"pupil": {self.health.key: "hidden", self.note.key: "read", "school_type": "hidden"}}
        role.save()
        self.office = User.objects.get(pk=self.sek.pk)
        set_current_user(None)
        self.login(self.office)

    def test_hidden_everywhere_on_screen(self):
        page = self.client.get(reverse("person_detail", args=[self.pupil.pk])).content.decode()
        self.assertNotIn("Asthma-Spray", page)
        self.assertNotIn("Gesundheit", page)
        self.assertIn("nett", page)
        rows = self.client.get(reverse("api_persons"), {"entity": "pupil"}).json()
        self.assertNotIn(self.health.key, rows[0])
        self.assertNotIn("school_type", rows[0])
        columns = self.client.get(reverse("pupils")).context["config"]["columns"]
        self.assertEqual({c["key"]: c["editable"] for c in columns if c["key"] in (self.note.key, "first_name")}, {self.note.key: False, "first_name": True})
        self.assertNotIn(self.health.key, [c["key"] for c in columns])
        history = self.client.get(reverse("person_block", args=[self.pupil.pk, "history"])).content.decode()
        self.assertNotIn("Asthma", history)
        self.assertIn("nett", history)

    def test_hidden_in_export_print_and_import(self):
        from transfer import importer

        export = {"entity": "pupil", "format": "csv", "columns": [{"key": "first_name"}, {"key": self.health.key}], "ids": [str(self.pupil.pk)]}
        content = self.client.post(reverse("export_rows"), {"payload": json.dumps(export)}).content.decode("utf-8-sig")
        self.assertNotIn("Asthma", content)
        self.assertIn("Lena", content)
        set_current_user(self.office)
        from prints.records import person_record

        record = person_record(self.pupil)
        self.assertNotIn(self.health.key, record["fields"])
        targets = dict(importer.targets("pupil", self.office))
        self.assertNotIn(self.health.key, targets)
        self.assertNotIn(self.note.key, targets)  # read-only: not imported either
        self.assertIn("first_name", targets)

    def test_read_only_and_hidden_are_never_written(self):
        for key in (self.note.key, self.health.key):
            answer = self.client.post(reverse("api_person_field", args=[self.pupil.pk, key]), json.dumps({"value": "x"}), content_type="application/json")
            self.assertEqual(answer.status_code, 403, key)
        self.assertEqual(self.client.post(reverse("person_field", args=[self.pupil.pk, self.note.key]), {"value": "x"}).status_code, 403)
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.custom[self.note.key], "nett")

    def test_search_does_not_find_by_a_hidden_email(self):
        role = UserRole.objects.get(key=Role.SEKRETARIAT)
        role.fields = {"pupil": {"email": "hidden"}}
        role.save()
        self.login(User.objects.get(pk=self.sek.pk))
        self.assertNotIn("Lena", self.client.get(reverse("global_search"), {"q": "lena@schule"}).content.decode())
        self.assertIn("Lena", self.client.get(reverse("global_search"), {"q": "Lena"}).content.decode())


class TableFieldTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        self.table = services.create_table("Bücher")
        self.price = services.create_field(self.table, "Preis", "number", config={"decimals": 2})
        self.secret_note = services.create_field(self.table, "Intern", "text")
        self.double = services.create_field(self.table, "Doppelt", "formula", config=services.formula_config(self.table, {"expression": "{Preis} * 2"}))
        services.set_rights(self.table, {Role.SEKRETARIAT: "edit"})
        self.momo = services.create_record(self.table, {"f_name": "Momo", "f_preis": "10", "f_intern": "Lieblingsbuch der Chefin"})
        role = UserRole.objects.get(key=Role.SEKRETARIAT)
        role.fields = {"table:buecher": {"f_preis": "hidden", "f_intern": "hidden"}}
        role.save()
        self.office = User.objects.get(pk=self.sek.pk)
        set_current_user(None)
        self.login(self.office)

    def test_hidden_in_list_page_formula_search_and_history(self):
        rows = self.client.get(reverse("table_rows", args=["buecher"])).json()
        self.assertNotIn("f_preis", rows[0])
        self.assertEqual(rows[0]["f_doppelt"], "nicht sichtbar")  # a formula over a hidden field
        page = self.client.get(reverse("record_detail", args=["buecher", self.momo.pk])).content.decode()
        self.assertNotIn("Lieblingsbuch", page)
        self.assertEqual(formula.display(self.momo, self.double, formula.Context(self.office))[0], "nicht sichtbar")
        self.assertNotIn("Momo", self.client.get(reverse("global_search"), {"q": "chefin"}).content.decode())
        self.assertIn("Momo", self.client.get(reverse("global_search"), {"q": "momo"}).content.decode())
        history = self.client.get(reverse("record_block", args=["buecher", self.momo.pk, "history"])).content.decode()
        self.assertNotIn("Lieblingsbuch", history)
        answer = self.client.post(reverse("table_api_field", args=["buecher", self.momo.pk, "f_preis"]), json.dumps({"value": 3}), content_type="application/json")
        self.assertEqual(answer.status_code, 403)

    def test_the_title_field_cannot_be_hidden(self):
        from core import fieldrules

        self.assertEqual(fieldrules.clean("table:buecher", {"f_name": "hidden", "f_preis": "hidden"}), {"f_name": "read", "f_preis": "hidden"})
        self.assertEqual(fieldrules.clean("pupil", {"last_name": "hidden"}), {"last_name": "read"})


class EditorTests(AppTestCase):
    def test_editor_and_field_page_set_the_same_rules(self):
        set_current_user(self.it)
        hobby = create_field("pupil", "Hobby", "text")
        set_current_user(None)
        self.login(self.it)
        page = self.client.get(reverse("role_edit", args=[Role.SCHULLEITUNG]))
        self.assertContains(page, f"field__pupil__{hobby.key}")
        post = {"name": "Schulleitung", "area_home": "read", "area_pupils": "read", f"field__pupil__{hobby.key}": "hidden", "field__pupil__last_name": "hidden"}
        self.client.post(reverse("role_edit", args=[Role.SCHULLEITUNG]), post)
        rules = UserRole.objects.get(key=Role.SCHULLEITUNG).fields["pupil"]
        self.assertEqual((rules[hobby.key], rules["last_name"]), ("hidden", "read"))  # names cannot be hidden
        self.assertContains(self.client.get(reverse("field_edit", args=[hobby.pk])), f"rule_{Role.SCHULLEITUNG}")
        self.client.post(reverse("field_edit", args=[hobby.pk]), {"action": "rights", f"rule_{Role.SCHULLEITUNG}": "read"})
        self.assertEqual(UserRole.objects.get(key=Role.SCHULLEITUNG).fields["pupil"][hobby.key], "read")
        self.assertEqual(UserRole.objects.get(key=Role.SCHULLEITUNG).fields["pupil"]["last_name"], "read")


class NewPersonTests(AppTestCase):
    def test_read_only_fields_are_not_offered_nor_taken(self):
        role = UserRole.objects.get(key=Role.SEKRETARIAT)
        role.fields = {"pupil": {"birth_date": "read", "gender": "hidden"}}
        role.save()
        self.login(User.objects.get(pk=self.sek.pk))
        page = self.client.get(reverse("person_new", args=["pupil"])).content.decode()
        self.assertNotIn('id="birth_date"', page)
        self.assertNotIn('id="gender"', page)
        self.assertIn('id="entry_date"', page)
        self.client.post(reverse("person_new", args=["pupil"]), {"first_name": "Neu", "last_name": "Kind", "birth_date": "2015-01-01", "gender": "w"})
        from people.models import Person

        made = Person.objects.get(first_name="Neu")
        self.assertEqual((made.birth_date, made.gender), (None, ""))
