"""Uploaded files encrypted on the disk, for the desktop client.

The client sets MEDIA_KEY (base64 of 32 bytes) from the Keychain, because its database is encrypted
but photos and documents next to it would not be. Servers leave it unset, and then nothing changes:
both functions return their input and the files stay as they are.

A sealed file is MAGIC + nonce + XChaCha20-Poly1305 ciphertext. Names and folders stay the same.
"""
import base64

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.utils.translation import gettext as _
from nacl import bindings, exceptions, utils

MAGIC = b"SVM1"
KEY_BYTES = bindings.crypto_aead_xchacha20poly1305_ietf_KEYBYTES
NONCE_BYTES = bindings.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
OVERHEAD = len(MAGIC) + NONCE_BYTES + bindings.crypto_aead_xchacha20poly1305_ietf_ABYTES
_cached = {}


class Unreadable(ValueError):
    """Not sealed, damaged or sealed with another key."""


def active():
    return bool(settings.MEDIA_KEY)


def _key():
    encoded = settings.MEDIA_KEY
    if encoded not in _cached:
        try:
            key = base64.b64decode(encoded, validate=True)
        except ValueError:
            key = b""
        if len(key) != KEY_BYTES:
            raise ImproperlyConfigured("MEDIA_KEY muss Base64 von 32 Bytes sein.")
        _cached[encoded] = key
    return _cached[encoded]


def seal(data):
    if not active():
        return data
    nonce = utils.random(NONCE_BYTES)
    return MAGIC + nonce + bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(data, MAGIC, nonce, _key())


def unseal(data):
    """With a key, a file without the prefix is refused, so a plain file never passes for a sealed one."""
    if not active():
        return data
    if not data.startswith(MAGIC) or len(data) < OVERHEAD:
        raise Unreadable(_("Die Datei ist nicht verschlüsselt."))
    nonce, box = data[len(MAGIC):len(MAGIC) + NONCE_BYTES], data[len(MAGIC) + NONCE_BYTES:]
    try:
        return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(box, MAGIC, nonce, _key())
    except exceptions.CryptoError:
        raise Unreadable(_("Die Datei ist beschädigt oder mit einem anderen Schlüssel verschlüsselt."))


def plain_size(stored):
    return max(stored - OVERHEAD, 0) if active() else stored


class SealedStorage(FileSystemStorage):
    """Chosen in the settings when MEDIA_KEY is set. Files are read whole, which is fine below the 20 MB upload limit."""

    def _open(self, name, mode="rb"):
        if mode.strip("b") != "r":
            raise ValueError("Verschlüsselte Dateien lassen sich nur lesen.")
        with open(self.path(name), "rb") as raw:
            return ContentFile(unseal(raw.read()), name=name)

    def _save(self, name, content):
        return super()._save(name, ContentFile(seal(b"".join(content.chunks()))))

    def size(self, name):
        return plain_size(super().size(name))
