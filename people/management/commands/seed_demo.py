import io
import random
import secrets
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image, ImageDraw, ImageFont

from core import modules
from core.context_user import untracked
from core.models import Role, SchoolSettings, User
from devices.models import Asset, AssetCategory, Assignment, CampaignItem, Place, ReturnCampaign, SupportCase
from masks.defaults import setup
from people.models import Card, ClassGroup, Comment, Enrollment, Guardian, Guardianship, Person, SchoolYear
from vault import crypto
from vault.models import Credential, Platform
from vault.services import generate_login

FIRST_F = "Emma Mia Hannah Lena Sophia Lina Clara Marie Greta Ida Frieda Paula Luisa Nora Maja Elisa Zoë Amelie Johanna Charlotte Anh Yuki Sofia Aylin Çağla Ingrid Noor Valentina".split()
FIRST_M = "Paul Felix Jonas Leon Noah Emil Theo Anton Jakob Moritz Oskar Henry Karl Lukas Tomáš Max Ben David Samuel Elias Mateo Kenji Luca Omar Mehmet Nils Erik Julian".split()
LAST = ("Müller Schmidt Schneider Fischer Weber Meyer Wagner Becker Schulz Hoffmann Koch Richter Klein Wolf Neumann Schwarz Zimmermann Braun Krüger Hofmann "
        "Lange Werner Krause Lehmann Köhler Maier Hermann König Walter Peters Dvořák Nguyễn Yılmaz Kowalski Rossi Tanaka Popović Østergaard García Haddad").split()
CN_FIRST = "小明 雨桐 子涵 欣怡 浩然 梓萱 宇轩 诗涵".split()
CN_LAST = "王 李 张 刘 陈 杨 黄 赵".split()
COMPOUNDS = ["Wohnpark Nord", "Am Stadtgarten", "Lindenhof", "Seeblick"]
ACCOUNTS = [("it", Role.IT, "Ina", "Technik"), ("sekretariat", Role.SEKRETARIAT, "Sina", "Sekretariat"), ("leitung", Role.SCHULLEITUNG, "Lea", "Leitung")]
# Kindergarten groups of the demo school and their colours.
KIGA_GROUPS = {"Blaue Gruppe": "#3381ff", "Gelbe Gruppe": "#ffeb52", "Grüne Gruppe": "#43d682", "Orange Gruppe": "#ffb452", "Rote Gruppe": "#f3766d"}


def demo_accounts():
    """The user names of all demo accounts, the modules' included."""
    return [account[0] for account in ACCOUNTS] + [account[0] for demo in modules.hooks("demo") for account in demo.accounts]


def avatar(name, seed):
    random.seed(seed)
    hue = random.choice([(219, 234, 254), (220, 252, 231), (254, 243, 199), (252, 231, 243), (237, 233, 254), (224, 242, 254)])
    ink = tuple(max(c - 130, 20) for c in hue)
    img = Image.new("RGB", (240, 336), hue)
    draw = ImageDraw.Draw(img)
    draw.ellipse((62, 70, 178, 186), fill=ink)
    draw.ellipse((22, 214, 218, 470), fill=ink)
    buffer = io.BytesIO()
    img.save(buffer, "PNG", optimize=True)
    return ContentFile(buffer.getvalue())


