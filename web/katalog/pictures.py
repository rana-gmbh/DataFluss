"""Screenshots: whatever was uploaded is decoded and written anew as WebP, so no metadata (location, camera) and
nothing but pixels reaches visitors."""
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError

MAX_FILES = 4
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
WIDTH = 1600
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class PictureError(Exception):
    pass


def convert(upload):
    """(ContentFile, width, height) of an upload, or PictureError with a plain message."""
    if upload.size > MAX_BYTES:
        raise PictureError(_("%(name)s ist größer als 5 MB.") % {"name": upload.name})
    try:
        with Image.open(upload) as probe:
            probe.verify()
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise PictureError(_("%(name)s: bitte PNG, JPEG oder WebP.") % {"name": upload.name})
            image.load()
            picture = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise PictureError(_("%(name)s ist kein lesbares Bild.") % {"name": upload.name})
    if picture.width > WIDTH:
        picture = picture.resize((WIDTH, round(picture.height * WIDTH / picture.width)), Image.LANCZOS)
    out = BytesIO()
    picture.save(out, "WEBP", quality=85, method=4)
    return ContentFile(out.getvalue(), name="bild.webp"), picture.width, picture.height
