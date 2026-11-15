"""Small versions of the photos for lists. They are made on first use, so older photos need no conversion."""
import hashlib
import io
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

from core import sealed

log = logging.getLogger(__name__)
BOX = (96, 128)


def thumbnail(name):
    """Returns the media name of the small version, or of the original if the photo cannot be read."""
    # A hidden folder stays out of the backups, and thumbnails can always be made again.
    small_name = f".thumbs/{hashlib.sha256(name.encode()).hexdigest()}.webp"
    target = Path(settings.MEDIA_ROOT) / small_name
    if target.is_file():
        return small_name
    try:
        target.parent.mkdir(exist_ok=True)
        with default_storage.open(name) as source, Image.open(source) as image:
            small = ImageOps.exif_transpose(image).convert("RGB")
            small.thumbnail(BOX)
            buffer = io.BytesIO()
            small.save(buffer, "WEBP", quality=80)
        partial = target.with_suffix(f".{uuid.uuid4().hex}.part")  # Two lists can ask at the same moment.
        partial.write_bytes(sealed.seal(buffer.getvalue()))
        partial.replace(target)
        return small_name
    except Exception:
        log.exception("Could not make a thumbnail")
        return name
