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


class RolePageTests(AppTestCase):
    def setUp(self):
        from django.urls import reverse

        self.url = reverse
        self.login(self.it)

    def test_new_role_from_a_preset_or_a_copy(self):
        self.client.post(self.url("role_new"), {"name": "Hausmeister", "start": "janitor"})
        janitor = UserRole.objects.get(key="hausmeister")
        self.assertEqual((janitor.areas["devices"], janitor.areas["people"]), ("edit", "read"))
        self.client.post(self.url("role_new"), {"name": "Büro 2", "start": "copy:sekretariat"})
        office = UserRole.objects.get(name="Büro 2")
        self.assertEqual(office.key, "buero_2")
        self.assertEqual(rights.capabilities(office.areas, office.extras), default_capabilities(Role.SEKRETARIAT))

    def test_editor_saves_areas_switches_and_tables(self):
        from tables import services
        from core.context_user import set_current_user

        set_current_user(self.it)
        books = services.create_table("Bücher")
        self.client.post(self.url("role_new"), {"name": "Bibliothek"})
        post = {"name": "Bibliothek", "description": "Ausleihe", "area_home": "read", "area_print": "read", "extra": ["print.design"],
                "table_buecher": "edit"}
        self.assertEqual(self.client.get(self.url("role_edit", args=["bibliothek"])).status_code, 200)
        self.client.post(self.url("role_edit", args=["bibliothek"]), post)
        role = UserRole.objects.get(key="bibliothek")
        self.assertEqual((role.areas["print"], role.extras, role.description), ("read", ["print.design"], "Ausleihe"))
        books.refresh_from_db()
        self.assertEqual(books.rights["bibliothek"], "edit")
        preview = self.client.post(self.url("role_preview", args=["bibliothek"]), {"area_devices": "edit"})
        self.assertContains(preview, "Geräte und Schäden")

    def test_delete_moves_accounts(self):
        self.client.post(self.url("role_new"), {"name": "Kurz"})
        account = User.objects.create_user("k", password="x" * 14, role="kurz")
        self.client.post(self.url("role_delete", args=["kurz"]), {"move_to": ""})
        self.assertTrue(UserRole.objects.filter(key="kurz").exists())  # accounts need a new role first
        self.client.post(self.url("role_delete", args=["kurz"]), {"move_to": Role.SCHULLEITUNG})
        self.assertFalse(UserRole.objects.filter(key="kurz").exists())
        self.assertEqual(User.objects.get(pk=account.pk).role, Role.SCHULLEITUNG)

    def test_the_last_it_account_keeps_its_role_and_stays_unlocked(self):
        self.client.post(self.url("user_update", args=[self.it.pk]), {"action": "role", "role": Role.SCHULLEITUNG})
        self.assertEqual(User.objects.get(pk=self.it.pk).role, Role.IT)


class EscalationTests(AppTestCase):
    """Someone who manages users without being IT grants only what they may do themselves."""

    def setUp(self):
        from django.urls import reverse

        self.url = reverse
        office = UserRole.objects.get(key=Role.SEKRETARIAT)
        office.extras = [*office.extras, "admin.users"]
        office.save()
        self.manager = User.objects.get(pk=self.sek.pk)
        self.login(self.manager)

    def test_no_rights_beyond_ones_own(self):
        self.assertEqual(self.client.get(self.url("roles")).status_code, 200)
        self.client.post(self.url("role_new"), {"name": "Mehr", "start": "copy:sekretariat"})
        answer = self.client.post(self.url("role_edit", args=["mehr"]), {"name": "Mehr", "area_vault": "edit", "extra": ["vault.export"]})
        self.assertContains(answer, "selbst haben")
        self.assertNotIn("vault.export", UserRole.objects.get(key="mehr").extras)

    def test_no_it_and_not_ones_own_role(self):
        self.client.post(self.url("user_update", args=[self.sl.pk]), {"action": "role", "role": Role.IT})
        self.assertEqual(User.objects.get(pk=self.sl.pk).role, Role.SCHULLEITUNG)
        self.client.post(self.url("user_update", args=[self.it.pk]), {"action": "toggle"})
        self.assertTrue(User.objects.get(pk=self.it.pk).is_active)
        self.client.post(self.url("user_update", args=[self.manager.pk]), {"action": "role", "role": Role.SCHULLEITUNG})
        self.assertEqual(User.objects.get(pk=self.manager.pk).role, Role.SEKRETARIAT)


class LimitedRoleTests(AppTestCase):
    def test_settings_and_start_follow_the_rights(self):
        from django.urls import reverse

        UserRole.objects.create(key="admin2", name="Kontenpflege", areas={"home": "read", "devices": "read"}, extras=["admin.users"])
        account = User.objects.create_user("a2", password="x" * 14, role="admin2")
        self.login(account)
        self.assertEqual(self.client.get(reverse("settings_home")).status_code, 200)  # without the protocol right
        start = self.client.get(reverse("dashboard"))
        self.assertEqual(start.status_code, 200)
        self.assertContains(start, reverse("assets"))
        self.assertEqual(self.client.get(reverse("pupils")).status_code, 403)

    def test_mask_only_for_some_roles(self):
        from django.urls import reverse
        from masks.models import Mask

        self.login(self.it)
        self.client.post(reverse("mask_list"), {"name": "Nur Büro", "entity": "pupil", "roles": [Role.SEKRETARIAT, "erfunden"]})
        mask = Mask.objects.get(name="Nur Büro")
        self.assertEqual(mask.roles, [Role.SEKRETARIAT])
        self.client.post(reverse("mask_action", args=[mask.pk]), {"action": "roles"})
        mask.refresh_from_db()
        self.assertEqual(mask.roles, [])
