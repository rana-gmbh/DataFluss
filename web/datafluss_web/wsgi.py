import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datafluss_web.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
