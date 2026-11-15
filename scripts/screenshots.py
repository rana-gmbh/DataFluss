"""Screenshots for the README (docs/screenshots/ of the public repository), always from invented data.

    .venv/bin/pip install playwright      # once; it drives the installed Google Chrome
    .venv/bin/python scripts/screenshots.py

A fresh demo database with the basic modules only ("Schule Musterstadt", seed_demo) in a temporary
folder, served by Django on a free port; the start screen of the desktop app from client/shell/ui with a
stand-in for the shell (screenshots/launcher-mock.js). Each shot is 1440 × 900 at 2×, in a window frame.
"""
import base64
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PY = sys.executable
WIDTH, HEIGHT = 1440, 900


def django_env(work):
    return {**os.environ, "DATABASE_URL": "sqlite://", "SQLITE_PATH": str(work / "db.sqlite3"), "MEDIA_ROOT": str(work / "media"),
            "BACKUP_ROOT": str(work / "backups"), "VAULT_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "SECRET_KEY": secrets.token_urlsafe(32), "DEBUG": "1", "REQUIRE_2FA": "0", "ALLOW_DEMO_DATA": "1",
            "DATAFLUSS_MODULES": "", "TIME_ZONE": "Europe/Berlin", "SERVER_ID": "eu"}


def manage(env, *args, code=None):
    command = [PY, "manage.py", *args] if code is None else [PY, "manage.py", "shell", "-v0", "-c", code]
    return subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout


def demo(work, env):
    manage(env, "migrate", "-v0")
    manage(env, "setup_school")
    manage(env, code=(f"from pathlib import Path; from django.conf import settings; settings.BASE_DIR = Path({str(work)!r})\n"
                      "from django.core.management import call_command; call_command('seed_demo', verbosity=0)\n"
                      "from core.models import User; User.objects.update(tours_seen=['platform', 'checklist'])"))
    logins = dict(line.split("\t", 1) for line in (work / ".secrets" / "demo-logins.txt").read_text().splitlines() if "\t" in line)
    person = manage(env, code=("from people.models import Person\n"
                               "p = Person.objects.filter(kind='pupil', current_class__name='7a', archived=False).exclude(photo='').order_by('last_name').first()\n"
                               "print(p.pk)")).strip()
    return logins, person


def serve(env):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/", timeout=1)
            return server, f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.2)
    server.terminate()
    sys.exit("The demo server did not start")


def launcher_copy(work):
    """The start screen with the stand-in shell in front of its script."""
    ui = work / "ui"
    shutil.copytree(ROOT / "client" / "shell" / "ui", ui)
    shutil.copy(ROOT / "scripts" / "screenshots" / "launcher-mock.js", ui / "mock.js")
    page = (ui / "index.html").read_text()
    (ui / "index.html").write_text(page.replace('<script src="launcher.js" defer></script>', '<script src="mock.js"></script><script src="launcher.js" defer></script>'))
    return ui / "index.html"


FRAME = """<!doctype html><html><head><style>
  html, body {{ margin: 0; background: transparent; }}
  .shot {{ display: inline-block; padding: 40px 48px 56px; }}
  .window {{ width: {width}px; border-radius: 12px; overflow: hidden; background: #fff;
    box-shadow: 0 0 0 1px rgb(0 0 0 / .12), 0 24px 60px rgb(15 23 42 / .28); }}
  .bar {{ height: 32px; display: flex; align-items: center; gap: 8px; padding: 0 14px; background: #ececee; border-bottom: 1px solid #d9d9de; }}
  .bar span {{ width: 12px; height: 12px; border-radius: 50%; }}
  .bar span:nth-child(1) {{ background: #ff5f57; }} .bar span:nth-child(2) {{ background: #febc2e; }} .bar span:nth-child(3) {{ background: #28c840; }}
  .bar b {{ flex: 1; text-align: center; margin-right: 52px; font: 600 13px -apple-system, system-ui, sans-serif; color: #4b5563; }}
  img {{ display: block; width: {width}px; }}
</style></head><body><div class="shot"><div class="window"><div class="bar"><span></span><span></span><span></span><b>{title}</b></div>
<img src="data:image/png;base64,{image}"></div></div></body></html>"""


def framed(browser, raw, title, target):
    page = browser.new_page(device_scale_factor=2)
    page.set_content(FRAME.format(width=WIDTH, title=title, image=base64.b64encode(raw).decode()))
    page.locator(".shot").screenshot(path=str(target), omit_background=True)
    page.close()
    from PIL import Image

    Image.open(target).save(target, optimize=True)


def main():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="datafluss-shots-"))
    env = django_env(work)
    server = None
    try:
        logins, person = demo(work, env)
        server, url = serve(env)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            pages = {}

            def signed_in(account):
                """A browser session per account; the office's start page has no backup notices, which only IT gets."""
                if account not in pages:
                    context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2, locale="de-DE", color_scheme="light")
                    page = context.new_page()
                    page.goto(f"{url}/anmelden/")
                    page.fill("#username", account)
                    page.fill("#password", logins[account])
                    page.press("#password", "Enter")
                    page.wait_for_url(f"{url}/")
                    pages[account] = page
                return pages[account]

            shots = [("hero", "it", "/schueler/", "Schule Musterstadt · Schüler"), ("record", "it", f"/personen/{person}/", "Schule Musterstadt · Schüler"),
                     ("dashboard", "sekretariat", "/", "Schule Musterstadt · Start")]
            for name, account, path, title in shots:
                page = signed_in(account)
                page.goto(f"{url}{path}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(800)  # the grid and the photos settle
                framed(browser, page.screenshot(), title, OUT / f"{name}.png")
                print(f"{name}.png")
            start = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
            start.goto(launcher_copy(work).as_uri())
            start.wait_for_timeout(800)
            framed(browser, start.screenshot(), "DataFluss", OUT / "start.png")
            print("start.png")
            browser.close()
    finally:
        if server:
            server.terminate()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
