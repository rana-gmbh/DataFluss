"""Two-way sync end to end: a real server and three real Macs, each its own process and database.

    .venv/bin/python tests/sync_e2e/run.py

Takes about three minutes. Everything happens in a temporary folder that is removed afterwards.
"""
import atexit
import base64
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = sys.executable
E = tempfile.mkdtemp(prefix="sync-e2e-")
atexit.register(shutil.rmtree, E, True)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    PORT = probe.getsockname()[1]
SERVER_URL = f"http://127.0.0.1:{PORT}"


def env_file(node):
    return f"{E}/{node}-env.sh"


def write_env():
    with open(f"{E}/vault.key", "w") as f:
        f.write(base64.b64encode(secrets.token_bytes(32)).decode())
    common = f"export PYTHONPATH={ROOT} DJANGO_SETTINGS_MODULE=schule.settings DEBUG=1 SERVER_URL={SERVER_URL}\n"
    with open(env_file("server"), "w") as f:
        f.write(common + f"export DATABASE_URL=sqlite:// SQLITE_PATH={E}/server.sqlite MEDIA_ROOT={E}/server-media "
                f"BACKUP_ROOT={E}/server-backups VAULT_KEY_FILE={E}/vault.key SERVER_ID=eu SECRET_KEY=e2e ALLOW_DEMO_DATA=1\n")
    media = base64.b64encode(secrets.token_bytes(32)).decode()
    for node in ("mac1", "mac2", "mac3", "mac4", "mac5"):
        with open(env_file(node), "w") as f:
            f.write(common + f"export DATABASE_URL=sqlite:// SQLITE_PATH={E}/{node}.sqlite MEDIA_ROOT={E}/{node}-media "
                    f"BACKUP_ROOT={E}/{node}-backups SERVER_ID=client-{node} SECRET_KEY=e2e-{node} MEDIA_KEY={media} "
                    f"VAULT_KEY=$(cat {E}/{node}.vault 2>/dev/null || echo {base64.b64encode(secrets.token_bytes(32)).decode()})\n")


def sh(node, command):
    out = subprocess.run(["sh", "-c", f". {env_file(node)}; cd {ROOT}; E={E} {command}"], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"{node}: {command}\n{out.stderr[-3000:]}")
    return out.stdout


