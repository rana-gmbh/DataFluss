#!/usr/bin/env python
"""The template site datafluss.de (docs/website.md). Its own Django project; it shares only the template check
(tables/template_check.py) with DataFluss, which is why the repository's root is on the path."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datafluss_web.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
