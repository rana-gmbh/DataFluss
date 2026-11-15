"""XChaCha20-Poly1305 encryption of secrets, with the key held outside the database."""
import base64
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from nacl import bindings, hash as nacl_hash, utils

PREFIX = "v1:"
KEY_BYTES = bindings.crypto_aead_xchacha20poly1305_ietf_KEYBYTES
NONCE_BYTES = bindings.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
_cached = {}


def generate_key():
    return base64.b64encode(utils.random(KEY_BYTES)).decode()


def _key():
    source = settings.VAULT_KEY or settings.VAULT_KEY_FILE
    if source in _cached:
        return _cached[source]
    encoded = settings.VAULT_KEY
    if not encoded:
        path = Path(settings.VAULT_KEY_FILE)
        if not path.exists():
            raise ImproperlyConfigured(f"Tresorschlüssel fehlt: {path}. Mit 'manage.py init_vault' erzeugen.")
        encoded = path.read_text().strip()
    key = base64.b64decode(encoded)
    if len(key) != KEY_BYTES:
        raise ImproperlyConfigured("Tresorschlüssel hat die falsche Länge.")
    _cached[source] = key
    return key


def reset_key_cache():
    _cached.clear()


def encrypt(plain, context):
    """`context` binds the value to its record and field, so ciphertext cannot be moved."""
    if plain in (None, ""):
        return ""
    nonce = utils.random(NONCE_BYTES)
    box = bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(plain.encode(), context.encode(), nonce, _key())
    return PREFIX + base64.b64encode(nonce + box).decode()


def decrypt(token, context):
    if not token:
        return ""
    raw = base64.b64decode(token[len(PREFIX):])
    nonce, box = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(box, context.encode(), nonce, _key()).decode()


def derived_password(purpose):
    """For files that must open without anyone typing a password. Copying them is useless without the key."""
    return nacl_hash.blake2b(purpose.encode(), key=_key()).decode()


def check_key():
    try:
        _key()
    except (OSError, ValueError) as exc:
        raise ImproperlyConfigured(f"Tresorschlüssel nicht lesbar: {exc}")


def ensure_key_file():
    path = Path(settings.VAULT_KEY_FILE)
    if settings.VAULT_KEY or path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_key() + "\n")
    os.chmod(path, 0o400)
    return True
