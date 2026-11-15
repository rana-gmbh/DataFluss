from django.conf import settings
from django.utils.translation import gettext_lazy as _
from PIL import Image

# MPO is the JPEG that phone cameras write, with a second small frame that browsers ignore.
IMAGE_TYPES = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
TOO_LARGE = _("Die Datei ist größer als 20 MB.")


def too_large(upload):
    return upload.size > settings.MAX_UPLOAD_BYTES


def image_extension(upload):
    """Taken from the content, because a name proves nothing. Empty for anything but a plain picture."""
    try:
        image = Image.open(upload)
        extension = IMAGE_TYPES.get(image.format, "")
        image.verify()
        upload.seek(0)
    except Exception:
        return ""
    return extension
