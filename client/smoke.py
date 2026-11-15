"""Smoke test of the desktop client's sidecar: the three ways a database is made, then key pages.

    client/.venv/bin/python client/smoke.py                      # the sidecar from source
    client/.venv/bin/python client/smoke.py --sidecar client/dist/schule-sidecar/schule-sidecar

It talks to the sidecar exactly as the shell does (frames over stdin and stdout, see sidecar.py):
1. a demo database: sign in as IT and open the main pages,
2. a backup of it, restored into a new database: sign in, open the same pages, and run its regular backup,
3. a new, empty database: the sign-in page answers and the first account must set up 2FA.

Everything happens in a temporary folder that is removed afterwards. Exit code 0 means all passed.
"""
import argparse
import base64
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "client" / "sidecar" / "sidecar.py"

PAGES = ["/", "/schueler/", "/personal/", "/eltern/", "/klassen/", "/geraete/", "/schaeden/", "/zugaenge/",
         "/berichte/", "/import-export/", "/einstellungen/sicherung/", "/einstellungen/", "/einstellungen/synchronisation/"]
PREMIUM_PAGES = ["/unterricht/", "/unterricht/alle/", "/daz/", "/kindergarten/"]


def premium_installed():
    """Whether the sidecar loads the premium module: from a module's source or bundle (schule/modules.py)."""
    sys.path.insert(0, str(ROOT))
    from schule import modules

    return modules.installed("premium")


