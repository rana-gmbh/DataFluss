"""The targets of phase 3 against local stand-ins: S3, Netlify, GitHub Pages, SFTP, and DataFluss hosting
(publish/cloud.py, publish/sftp.py, publish/hosting.py)."""
import base64
import hashlib
import json
import re
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlsplit

import paramiko
from django.core.exceptions import ValidationError
from django.test import LiveServerTestCase, override_settings
from django.urls import reverse

from publish import cloud, hosting, service, sftp, targets
from publish.models import HostedSite, Publication
from tables.models import Record

from .test_publish import Synchronous
from .test_publish_safety import PublishCase


class Fake(BaseHTTPRequestHandler):
    """A tiny web service: each test sets `route(handler, method, path, body) -> (status, bytes)`."""

    route = None
    calls = []

    def log_message(self, *args):
        pass

    def _answer(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Fake.calls.append((method, self.path, dict(self.headers), body))
        status, answer = Fake.route(self, method, unquote(urlsplit(self.path).path), body)
        self.send_response(status)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(answer)

    def do_GET(self):
        self._answer("GET")

    def do_HEAD(self):
        self._answer("HEAD")

    def do_PUT(self):
        self._answer("PUT")

    def do_POST(self):
        self._answer("POST")

    def do_PATCH(self):
        self._answer("PATCH")

    def do_DELETE(self):
        self._answer("DELETE")


class ServiceCase(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        Fake.calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def use(self, kind, config, secret):
        Publication.objects.filter(pk=self.publication.pk).update(target=kind, target_config=config)
        publication = Publication.objects.get(pk=self.publication.pk)
        targets.set_password(publication, secret)
        publication.save()
        return self.confirmed(publication)

    def publish(self):
        service.start(Publication.objects.get(pk=self.publication.pk), service.PUBLISH)
        publication = Publication.objects.get(pk=self.publication.pk)
        self.assertEqual(publication.status, Publication.DONE, publication.error)
        return publication


class S3Tests(ServiceCase):
    def test_the_signature_of_the_aws_example(self):
        """AWS's documented GET Object example (Signature Version 4, "Example: GET Object")."""
        empty = hashlib.sha256(b"").hexdigest()
        header = cloud.sigv4("GET", "examplebucket.s3.amazonaws.com", "/test.txt", "",
                             {"host": "examplebucket.s3.amazonaws.com", "range": "bytes=0-9", "x-amz-content-sha256": empty,
                              "x-amz-date": "20130524T000000Z"}, empty, "AKIAIOSFODNN7EXAMPLE",
                             "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1", "20130524T000000Z")
        self.assertTrue(header.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), header)

    def test_publish_and_remove_with_signed_requests(self):
        stored = {}

        def route(handler, method, path, body):
            auth = handler.headers["Authorization"]
            assert auth.startswith("AWS4-HMAC-SHA256 Credential=KEY123/") and "x-amz-date" in auth, auth
            assert handler.headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()
            key = path.removeprefix("/filme-bucket/")
            if method == "HEAD":
                return 200, b""
            if method == "PUT":
                stored[key] = (body, handler.headers["Content-Type"], handler.headers["Cache-Control"])
                return 200, b""
            if method == "GET":
                return (200, stored[key][0]) if key in stored else (404, b"")
            stored.pop(key, None)
            return 204, b""

        Fake.route = route
        self.use(Publication.S3, {"endpoint": self.url, "region": "eu-central-1", "bucket": "filme-bucket", "prefix": "kino", "key_id": "KEY123"},
                 "geheimes-secret")
        self.publish()
        self.assertEqual(stored["kino/index.html"][1], "text/html; charset=utf-8")
        media = [k for k in stored if k.startswith("kino/_media/")]
        self.assertTrue(media)
        self.assertEqual(stored[media[0]][2], "public, max-age=31536000, immutable")
        Record.objects.get(pk=self.story.pk).recycle()
        self.publish()
        self.assertNotIn(f"kino/filme/{self.story.pk}.html", stored)
        self.assertNotIn("geheimes-secret", json.dumps([c[2] for c in Fake.calls]))  # only the signature travels

    def test_plain_http_only_on_this_computer(self):
        with self.assertRaisesMessage(ValidationError, "https://"):
            cloud.S3Target("http://s3.example.org", "x", "b", "", "k", "s")


class NetlifyTests(ServiceCase):
    def test_only_what_netlify_asks_for_is_sent(self):
        uploads = {}

        def route(handler, method, path, body):
            assert handler.headers["Authorization"] == "Bearer netlify-token"
            if method == "GET":
                return 200, b'{"id": "site-1"}'
            if method == "POST":
                files = json.loads(body)["files"]
                assert "/index.html" in files and "/datafluss-manifest.json" in files
                Fake.digests = files
                return 200, json.dumps({"id": "deploy-1", "required": [files["/index.html"]]}).encode()
            uploads[path] = body
            return 200, b"{}"

        Fake.route = route
        self.use(Publication.NETLIFY, {"site_id": "site-1"}, "netlify-token")
        with mock.patch.object(cloud.NetlifyTarget, "API", f"{self.url}/api/v1"):
            self.publish()
        self.assertEqual(list(uploads), ["/api/v1/deploys/deploy-1/files/index.html"])
        self.assertEqual(Fake.digests["/index.html"], hashlib.sha1(uploads["/api/v1/deploys/deploy-1/files/index.html"]).hexdigest())


class GitHubTests(ServiceCase):
    def test_one_commit_with_nojekyll_and_removals(self):
        repo = {"head": None, "trees": [], "blobs": {}, "files": {}}

        def route(handler, method, path, body):
            assert handler.headers["Authorization"] == "Bearer gh-token"
            path = path.removeprefix("/repos/filmclub/website")
            data = json.loads(body) if body else {}
            if method == "GET" and path == "":
                return 200, b'{"permissions": {"push": true}}'
            if method == "GET" and path.startswith("/git/ref/heads/"):
                return (200, json.dumps({"object": {"sha": repo["head"]}}).encode()) if repo["head"] else (404, b"{}")
            if method == "GET" and path.startswith("/contents/"):
                name = path.removeprefix("/contents/")
                return (200, repo["files"][name]) if name in repo["files"] else (404, b"")
            if method == "GET" and path.startswith("/git/commits/"):
                return 200, b'{"tree": {"sha": "tree-0"}}'
            if method == "POST" and path == "/git/blobs":
                sha = hashlib.sha1(base64.b64decode(data["content"])).hexdigest()
                repo["blobs"][sha] = base64.b64decode(data["content"])
                return 201, json.dumps({"sha": sha}).encode()
            if method == "POST" and path == "/git/trees":
                repo["trees"].append(data)
                for entry in data["tree"]:
                    if entry["sha"] is None:
                        repo["files"].pop(entry["path"], None)
                    else:
                        repo["files"][entry["path"]] = repo["blobs"][entry["sha"]]
                return 201, b'{"sha": "tree-1"}'
            if method == "POST" and path == "/git/commits":
                return 201, json.dumps({"sha": f"commit-{len(repo['trees'])}"}).encode()
            if path in ("/git/refs", "/git/refs/heads/gh-pages"):
                repo["head"] = data["sha"]
                return 200, b"{}"
            return 404, b"{}"

        Fake.route = route
        self.use(Publication.GITHUB, {"repo": "filmclub/website", "branch": "gh-pages", "folder": "docs"}, "gh-token")
        with mock.patch.object(cloud.GitHubTarget, "API", self.url):
            self.publish()
            first = repo["trees"][0]
            self.assertNotIn("base_tree", first)  # a new branch
            self.assertIn("docs/.nojekyll", repo["files"])
            self.assertIn("docs/index.html", repo["files"])
            Record.objects.get(pk=self.story.pk).recycle()
            self.publish()
        second = repo["trees"][1]
        self.assertEqual(second["base_tree"], "tree-0")
        self.assertIn({"path": f"docs/filme/{self.story.pk}.html", "mode": "100644", "type": "blob", "sha": None}, second["tree"])
        self.assertNotIn(f"docs/filme/{self.story.pk}.html", repo["files"])
        self.assertEqual(repo["head"], "commit-2")


class FakeSftpClient:
    def __init__(self, files):
        self.files, self.dirs = files, set()

    def stat(self, path):
        if path not in self.dirs:
            raise OSError("no such file")

    def mkdir(self, path):
        self.dirs.add(path)

    def putfo(self, handle, path):
        self.files[path] = handle.read()

    def open(self, path, mode="rb"):
        import io

        if path not in self.files:
            raise OSError("no such file")
        return io.BytesIO(self.files[path])

    def remove(self, path):
        self.files.pop(path, None)

    def close(self):
        pass


class SftpTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.files = {}
        self.key = paramiko.RSAKey.generate(1024)
        self.auth = []
        test = self

        class Transport:
            def __init__(self, sock):
                pass

            def start_client(self, timeout=None):
                pass

            def get_remote_server_key(self):
                return test.key

            def auth_password(self, user, password):
                if password != "sftp-pass":
                    raise paramiko.AuthenticationException()
                test.auth.append(("password", user))

            def auth_publickey(self, user, key):
                test.auth.append(("key", user, key.get_name()))

            def close(self):
                pass

        patches = [mock.patch("publish.sftp.socket.create_connection", return_value=object()), mock.patch("publish.sftp.paramiko.Transport", Transport),
                   mock.patch("publish.sftp.paramiko.SFTPClient.from_transport", return_value=FakeSftpClient(self.files))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_host_key_is_trusted_on_first_use_and_then_held_to(self):
        self.use(Publication.SFTP, {"host": "sftp.example.org", "port": 22, "user": "anna", "path": "/var/www/kino", "auth": "password"}, "sftp-pass")
        self.login(self.it)
        answer = self.client.post(reverse("publication_test", args=[self.publication.pk])).content.decode()
        self.assertIn("vertraut", answer)
        self.assertEqual(Publication.objects.get().target_config["host_key"], sftp.fingerprint(self.key))
        self.publish()
        self.assertIn("/var/www/kino/index.html", self.files)

        self.key = paramiko.RSAKey.generate(1024)  # the server, or someone in the middle, shows another key
        service.start(Publication.objects.get(), service.PUBLISH)
        publication = Publication.objects.get()
        self.assertEqual(publication.status, Publication.FAILED)
        self.assertIn("anderen Schlüssel", publication.error)

    def test_signing_in_with_a_key(self):
        import io

        text = io.StringIO()
        paramiko.RSAKey.generate(1024).write_private_key(text)
        self.use(Publication.SFTP, {"host": "sftp.example.org", "user": "anna", "path": "kino", "auth": "key"}, text.getvalue())
        self.publish()
        self.assertEqual(self.auth[-1], ("key", "anna", "ssh-rsa"))
        self.assertIn("kino/index.html", self.files)
        with self.assertRaisesMessage(ValidationError, "SSH-Schlüssel"):
            sftp.private_key("kein Schlüssel")

    def test_a_wrong_password(self):
        self.use(Publication.SFTP, {"host": "sftp.example.org", "user": "anna", "auth": "password"}, "falsch")
        with self.assertRaisesMessage(ValidationError, "Anmeldung abgelehnt"):
            targets.target_for(Publication.objects.get()).test()


class HostingApiTests(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        self.token = hosting.new_token()
        self.site = HostedSite.objects.create(name="Gast", slug="gast", token_hash=hosting.token_hash(self.token), quota_mb=1)

    def call(self, method, path, body=b"", token=None):
        return getattr(self.client, method)(f"/api/hosting/v1/{path}", body, content_type="application/octet-stream",
                                             HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def test_files_go_up_and_are_served_in_a_sandbox(self):
        self.assertEqual(self.call("get", "site").json()["quota"], 1024 * 1024)
        self.assertEqual(self.call("put", "files/index.html", b"<h1>Hallo</h1><script src='x.js'></script>").status_code, 201)
        self.assertEqual(self.call("put", "files/sub/x.js", b"alert(1)").status_code, 201)
        page = self.client.get("/p/gast/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("sandbox allow-scripts", page["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", page["Content-Security-Policy"])
        self.assertEqual(self.client.get("/p/gast/sub/x.js")["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(self.call("delete", "files/sub/x.js").status_code, 200)
        self.assertEqual(self.client.get("/p/gast/sub/x.js").status_code, 404)

    def test_refusals(self):
        self.assertEqual(self.call("get", "site", token="falsch").status_code, 401)
        self.assertEqual(self.client.get("/api/hosting/v1/site").status_code, 401)
        for bad in ("files/../x", "files/.hidden", "files/a//b"):
            with self.subTest(bad):
                self.assertIn(self.call("put", bad, b"x").status_code, (400, 404))
        self.assertEqual(self.call("put", "files/gross.bin", b"x" * (1024 * 1024 + 1)).status_code, 507)  # over the quota
        self.call("put", "files/datei.exe", b"MZ")
        self.assertEqual(self.client.get("/p/gast/datei.exe")["Content-Disposition"], "attachment")
        self.assertEqual(self.client.get("/media/hosted/gast/datei.exe").status_code in (302, 403, 404), True)

    def test_settings_create_shows_the_token_once(self):
        self.login(self.it)
        page = self.client.post(reverse("hosted_sites"), {"action": "create", "name": "Verein"}).content.decode()
        token = re.search(r'select-all break-all h-auto py-2">([^<]+)<', page).group(1)
        site = HostedSite.objects.get(name="Verein")
        self.assertEqual(site.token_hash, hosting.token_hash(token))
        self.assertNotIn(token, self.client.get(reverse("publication_list")).content.decode())
        self.client.post(reverse("hosted_sites"), {"action": "delete", "site": site.pk})
        self.assertFalse(HostedSite.objects.filter(name="Verein").exists())

    def test_addresses_are_not_shared_with_publications(self):
        self.login(self.it)
        self.client.post(reverse("publication_list"), {"name": "Gast"})
        self.assertEqual(Publication.objects.get(name="Gast").slug, "gast-2")


class HostingEndToEnd(LiveServerTestCase):
    """A publication sent with the hosting target to a server that runs for real."""

    def setUp(self):
        from vault import crypto

        crypto.ensure_key_file()
        self.media = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def test_publish_to_another_server(self):
        from core.models import Role
        from tables import services
        from tables.models import READ

        films = services.create_table("Filme")
        services.set_rights(films, {Role.SCHULLEITUNG: READ})
        services.create_record(films, {"f_name": "Momo"})
        token = hosting.new_token()
        HostedSite.objects.create(name="Kino", slug="kino", token_hash=hosting.token_hash(token))
        publication = Publication.objects.create(name="Kino", slug="kino-hier", role=Role.SCHULLEITUNG, choice={"filme": {"fields": ["f_name"]}},
                                                 target=Publication.HOSTING, target_config={"url": self.live_server_url})
        targets.set_password(publication, token)
        publication.save()
        Publication.objects.filter(pk=publication.pk).update(published_choice=service.confirmed_state(publication))
        with mock.patch.object(service, "IN_THREAD", False):
            service.start(Publication.objects.get(pk=publication.pk), service.PUBLISH)
        publication = Publication.objects.get(pk=publication.pk)
        self.assertEqual(publication.status, Publication.DONE, publication.error)
        served = Path(self.media) / "hosted" / "kino"
        self.assertTrue((served / "index.html").is_file())
        self.assertIn("Momo", (served / "filme" / "index.html").read_text())
