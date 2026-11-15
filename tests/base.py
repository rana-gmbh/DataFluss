from unittest import skipUnless

from django.test import TestCase

from schule import modules

from core.context_user import set_current_user
from core.models import Role, User
from masks.defaults import setup
from people.models import ClassGroup, Person, SchoolYear
from vault import crypto
from vault.models import Platform
from datetime import date


# Tests here that need Unterricht, DaZ or Kindergarten run only with the premium module loaded
# (schule/modules.py). They move to the premium module's own tests when the repositories split.
PREMIUM = modules.installed("premium")
needs_premium = skipUnless(PREMIUM, "needs the premium module")


def only_own_tests(cls):
    """For a module's tests that extend a platform test class: its inherited tests are not run again."""
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


class AppTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        crypto.ensure_key_file()
        setup()
        cls.it = User.objects.create_user("it", password="x" * 14, role=Role.IT)
        cls.sek = User.objects.create_user("sek", password="x" * 14, role=Role.SEKRETARIAT)
        cls.sl = User.objects.create_user("sl", password="x" * 14, role=Role.SCHULLEITUNG)
        cls.year = SchoolYear.objects.create(name="2026/27", start=date(2026, 8, 1), end=date(2027, 7, 31), is_current=True)
        cls.group = ClassGroup.objects.create(name="5a", level=5, school_year=cls.year)
        cls.top = ClassGroup.objects.create(name="12a", level=12, school_year=cls.year)
        cls.pupil = Person.objects.create(kind="pupil", first_name="Lena", last_name="Muster", email="lena@schule.example", school_type="GY", grade_level=5, current_class=cls.group)
        cls.m365 = Platform.objects.get(key="m365")

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["second_factor_ok"] = True
        session.save()

    def tearDown(self):
        set_current_user(None)
