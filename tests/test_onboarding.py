"""First steps: the guided tour (core/tour.py, static/js/tour.js) and the start page's checklist."""
import json

from django.urls import reverse

from core.models import SchoolSettings

from .base import AppTestCase


class TourTests(AppTestCase):
    def steps(self, response):
        start = response.content.decode().split('<script id="tour-steps" type="application/json">', 1)[1]
        return json.loads(start.split("</script>", 1)[0])

    def test_it_starts_by_itself_until_it_was_seen(self):
        self.login(self.it)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'data-auto="1"')
        self.assertEqual(self.client.post(reverse("tour_seen")).status_code, 200)
        self.it.refresh_from_db()
        self.assertEqual(self.it.tours_seen, ["platform"])
        self.assertContains(self.client.get(reverse("dashboard")), 'data-auto=""')

    def test_steps_follow_the_role_and_the_modules(self):
        self.login(self.sek)
        anchors = [step["anchor"] for step in self.steps(self.client.get(reverse("dashboard")))]
        self.assertIn("nav-pupils", anchors)
        self.assertNotIn("nav-settings_home", anchors)  # the office has no settings

    def test_every_step_points_at_an_anchor_of_the_page(self):
        self.login(self.it)
        page = self.client.get(reverse("dashboard"))
        html = page.content.decode()
        for step in self.steps(page):
            if step["anchor"] not in ("database", "sync"):  # only in the desktop client
                self.assertIn(f'data-tour="{step["anchor"]}"', html, step["anchor"])

    def test_only_known_tours_are_stored(self):
        self.login(self.it)
        self.client.post(reverse("tour_seen"), {"tour": "irgendwas"})
        self.it.refresh_from_db()
        self.assertEqual(self.it.tours_seen, [])


class ChecklistTests(AppTestCase):
    def page(self, user):
        self.login(user)
        return self.client.get(reverse("dashboard"))

    def test_it_ticks_itself_off(self):
        response = self.page(self.it)
        self.assertEqual(response.context["checklist_done"], 2)  # people exist, and several accounts
        labels = {str(label): done for label, _url, done in response.context["checklist"]}
        self.assertFalse(labels["Name und Logo eintragen"])
        SchoolSettings.objects.filter(pk=1).update(name="Deutsche Schule Musterstadt")
        self.assertEqual(self.page(self.it).context["checklist_done"], 3)

    def test_it_can_be_put_away(self):
        self.page(self.it)
        self.client.post(reverse("tour_seen"), {"tour": "checklist"})
        self.assertIsNone(self.page(self.it).context.get("checklist"))

    def test_only_for_those_who_set_up(self):
        from people.models import Person

        self.assertIsNone(self.page(self.sl).context.get("checklist"))
        Person.objects.all().delete()
        self.assertEqual(len(self.page(self.sek).context.get("checklist") or []), 1)  # the office imports people