def start_server():
    sh("server", f"{PY} manage.py migrate -v0 && {PY} manage.py setup_school")
    sh("server", f"{PY} manage.py shell -v0 -c " + shlex.quote(
        f"from pathlib import Path; from django.conf import settings; settings.BASE_DIR = Path('{E}')\n"
        "from django.core.management import call_command; call_command('seed_demo', verbosity=0)\n"
        "from core.models import SchoolSettings; SchoolSettings.objects.filter(pk=1).update(require_2fa=False)"))
    server = subprocess.Popen(["sh", "-c", f". {env_file('server')}; cd {ROOT}; exec {PY} manage.py runserver 127.0.0.1:{PORT} --noreload"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(server.terminate)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{SERVER_URL}/healthz/", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    sys.exit("server did not start")


def password(user):
    for line in open(f"{E}/.secrets/demo-logins.txt"):
        name, _, value = line.rstrip("\n").partition("\t")
        if name == user:
            return value


def register(node, user):
    print(sh(node, f"{PY} {ROOT}/tests/sync_e2e/mac.py {node} register {user} {shlex.quote(password(user))}").strip().splitlines()[-1])



def run(node, code):
    """Runs code on a node (server or a Mac) and returns what it prints as JSON."""
    env_file_path = env_file(node)
    script = f". {env_file_path}; cd {ROOT}; E={E} {PY} manage.py shell -v0 -c {shlex.quote(code)}"
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"{node} failed:\n{out.stderr[-2000:]}")
    lines = [l for l in out.stdout.splitlines() if l.startswith("@@")]
    return json.loads(lines[-1][2:]) if lines else None

def sync(node):
    out = subprocess.run(["sh", "-c", f". {env_file(node)}; cd {ROOT}; E={E} {PY} {ROOT}/tests/sync_e2e/mac.py {node} sync"], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"sync {node} failed:\n{out.stderr[-2000:]}")
    return out.stdout.strip().splitlines()[-1]

HEAD = "import json; from people.models import Person; from sync.models import Conflict\n"
def person_field(node, pk, field):
    return run(node, HEAD + f"p = Person._base_manager.filter(pk='{pk}').first(); print('@@' + json.dumps(None if p is None else getattr(p, '{field}'), default=str))")

ok = True
def check(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(("OK   " if good else "FAIL ") + label + ("" if good else f"  (got {got!r}, want {want!r})"))

def main():
    global ok
    pk = run("server", HEAD + "p = Person.objects.filter(kind='pupil').order_by('last_name').first(); print('@@' + json.dumps(str(p.pk)))")

    # 1 server edit reaches the Mac
    run("server", HEAD + f"p = Person.objects.get(pk='{pk}'); p.last_name = 'Server-Name'; p.save(); print('@@1')")
    sync("mac1")
    check("server edit arrives on the Mac", person_field("mac1", pk, "last_name"), "Server-Name")

    # 2 Mac edit reaches the server
    run("mac1", HEAD + f"p = Person.objects.get(pk='{pk}'); p.first_name = 'Mac-Vorname'; p.save(); print('@@1')")
    sync("mac1")
    check("Mac edit arrives on the server", person_field("server", pk, "first_name"), "Mac-Vorname")

    # 3 different form fields of the same pupil on both sides: both survive, no conflict
    before = run("server", HEAD + "print('@@' + json.dumps(Conflict.objects.count()))")
    run("server", HEAD + f"p = Person.objects.get(pk='{pk}'); p.custom = {{**p.custom, 'telefon_server': '111'}}; p.save(); print('@@1')")
    run("mac1", HEAD + f"p = Person.objects.get(pk='{pk}'); p.custom = {{**p.custom, 'telefon_mac': '222'}}; p.save(); print('@@1')")
    sync("mac1")
    s_custom = person_field("server", pk, "custom"); m_custom = person_field("mac1", pk, "custom")
    check("server keeps both form fields", (s_custom.get("telefon_server"), s_custom.get("telefon_mac")), ("111", "222"))
    check("Mac keeps both form fields", (m_custom.get("telefon_server"), m_custom.get("telefon_mac")), ("111", "222"))
    check("no conflict for different fields", run("server", HEAD + "print('@@' + json.dumps(Conflict.objects.count()))"), before)

    # 4 the same field on both sides: the later edit wins everywhere, and a conflict waits
    run("mac1", HEAD + f"p = Person.objects.get(pk='{pk}'); p.email = 'mac@schule.example'; p.save(); print('@@1')")
    run("server", HEAD + f"p = Person.objects.get(pk='{pk}'); p.email = 'server@schule.example'; p.save(); print('@@1')")
    sync("mac1"); sync("mac1")
    check("same field: server and Mac agree", (person_field("server", pk, "email"), person_field("mac1", pk, "email")), ("server@schule.example", "server@schule.example"))
    check("same field: one conflict to confirm", run("server", HEAD + "print('@@' + json.dumps(Conflict.objects.count()))"), before + 1)

    # 5 a pupil made on the Mac appears on the server
    new = run("mac1", HEAD + "p = Person.objects.create(kind='pupil', first_name='Neu', last_name='Vom-Mac', custom={'x': '1'}); print('@@' + json.dumps(str(p.pk)))")
    sync("mac1")
    check("record made on the Mac exists on the server", person_field("server", new, "last_name"), "Vom-Mac")

    # 6 a delete on the server removes it on the Mac
    run("server", HEAD + f"Person.objects.get(pk='{new}').delete(); print('@@1')")
    sync("mac1")
    check("delete on the server removes it on the Mac", person_field("mac1", new, "last_name"), None)



def scoping():
    """A Schulleitung computer may read but not change classes; IT computers reach each other through the server."""
    global ok
    klass = run("mac2", HEAD + "from people.models import ClassGroup; g = ClassGroup.objects.first(); g.name = 'Gehackt'; g.save(); print('@@' + json.dumps(str(g.pk)))")
    sync("mac2"); sync("mac2")
    GROUP = HEAD + "from people.models import ClassGroup; print('@@' + json.dumps(ClassGroup.objects.get(pk='{}').name))"
    check("refused change does not reach the server", run("server", GROUP.format(klass)) != "Gehackt", True)
    check("refused change is undone on the Mac", run("mac2", GROUP.format(klass)) == run("server", GROUP.format(klass)), True)

    pk2 = run("mac1", HEAD + "p = Person.objects.filter(kind='staff').first(); p.last_name = 'Von-Mac1'; p.save(); print('@@' + json.dumps(str(p.pk)))")
    sync("mac1"); sync("mac3")
    check("Mac to Mac through the server", person_field("mac3", pk2, "last_name"), "Von-Mac1")


TABLES = ("import json; from core.models import Role, User; from people.models import Person\n"
          "from tables import links, services; from tables.models import Field, Link, Record, Table\n")


def tables():
    """Own tables (docs/TABLES-PLAN.md): a read-only role, links offline on two computers, hidden targets, rights taken away."""
    global ok
    ids = run("server", TABLES + (
        "intern = services.create_table('Intern'); geheim = services.create_record(intern, {'f_name': 'Nur IT'})\n"
        "books = services.create_table('Bücher'); services.set_rights(books, {Role.SCHULLEITUNG: 'read'})\n"
        "who = services.create_field(books, 'Leser', 'link', config={'target': 'pupil', 'multiple': True})\n"
        "note = services.create_field(books, 'Ablage', 'link', config={'target': 'record', 'table': intern.key})\n"
        "momo = services.create_record(books, {'f_name': 'Momo'}); links.set_targets(momo, note, [str(geheim.pk)])\n"
        "pupils = [str(p.pk) for p in Person.objects.filter(kind='pupil').order_by('last_name')[:2]]\n"
        "print('@@' + json.dumps({'momo': str(momo.pk), 'geheim': str(geheim.pk), 'pupils': pupils}))"))
    for node in ("mac1", "mac2", "mac3"):
        sync(node)
    HOLDS = TABLES + "print('@@' + json.dumps([Record.objects.filter(pk='{}').exists(), Record.objects.filter(pk='{}').exists()]))"
    check("tables: IT computer holds both tables", run("mac1", HOLDS.format(ids["momo"], ids["geheim"])), [True, True])
    check("tables: Schulleitung computer holds only the readable table", run("mac2", HOLDS.format(ids["momo"], ids["geheim"])), [True, False])
    shown = run("mac2", TABLES + f"r = Record.objects.get(pk='{ids['momo']}'); f = Field.objects.get(table=r.table, key='f_ablage')\n"
                "print('@@' + json.dumps(links.display(r, f, User.objects.get(username='leitung'))))")
    check("tables: a link into a table the role may not read says so", shown, "nicht sichtbar")

    ADD = TABLES + "r = Record.objects.get(pk='{}'); f = Field.objects.get(table=r.table, key='f_leser'); links.add_target(r, f, '{}'); print('@@1')"
    run("mac1", ADD.format(ids["momo"], ids["pupils"][0]))
    run("mac3", ADD.format(ids["momo"], ids["pupils"][1]))
    sync("mac1"); sync("mac3"); sync("mac1")
    READERS = TABLES + "print('@@' + json.dumps(sorted(str(l.target_id) for l in Link.objects.filter(record_id='{}', field='f_leser'))))"
    both = sorted(ids["pupils"])
    check("tables: offline additions from two computers both reach the server", run("server", READERS.format(ids["momo"])), both)
    check("tables: and both computers", (run("mac1", READERS.format(ids["momo"])), run("mac3", READERS.format(ids["momo"]))), (both, both))

    run("mac2", TABLES + f"r = Record.objects.get(pk='{ids['momo']}'); r.values = {{**r.values, 'f_name': 'Gehackt'}}; r.save(); print('@@1')")
    sync("mac2"); sync("mac2")
    TITLE = TABLES + f"print('@@' + json.dumps(Record.objects.get(pk='{ids['momo']}').values['f_name']))"
    check("tables: a read-only computer cannot change a record", (run("server", TITLE), run("mac2", TITLE)), ("Momo", "Momo"))

    run("server", TABLES + "services.set_rights(Table.objects.get(key='buecher'), {}); print('@@1')")
    sync("mac2")
    check("tables: records leave a computer whose right was taken away", run("mac2", HOLDS.format(ids["momo"], ids["geheim"])), [False, False])


def field_rules():
    """Roles (core/fieldrules.py): a field the role does not see leaves its computer, and comes back with the right."""
    RULE = ("import json; from core.models import Role, UserRole; from people.models import Person\n"
            "r = UserRole.objects.get(key=Role.SCHULLEITUNG); r.fields = {rules}; r.save()\n"
            "p = Person.objects.filter(kind='pupil').exclude(email='').order_by('last_name').first(); print('@@' + json.dumps([str(p.pk), p.email]))")
    pk, email = run("server", RULE.format(rules="{'pupil': {'email': 'hidden'}}"))
    sync("mac2")
    check("roles: a hidden field leaves the computer", person_field("mac2", pk, "email"), "")
    ROLE = "import json; from core.models import UserRole; print('@@' + json.dumps(UserRole.objects.get(key='schulleitung').fields))"
    check("roles: the computer knows the role's rules", run("mac2", ROLE), {"pupil": {"email": "hidden"}})
    run("server", RULE.format(rules="{}"))
    sync("mac2")
    check("roles: and comes back when the role may see it again", person_field("mac2", pk, "email"), email)


if __name__ == "__main__":
    write_env()
    start_server()
    register("mac1", "it")
    main()
    register("mac2", "leitung")
    register("mac3", "it")
    scoping()
    field_rules()
    tables()
    # Scenarios of modules: DATAFLUSS_E2E_SCENARIOS names their files (a module's own repository), and
    # premium.py beside this file runs when the premium module is in this repository.
    scenario_files = [path for path in os.environ.get("DATAFLUSS_E2E_SCENARIOS", "").split(os.pathsep) if path]
    local = os.path.join(os.path.dirname(__file__), "premium.py")
    if os.path.isdir(os.path.join(ROOT, "daz")) and os.path.exists(local):
        scenario_files.append(local)
    for path in scenario_files:
        import importlib.util

        spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
        scenarios = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scenarios)
        scenarios.scenarios(sys.modules[__name__])
    print("ALL OK" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
