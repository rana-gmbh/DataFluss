"""SFTP as a target (docs/publish.md "Ziele"), with paramiko. Signs in with a password or a private SSH key
(both kept in the vault, publish/targets.py password()).

The server's host key is trusted on first use: the first connection (usually "Gespeichertes Ziel testen")
records its fingerprint in the target's settings and shows it on the page; from then on another key stops
every connection, since that is what a server in the middle would look like.
"""
import base64
import hashlib
import io
import posixpath
import socket

import paramiko
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .targets import TIMEOUT, Target, safe

KEY_TYPES = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)


def fingerprint(key):
    return f"{key.get_name()} SHA256:{base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')}"


def private_key(text):
    for kind in KEY_TYPES:
        try:
            return kind.from_private_key(io.StringIO(text.strip() + "\n"))
        except (paramiko.SSHException, ValueError, IndexError):
            continue
    raise ValidationError(_("SFTP: Der SSH-Schlüssel lässt sich nicht lesen (nur Schlüssel ohne Passphrase)."))


class SftpTarget(Target):
    def __init__(self, host, port, user, secret, folder, host_key="", auth="password"):
        self.host, self.port = (host or "").strip(), int(port or 22)
        self.user, self.secret, self.auth = (user or "").strip(), secret or "", auth
        folder = (folder or "").strip()
        self.folder = folder.rstrip("/") or ("/" if folder.startswith("/") else "")  # empty: the account's home folder
        self.host_key = host_key or ""
        self.seen_key = ""
        if not self.host or not self.user:
            raise ValidationError(_("SFTP: Bitte Server und Benutzer angeben."))
        self.transport = self.sftp = None
        self.made = set()

    def start(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=TIMEOUT)
            self.transport = paramiko.Transport(sock)
            self.transport.start_client(timeout=TIMEOUT)
            self.seen_key = fingerprint(self.transport.get_remote_server_key())
            if self.host_key and self.seen_key != self.host_key:
                raise ValidationError(_("SFTP: Der Server meldet einen anderen Schlüssel als beim ersten Mal (%(key)s). Aus Sicherheit wird "
                                        "nichts übertragen. Wenn der Server neu eingerichtet wurde, das Ziel neu speichern und testen.")
                                      % {"key": self.seen_key})
            if self.auth == "key":
                self.transport.auth_publickey(self.user, private_key(self.secret))
            else:
                self.transport.auth_password(self.user, self.secret)
            self.sftp = paramiko.SFTPClient.from_transport(self.transport)
        except ValidationError:
            self.close()
            raise
        except paramiko.AuthenticationException:
            self.close()
            raise ValidationError(_("SFTP: Anmeldung abgelehnt."))
        except (OSError, paramiko.SSHException, EOFError) as exc:
            self.close()
            raise ValidationError(_("SFTP nicht erreichbar: %(error)s") % {"error": exc})

    def close(self):
        for part in (self.sftp, self.transport):
            if part is not None:
                try:
                    part.close()
                except Exception:  # noqa: BLE001 - closing what may already be gone
                    pass
        self.sftp = self.transport = None

    def _path(self, path):
        return posixpath.join(self.folder, safe(path)) if self.folder else safe(path)

    def _folders(self, path):
        parts = path.split("/")[:-1]
        for n in range(1, len(parts) + 1):
            folder = "/".join(parts[:n])
            if folder in self.made:
                continue
            remote = self._path(folder)
            try:
                self.sftp.stat(remote)
            except OSError:
                self.sftp.mkdir(remote)
            self.made.add(folder)

    def read(self, path):
        try:
            with self.sftp.open(self._path(path), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def put(self, path, data):
        self._folders(path)
        try:
            self.sftp.putfo(io.BytesIO(data), self._path(path))
        except OSError as exc:
            raise ValidationError(_("SFTP: %(path)s konnte nicht gespeichert werden (%(error)s).") % {"path": path, "error": exc})

    def delete(self, path):
        try:
            self.sftp.remove(self._path(path))
        except OSError:
            pass  # gone already