class Command(BaseCommand):
    help = "Fills the system with invented demo data. Never contains real people."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Remove existing people, devices and logins first")
        parser.add_argument("--confirm-school", default="", help="The school's exact name, required with --reset")
        parser.add_argument("--imported-are-test-data", action="store_true", help="Add DaZ demo records even though people were imported. Never use with real pupils")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.ALLOW_DEMO_DATA:
            raise CommandError("Demodaten sind hier nicht erlaubt. Nur auf einem System ohne echte Personen die Variable ALLOW_DEMO_DATA=1 setzen.")
        if options["reset"] and options["confirm_school"] != SchoolSettings.load().name:
            raise CommandError(f"--reset löscht alle Personen. Zur Bestätigung --confirm-school \"{SchoolSettings.load().name}\" angeben.")
        random.seed(2026)
        setup()
        demos = modules.hooks("demo")  # the modules' parts: DaZ, Kindergarten, Unterricht
        if options["reset"]:
            # Demo data bypasses the change log and the sync journal on purpose (untracked and plain bulk calls).
            with untracked():
                Credential.objects.all().delete()
                for demo in demos:
                    demo.reset()
                SupportCase.objects.all().delete()
                Asset.objects.all().delete()
                Guardian.objects.all().delete()
                Person.objects.all().delete()
                ClassGroup.objects.all().delete()
                SchoolYear.objects.all().delete()
                for demo in demos:
                    demo.reset_after()
                Place.objects.all().delete()
                ReturnCampaign.objects.all().delete()
        if Person.objects.exists():
            if Person.objects.exclude(legacy_ninox_id="").exists() and not options["imported_are_test_data"]:
                self.stdout.write("Es sind importierte Personen vorhanden. Es werden keine Demodaten ergänzt.")
                return
            pending = [demo for demo in demos if demo.wanted()]
            if pending:
                with untracked():
                    done = [demo.top_up() for demo in pending]
                self._accounts()
                for message in done:
                    self.stdout.write(self.style.SUCCESS(message))
                return
            self.stdout.write("Es sind bereits Daten vorhanden. Mit --reset neu anlegen.")
            return
        with untracked():
            self._school()
            self._people()
            self._devices()
            self._logins()
            for demo in demos:
                demo.fill()
        self._accounts()
        self.stdout.write(self.style.SUCCESS(
            f"Demo bereit: {Person.objects.filter(kind='pupil').count()} Schüler, {Person.objects.filter(kind='staff').count()} Personal, "
            f"{Asset.objects.count()} Geräte, {Credential.objects.count()} Zugänge."
        ))

    def _school(self):
        school = SchoolSettings.load()
        school.name = "Schule Musterstadt"
        school.short_name = "Musterstadt"
        school.email_domain = "schule.example"
        school.save()
        self.year = SchoolYear.objects.create(name="2026/27", start=date(2026, 8, 1), end=date(2027, 7, 31), is_current=True)
        self.past = SchoolYear.objects.create(name="2025/26", start=date(2025, 8, 1), end=date(2026, 7, 31))
        self.groups = []
        for level in range(1, 13):
            for letter in ("a", "b") + (("c",) if level == 4 else ()):
                self.groups.append(ClassGroup.objects.create(name=f"{level}{letter}", level=level, school_year=self.year))
        self.kiga = []
        for name, color in KIGA_GROUPS.items():
            self.kiga.append(ClassGroup.objects.create(name=name, kind=ClassGroup.GRUPPE, school_year=self.year, color=color))
        self.leavers = ClassGroup.objects.create(name="Austritt 2026", kind=ClassGroup.AUSTRITT)

    def _person(self, kind, group=None, **extra):
        female = random.random() < 0.5
        chinese = random.random() < 0.08
        if chinese:
            first, last = random.choice(CN_FIRST), random.choice(CN_LAST)
        else:
            first = random.choice(FIRST_F if female else FIRST_M)
            if random.random() < 0.15:
                first += " " + random.choice(FIRST_F if female else FIRST_M)
            last = random.choice(LAST)
        slug = str(random.randrange(10, 99))
        ascii_first = "".join(ch for ch in (first.split()[0]).lower().translate(str.maketrans("äöüßøçğıšáễ", "aousocgisae")) if ch.isascii() and ch.isalpha()) or "name"
        ascii_last = "".join(ch for ch in last.lower().translate(str.maketrans("äöüßøçğıšáễ", "aousocgisae")) if ch.isascii() and ch.isalpha()) or "chen"
        person = Person(
            kind=kind, first_name=first, last_name=last, family_name_first=chinese, gender="Weiblich" if female else "Männlich",
            email=f"{ascii_first}.{ascii_last}{slug}@schule.example", **extra,
        )
        person.save()
        if random.random() < 0.92:
            person.photo.save(f"demo-{person.pk}.png", avatar(person.display_name, str(person.pk)), save=True)
        if group:
            Enrollment.objects.create(person=person, class_group=group, start=person.entry_date or self.year.start)
            person.current_class = group
            person.save()
        return person

    def _people(self):
        today = date(2026, 10, 31)
        families = []
        self.pupils = []
        for group in self.groups:
            for _ in range(random.randint(13, 20)):
                level = group.level
                stype = "GS" if level <= 4 else random.choice(["GY", "GY", "OS", "RS"]) if level <= 10 else "GY"
                person = self._person(
                    Person.PUPIL, group, school_type=stype, grade_level=level,
                    birth_date=date(2026 - level - 6, random.randint(1, 12), random.randint(1, 28)),
                    birth_place=random.choice(["Peking", "Berlin", "München", "Hamburg", "Shanghai", "Wien"]),
                    entry_date=date(random.randint(2026 - level, 2026), 8, 1), bus=random.random() < 0.4, dyslexia=random.random() < 0.05,
                )
                self.pupils.append(person)
                family = next((f for f in families if f["last"] == person.last_name and random.random() < 0.35), None)
                if family is None:
                    parents = []
                    for role, names in (("Elternteil 1", FIRST_F), ("Elternteil 2", FIRST_M)):
                        if role == "Elternteil 2" and random.random() < 0.2:
                            continue
                        first = random.choice(names)
                        ascii_name = "".join(c for c in first.lower() if c.isascii() and c.isalpha()) or "eltern"
                        parents.append((Guardian.objects.create(first_name=first, last_name=person.last_name, email=f"{ascii_name}.{random.randrange(100, 999)}@eltern.example", phone=f"+49 151 {random.randrange(1000, 9999)} {random.randrange(1000, 9999)}"), role))
                    family = {"last": person.last_name, "parents": parents}
                    families.append(family)
                for guardian, role in family["parents"]:
                    Guardianship.objects.create(person=person, guardian=guardian, role=role)
                if random.random() < 0.8:
                    Card.objects.create(person=person, code=str(random.randrange(10**9, 4 * 10**9)), raw_input="")
        for group in self.kiga:
            for _ in range(random.randint(9, 14)):
                person = self._person(Person.PUPIL, group, school_type="Kiga", birth_date=date(random.randint(2021, 2023), random.randint(1, 12), random.randint(1, 28)), entry_date=date(2025, 9, 1))
                person.email = ""
                person.save()
        guests = []
        for _ in range(12):
            guests.append(self._person(Person.PUPIL, random.choice(self.groups[16:]), school_type="Gast", entry_date=today - timedelta(days=26), exit_date=today - timedelta(days=8)))
        self.guests = guests
        for i, person in enumerate(self.pupils[:6]):
            person.new_codes_needed = True
            person.email = "" if i < 2 else person.email
            person.save()
        for _ in range(48):
            person = self._person(Person.PUPIL, self.leavers, school_type=random.choice(["GY", "OS"]), archived=True, entry_date=date(2019, 8, 1), exit_date=date(2026, 7, 31), birth_date=date(2008, random.randint(1, 12), 5))
        cats = ["Lehrpersonal"] * 26 + ["Kindergarten"] * 9 + ["Verwaltung"] * 5 + ["Technik"] * 2 + ["Schulleitung"] * 2 + ["Praktikant"] * 2
        self.staff = []
        for category in cats:
            person = self._person(
                Person.STAFF, category=category, entry_date=date(random.randint(2016, 2026), 8, 1), birth_date=date(random.randint(1965, 1998), random.randint(1, 12), 12),
                mobile=f"+49 160 {random.randrange(1000, 9999)} {random.randrange(1000, 9999)}", phone_office=f"+49 30 8532 {random.randrange(1000, 9999)}",
                compound=random.choice(COMPOUNDS), street=f"Musterstraße {random.randint(1, 80)}", district="Mitte", city="12345 Musterstadt",
            )
            person.salutation = "Frau" if person.gender == "Weiblich" else "Herr"
            person.abbreviation = (person.last_name[:3]).upper()
            person.save()
            self.staff.append(person)
            Card.objects.create(person=person, code=str(random.randrange(10**9, 4 * 10**9)))
        Comment.objects.create(person=self.pupils[0], text="Beispielkommentar: Eltern wünschen Rückruf wegen Gerätetausch.")

    def _devices(self):
        surface = AssetCategory.objects.get(name="Surface")
        teacher = AssetCategory.objects.get(name="Lehrergerät")
        ipad = AssetCategory.objects.get(name="iPad (MS)")
        guest = AssetCategory.objects.get(name="Gastgerät (GS)")

        def serial():
            return f"{random.randrange(10**9, 10**10):012d}"

        def mk(category, number=None):
            return Asset.objects.create(number=number or category.allocate_number(), category=category, serial=serial() if category != ipad else "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", k=10)))

        assets = []
        for person in [p for p in self.pupils if (p.grade_level or 0) >= 5]:
            if random.random() < 0.72:
                asset = mk(surface if person.grade_level >= 8 else ipad)
                Assignment.objects.create(asset=asset, person=person, issued_at=date(2025, 8, 19), pen="Ja", keyboard="Ja")
                assets.append((asset, person))
        for person in self.staff:
            if random.random() < 0.8:
                asset = mk(teacher)
                Assignment.objects.create(asset=asset, person=person, issued_at=date(2024, 8, 19))
        for person in self.guests:
            Assignment.objects.create(asset=mk(guest), person=person, issued_at=date(2026, 10, 5))
        for name, kind in (("Bibliothek", Place.ROOM), ("Kiga Musik", Place.GROUP), ("Blaue Gruppe", Place.GROUP), ("Büro 1005", Place.ROOM)):
            place = Place.objects.create(name=name, kind=kind)
            Assignment.objects.create(asset=mk(surface), place=place, issued_at=date(2025, 8, 1))
        for _ in range(14):
            mk(surface)
        old = Assignment.objects.filter(person__in=self.pupils[-5:]).select_related("asset")
        texts = [
            ("Display gesprungen nach Sturz.", ["Display"]), ("Touch reagiert stellenweise nicht.", ["Display"]), ("Akku lädt nicht mehr.", ["Interne Hardware"]),
            ("Scharnier lose, Ständer wackelt.", ["Ständer/Scharnier"]), ("Wasserschaden, Gerät startet nicht.", ["Interne Hardware"]), ("Sehr langsam, Tastatur fällt aus.", ["Interne Hardware", "Tastatur"]),
        ]
        for asset, person in random.sample(assets, min(24, len(assets))):
            text, parts = random.choice(texts)
            status = random.choice(["Erledigt", "Erledigt", "Erledigt", "Offen", "In Reparatur", "Wartet auf Ersatz"])
            SupportCase.objects.create(person=person, asset=asset, description=text, parts=parts, date=date(2026, random.randint(1, 10), random.randint(1, 28)), status=status)
            if status != "Erledigt":
                asset.damaged = True
                asset.save()
        campaign = ReturnCampaign.objects.create(name="Rückgabe Klasse 12", date=date(2027, 6, 18))
        for assignment in Assignment.objects.filter(person__grade_level=12, returned_at__isnull=True).select_related("asset"):
            CampaignItem.objects.create(campaign=campaign, asset=assignment.asset)

    def _logins(self):
        platforms = {p.key: p for p in Platform.objects.all()}
        for person in Person.objects.filter(kind=Person.PUPIL, archived=False).exclude(school_type__in=["Kiga", "Gast"]):
            if person.email:
                generate_login(person, platforms["m365"])
            generate_login(person, platforms["bibox"])
            generate_login(person, platforms["anton"])
            if (person.grade_level or 0) >= 5:
                for key in ("cornelsen", "klett", "untis"):
                    generate_login(person, platforms[key])
            if (person.grade_level or 0) >= 7:
                generate_login(person, platforms["buchner"])
                generate_login(person, platforms["fobizz"])
        for guardian in Guardian.objects.all():
            generate_login(guardian, platforms["untis-eltern"])
        for person in self.staff:
            generate_login(person, platforms["m365"])
        Credential.objects.update(must_change=False)

    def _accounts(self):
        path = Path(settings.BASE_DIR) / ".secrets" / "demo-logins.txt"
        lines = []
        demos = modules.hooks("demo")
        for username, role, first, last in ACCOUNTS + [account for demo in demos for account in demo.accounts]:
            user, created = User.objects.get_or_create(username=username, defaults={"role": role, "first_name": first, "last_name": last})
            if created:
                password = "Demo-" + secrets.token_urlsafe(9)
                user.set_password(password)
                user.save()
                lines.append(f"{username}\t{password}")
            for demo in demos:
                demo.link(user, role)
        if lines:
            path.parent.mkdir(exist_ok=True)
            with path.open("a") as handle:
                handle.write("\n".join(lines) + "\n")
            path.chmod(0o600)
            self.stdout.write(f"Demo-Anmeldedaten: {path}")
