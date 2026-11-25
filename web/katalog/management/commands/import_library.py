"""Publishes DataFluss's own database templates (tables/library) as official templates of the catalog."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from katalog import services
from katalog.models import Template, Version
from tables import template_check as tc

CATEGORY = {"filme": "sammlung", "buecherei": "bildung", "termine": "verein", "inventar": "arbeit"}


class Command(BaseCommand):
    help = "Veröffentlicht die mitgelieferten Datenbank-Vorlagen als offizielle Vorlagen."

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="?", default=str(tc.LIBRARY))

    def handle(self, folder, **options):
        for path in sorted(Path(folder).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("database") or data.get("listed") is False:
                continue
            template = Template.objects.filter(key=data["id"]).first()
            if template is not None and template.versions.filter(number=data["version"], status=Version.APPROVED).exists():
                self.stdout.write(f"{data['id']} {data['version']}: schon da")
                continue
            version, problems = services.submit(None, path.read_bytes(), CATEGORY.get(data["id"], "sonstiges"), template=template, official=True)
            if problems:
                self.stderr.write(f"{data['id']}: {' '.join(problems)}")
                continue
            services.approve(version, None, "")
            self.stdout.write(f"{data['id']} {data['version']}: veröffentlicht")
