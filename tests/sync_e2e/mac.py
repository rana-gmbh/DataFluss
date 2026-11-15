"""One test Mac for run.py: mac.py NAME register USER PASSWORD | sync"""
import os, sys, json, time
import django
django.setup()
from django.conf import settings
from vault import crypto
E = os.environ["E"]; name = sys.argv[1]; action = sys.argv[2]
tokfile = f"{E}/{name}.token"
from sync import client
if action == "register":
    from django.core.management import call_command
    call_command("migrate", verbosity=0)
    answer = client.register(os.environ["SERVER_URL"], sys.argv[3], sys.argv[4], "", f"Test {name}")
    open(tokfile, "w").write(answer["token"]); open(f"{E}/{name}.vault", "w").write(answer["vault_key"])
    settings.VAULT_KEY = answer["vault_key"]; crypto.reset_key_cache()
    print("registered as", answer["node"], "user", answer["user"])
    t = time.time(); print(client.sync_once(answer["token"]), f"{time.time()-t:.1f} s")
elif action == "sync":
    t = time.time(); print(client.sync_once(open(tokfile).read()), f"{time.time()-t:.1f} s")
elif action == "shell":
    exec(sys.argv[3])
