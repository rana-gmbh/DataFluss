"""Editable roles (core.models.UserRole, core/rights.py): built-in roles keep their rights, new roles get
exactly what their areas and switches say, IT always has everything."""
from core import rights
from core.models import Role, User, UserRole, role_choices
from core.permissions import CAPABILITIES, default_capabilities, ensure_roles, has_capability

from .base import AppTestCase


class SeedTests(AppTestCase):
    def test_built_in_roles_have_rows_with_their_old_rights(self):
        ensure_roles()
        for role in (Role.SEKRETARIAT, Role.SCHULLEITUNG):
            with self.subTest(role=role):
                row = UserRole.objects.get(key=role)
                self.assertEqual(row.pk, UserRole.id_for(role))
                self.assertEqual(rights.capabilities(row.areas, row.extras), default_capabilities(role))
                user = User(username=f"x-{role}", role=role)
                for capability in CAPABILITIES:
                    self.assertEqual(has_capability(user, capability), role in CAPABILITIES[capability], capability)

    def test_every_capability_can_be_granted(self):
        everything = rights.capabilities({a.key: rights.EDIT for a in rights.areas()}, [e.key for a in rights.areas() for e in a.extras])
        self.assertEqual(set(CAPABILITIES) - everything, set())

    def test_it_has_everything_and_no_row(self):
        self.assertFalse(UserRole.objects.filter(key=Role.IT).exists())
        self.assertTrue(all(has_capability(self.it, c) for c in CAPABILITIES))
        self.assertEqual(role_choices()[0][0], Role.IT)


class CustomRoleTests(AppTestCase):
    def test_a_new_role_gets_what_its_areas_say(self):
        UserRole.objects.create(key="hausmeister", name="Hausmeister", areas={"devices": rights.EDIT, "people": rights.READ, "home": rights.READ},
                                extras=["people.archive", "print.design"])
        janitor = User.objects.create_user("hm", password="x" * 14, role="hausmeister")
        self.assertTrue(janitor.can("device.edit") and janitor.can("person.view") and janitor.can("person.archive"))
        self.assertFalse(janitor.can("person.edit") or janitor.can("vault.view"))
        self.assertFalse(janitor.can("print.design"))  # a switch of an area the role cannot open
        self.assertIn(("hausmeister", "Hausmeister"), role_choices())

    def test_changing_a_built_in_role_changes_its_accounts(self):
        row = UserRole.objects.get(key=Role.SCHULLEITUNG)
        row.areas = {**row.areas, "people": rights.NONE}
        row.save()
        self.assertFalse(User.objects.get(pk=self.sl.pk).can("person.view"))
        self.assertTrue(User.objects.get(pk=self.sek.pk).can("person.view"))
