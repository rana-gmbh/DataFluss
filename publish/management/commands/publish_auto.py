"""Publishes what is due (publish/auto.py). deploy/start.sh runs it with --loop, since the container has no cron."""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Publish the publications that are due by themselves (after changes or daily)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep running and look every minute.")

    def handle(self, *args, loop=False, **options):
        from publish import auto

        while True:
            ran = auto.tick()
            if ran and options.get("verbosity", 1) > 1:
                self.stdout.write(f"{ran} published")
            if not loop:
                return
            close_old_connections()  # a long-running loop must not keep a dropped connection
            time.sleep(auto.EVERY_SECONDS)
