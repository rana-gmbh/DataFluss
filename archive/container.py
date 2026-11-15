"""The encrypted container file: a password protects a stream of 1 MiB blocks.

Each block is authenticated, and the last one is marked, so changed or cut-off files are noticed.
"""
import struct

from django.utils.translation import gettext as _, gettext_lazy
from nacl import bindings, exceptions, pwhash, utils

MAGIC = b"SVBACKUP"
FORMAT = 1
BLOCK = 1024 * 1024
HEADER = struct.Struct(">8sB16sQQ")  # magic, format, salt, Argon2 operations, Argon2 memory
LENGTH = struct.Struct(">I")
KDF = pwhash.argon2id
OPS, MEM = KDF.OPSLIMIT_MODERATE, KDF.MEMLIMIT_MODERATE
BROKEN = gettext_lazy("Das Passwort stimmt nicht, oder die Datei ist beschädigt.")


class ContainerError(Exception):
    """The message is shown to the user."""


def _key(password, salt, ops, mem):
    return KDF.kdf(bindings.crypto_secretstream_xchacha20poly1305_KEYBYTES, password.encode(), salt, opslimit=ops, memlimit=mem)


def is_container(start):
    return start[:len(MAGIC)] == MAGIC


class Writer:
    def __init__(self, raw, password):
        self.raw, self.pending = raw, bytearray()
        salt = utils.random(KDF.SALTBYTES)
        self.header = HEADER.pack(MAGIC, FORMAT, salt, OPS, MEM)
        self.state = bindings.crypto_secretstream_xchacha20poly1305_state()
        stream = bindings.crypto_secretstream_xchacha20poly1305_init_push(self.state, _key(password, salt, OPS, MEM))
        raw.write(self.header + stream)

    def _push(self, block, tag):
        box = bindings.crypto_secretstream_xchacha20poly1305_push(self.state, block, self.header, tag)
        self.raw.write(LENGTH.pack(len(box)) + box)

    def write(self, data):
        self.pending += data
        while len(self.pending) >= BLOCK:
            self._push(bytes(self.pending[:BLOCK]), bindings.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE)
            del self.pending[:BLOCK]
        return len(data)

    def close(self):
        self._push(bytes(self.pending), bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL)
        self.pending.clear()


class Reader:
    def __init__(self, raw, password):
        self.raw, self.pending, self.finished = raw, bytearray(), False
        self.header = raw.read(HEADER.size)
        if len(self.header) < HEADER.size or not is_container(self.header):
            raise ContainerError(_("Das ist keine Sicherungsdatei."))
        _magic, version, salt, ops, mem = HEADER.unpack(self.header)
        if version != FORMAT:
            raise ContainerError(_("Diese Sicherungsdatei stammt aus einer neueren Version. Bitte zuerst aktualisieren."))
        if ops > KDF.OPSLIMIT_SENSITIVE or mem > KDF.MEMLIMIT_SENSITIVE:
            raise ContainerError(str(BROKEN))
        stream = raw.read(bindings.crypto_secretstream_xchacha20poly1305_HEADERBYTES)
        if len(stream) < bindings.crypto_secretstream_xchacha20poly1305_HEADERBYTES:
            raise ContainerError(str(BROKEN))
        self.state = bindings.crypto_secretstream_xchacha20poly1305_state()
        bindings.crypto_secretstream_xchacha20poly1305_init_pull(self.state, stream, _key(password, salt, ops, mem))

    def _pull(self):
        length = self.raw.read(LENGTH.size)
        if len(length) < LENGTH.size:
            raise ContainerError(str(BROKEN))
        size = LENGTH.unpack(length)[0]
        box = self.raw.read(size)
        if size > BLOCK + bindings.crypto_secretstream_xchacha20poly1305_ABYTES or len(box) < size:
            raise ContainerError(str(BROKEN))
        try:
            block, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(self.state, box, self.header)
        except exceptions.RuntimeError:
            raise ContainerError(str(BROKEN))
        self.pending += block
        self.finished = tag == bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL

    def read(self, size=-1):
        while not self.finished and (size < 0 or len(self.pending) < size):
            self._pull()
        size = len(self.pending) if size < 0 else size
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data

    def verify_end(self):
        """Reads to the final block, which proves that the file is complete."""
        while not self.finished:
            self.pending.clear()
            self._pull()