class Sidecar:
    def __init__(self, command, db_dir, keys, task=None, env=None):
        # REQUIRE_2FA as in the built app, whatever a developer's .env says.
        extra = env
        env = {**os.environ, "SCHULE_DB_DIR": str(db_dir), "PYTHONPATH": str(ROOT), "REQUIRE_2FA": "1",
               "SCHULE_CLIENT_DB_NAME": db_dir.name, **keys}
        env.pop("DJANGO_SETTINGS_MODULE", None)
        if task:
            env["SIDECAR_TASK"] = task
        # As the shell sets it, only for demo databases (main.rs), whatever a developer's .env says.
        env["ALLOW_DEMO_DATA"] = "1" if task == "demo" else ""
        env.update(extra or {})
        self.log = open(db_dir.parent / f"{db_dir.name}.log", "wb")
        self.proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.cookies = {}
        self.next_id = 1

    def send(self, header, body=b""):
        raw = json.dumps(header).encode()
        self.proc.stdin.write(struct.pack(">I", len(raw)) + raw + struct.pack(">I", len(body)) + body)
        self.proc.stdin.flush()

    def _exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.proc.stdout.read(size - len(data))
            if not chunk:
                raise SystemExit(f"sidecar ended; see {self.log.name}")
            data += chunk
        return data

    def read(self):
        (length,) = struct.unpack(">I", self._exact(4))
        header = json.loads(self._exact(length))
        (length,) = struct.unpack(">I", self._exact(4))
        return header, self._exact(length)

    def run_task(self, task, data, may_fail=False):
        self.send({"control": task}, json.dumps(data).encode())
        while True:
            header, _ = self.read()
            if "progress" in header:
                continue
            if header.get("task") == "failed":
                if may_fail:
                    return header
                raise SystemExit(f"task {task} failed: {header.get('error')}")
            if header.get("task") == "done":
                return header

    def wait_ready(self):
        while True:
            header, _ = self.read()
            if header.get("ready"):
                return header

    def request(self, method, path, form=None):
        headers = [["Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items())]] if self.cookies else []
        body = b""
        if form is not None:
            body = urlencode(form).encode()
            headers += [["Content-Type", "application/x-www-form-urlencoded"], ["Origin", "https://localhost"],
                        ["Referer", f"https://localhost{path}"]]
            if "csrftoken" in self.cookies:
                headers.append(["X-CSRFToken", self.cookies["csrftoken"]])
        rid, self.next_id = self.next_id, self.next_id + 1
        self.send({"id": rid, "method": method, "path": path, "query": "", "headers": headers}, body)
        while True:
            header, content = self.read()
            if header.get("id") == rid:
                break
        location = None
        for name, value in header["headers"]:
            if name.lower() == "set-cookie":
                key, _, rest = value.partition("=")
                self.cookies[key] = rest.split(";", 1)[0]
            elif name.lower() == "location":
                location = value
        return header["status"], content, location

    def page_token(self, path):
        """The CSRF token a page carries, for a form posted from it."""
        _, page, _ = self.request("GET", path)
        return re.search(rb'name="csrf-token" content="([^"]+)"|name="csrfmiddlewaretoken" value="([^"]+)"', page).group(0).split(b'"')[3].decode()

    def sign_in(self, username, password):
        status, page, _ = self.request("GET", "/anmelden/")
        check("sign-in page", status, 200)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1).decode()
        status, _, location = self.request("POST", "/anmelden/", {"csrfmiddlewaretoken": token, "username": username, "password": password})
        return status, location

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


failures = []


def check(label, got, want):
    good = got == want
    print(("OK   " if good else "FAIL ") + label + ("" if good else f"  (got {got!r}, want {want!r})"))
    if not good:
        failures.append(label)


def new_keys():
    return {"SQLCIPHER_KEY": secrets.token_hex(32), "VAULT_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "SECRET_KEY": secrets.token_urlsafe(40), "MEDIA_KEY": base64.b64encode(secrets.token_bytes(32)).decode()}


def open_pages(sidecar, label):
    pages = PAGES + (PREMIUM_PAGES if premium_installed() else [])
    for path in pages:
        status, _, _ = sidecar.request("GET", path)
        check(f"{label}: {path}", status, 200)
    if premium_installed():
        check(f"{label}: the grade book's script", sidecar.request("GET", "/static/js/gradebook.js")[0], 200)
    if os.environ.get("DATAFLUSS_MODULE_PATH") and premium_installed():
        _, page, _ = sidecar.request("GET", "/unterricht/")
        check(f"{label}: the bundle's stylesheet is linked", b"/static/css/premium.css" in page, True)
        check(f"{label}: the bundle's stylesheet is served", sidecar.request("GET", "/static/css/premium.css")[0], 200)


def regular_backup(sidecar, folder, password):
    """The backup plan of a database of its own (archive/plan.py): set up on the page, then run now."""
    folder.mkdir()
    token = sidecar.page_token("/einstellungen/sicherung/")
    form = {"folder": str(folder), "every": "day", "keep": "7", "password": password, "repeat": password, "csrfmiddlewaretoken": token}
    status, body, _ = sidecar.request("POST", "/einstellungen/sicherung/plan/", form)
    check("regular backup: set up", status, 200)
    if status != 200:
        print(f"     {body.decode(errors='replace')[:300]}")
    status, _, _ = sidecar.request("POST", "/einstellungen/sicherung/plan/jetzt/", {"csrfmiddlewaretoken": token})
    check("regular backup: started", status, 200)
    for _ in range(240):
        _, body, _ = sidecar.request("GET", "/einstellungen/sicherung/status/")
        job = json.loads(body or b"{}")
        if job.get("state") != "running":
            break
        time.sleep(0.5)
    check("regular backup: done", job.get("state"), "done")
    check("regular backup: one file in the folder", len(list(folder.glob("*.svbackup"))), 1)


def make_backup(python, db_dir, keys, target, password):
    """Writes a backup of a database the way the backup page does, with the database's own keys."""
    code = (f"import sys; sys.path.insert(0, {str(SIDECAR.parent)!r}); import sidecar\n"
            f"from pathlib import Path; sidecar._environment(Path({str(db_dir)!r}))\n"
            "import django; django.setup()\n"
            f"from archive import snapshot; snapshot.create(Path({str(target)!r}), {password!r})")
    env = {**os.environ, "PYTHONPATH": str(ROOT), **keys}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    subprocess.run([python, "-c", code], cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sidecar", help="a frozen sidecar program instead of the source")
    parser.add_argument("--bundles", help="a folder with module bundles (DATAFLUSS_MODULE_PATH); premium is then expected from a bundle")
    args = parser.parse_args()
    if args.bundles:
        os.environ["DATAFLUSS_MODULE_PATH"] = str(Path(args.bundles).resolve())
    command = [args.sidecar] if args.sidecar else [sys.executable, str(SIDECAR)]
    work = Path(tempfile.mkdtemp(prefix="datafluss-smoke-"))
    started = time.perf_counter()
    finished = False
    try:
        # 1. Demo database
        demo_dir, demo_keys = work / "demo", new_keys()
        demo_dir.mkdir()
        sidecar = Sidecar(command, demo_dir, demo_keys, task="demo")
        logins = {row["username"]: row["password"] for row in sidecar.run_task("demo", {})["logins"]}
        sidecar.wait_ready()
        status, location = sidecar.sign_in("it", logins["it"])
        check("demo: IT signs in", (status, location), (302, "/"))
        open_pages(sidecar, "demo")
        sidecar.close()

        # 2. Backup of the demo, restored into a new database
        backup, password = work / "demo.svbackup", "smoke-test-backup-password"
        make_backup(sys.executable if not args.sidecar else str(ROOT / "client" / ".venv" / "bin" / "python"),
                    demo_dir, demo_keys, backup, password)
        restored_dir = work / "restored"
        restored_dir.mkdir()
        sidecar = Sidecar(command, restored_dir, new_keys(), task="restore")
        done = sidecar.run_task("restore", {"path": str(backup), "password": password, "keep_secrets": True})
        check("restore: people came along", done.get("people", 0) > 0, True)
        sidecar.wait_ready()
        status, location = sidecar.sign_in("it", logins["it"])
        check("restore: IT signs in", (status, location), (302, "/"))
        open_pages(sidecar, "restore")
        regular_backup(sidecar, work / "regular", password)
        sidecar.close()

        # The premium module missing: a database with its data stays closed and unchanged, a backup with it is refused.
        if premium_installed():
            without = {"DATAFLUSS_MODULES": ""}
            sidecar = Sidecar(command, demo_dir, demo_keys, env=without)
            code = sidecar.proc.wait(timeout=120)
            sidecar.log.close()
            message = (work / "demo.log").read_text()
            check("without premium: the demo database stays closed", (code, "fehlt" in message), (3, True))
            refused_dir = work / "refused"
            refused_dir.mkdir()
            sidecar = Sidecar(command, refused_dir, new_keys(), task="restore", env=without)
            answer = sidecar.run_task("restore", {"path": str(backup), "password": password}, may_fail=True)
            check("without premium: the premium backup is refused", "fehlt" in (answer.get("error") or ""), True)
            sidecar.close()

        # 3. New, empty database
        new_dir = work / "new"
        new_dir.mkdir()
        sidecar = Sidecar(command, new_dir, new_keys(), task="create")
        account = {"school": "Smoke-Test-Schule", "username": "it", "password": "ein-langes-Passwort-2026"}
        sidecar.run_task("create", account)
        sidecar.wait_ready()
        check("new: first account signs in", sidecar.sign_in(account["username"], account["password"]), (302, "/"))
        status, _, location = sidecar.request("GET", "/")
        check("new: first account must set up 2FA", (status, location), (302, "/anmelden/zwei-faktor/einrichten/"))
        sidecar.close()

        # 4. A new database that leaves out the premium module (the start screen's wizard)
        if premium_installed():
            plain_dir = work / "plain"
            plain_dir.mkdir()
            sidecar = Sidecar(command, plain_dir, new_keys(), task="demo")
            logins = {row["username"]: row["password"] for row in sidecar.run_task("demo", {"modules": []})["logins"]}
            sidecar.wait_ready()
            check("without premium in the database: no DaZ demo account", "daz" in logins, False)
            check("without premium in the database: IT signs in", sidecar.sign_in("it", logins["it"]), (302, "/"))
            status, page, _ = sidecar.request("GET", "/")
            check("without premium in the database: no Unterricht in the menu", (status, b'href="/unterricht/"' in page), (200, False))
            check("without premium in the database: DaZ is closed", sidecar.request("GET", "/daz/")[0], 403)
            sidecar.close()
        finished = True
    finally:
        if finished and not failures:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"Kept {work} with the sidecars' logs", file=sys.stderr)
    print(f"{'ALL OK' if not failures else f'{len(failures)} FAILED'} in {time.perf_counter() - started:.0f} s")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
