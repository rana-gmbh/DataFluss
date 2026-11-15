"""The copy that survives this server: any S3-compatible storage."""
import time

from django.conf import settings
from django.utils.translation import gettext as _, gettext_noop

NIGHTLY_FILES = "nacht-"
BEAT_EVERY = 30


class OffsiteError(Exception):
    pass


def enabled():
    return bool(settings.BACKUP_S3_BUCKET and settings.BACKUP_S3_ACCESS_KEY and settings.BACKUP_S3_SECRET_KEY)


def _client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.BACKUP_S3_ENDPOINT or None,
        region_name=settings.BACKUP_S3_REGION or None,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
        # Storage from other providers refuses the newer default checksums.
        config=Config(connect_timeout=15, read_timeout=60, retries={"max_attempts": 5, "mode": "standard"},
                      request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )


def _key(name):
    prefix = settings.BACKUP_S3_PREFIX.strip("/")
    return f"{prefix}/{name}" if prefix else name


def copy(path, progress):
    """Uploads the file and proves that all of it arrived."""
    from botocore.exceptions import BotoCoreError, ClientError

    beat = {"last": time.monotonic()}

    def sent(_sent):
        if time.monotonic() - beat["last"] > BEAT_EVERY:
            beat["last"] = time.monotonic()
            progress(gettext_noop("Kopie wird übertragen"))

    client = _client()
    try:
        client.upload_file(str(path), settings.BACKUP_S3_BUCKET, _key(path.name), Callback=sent)
        size = client.head_object(Bucket=settings.BACKUP_S3_BUCKET, Key=_key(path.name))["ContentLength"]
    except (BotoCoreError, ClientError, OSError) as exc:
        raise OffsiteError(str(exc)[:300])
    if size != path.stat().st_size:
        raise OffsiteError(_("Die Kopie ist nicht vollständig angekommen."))


def prune(keep):
    client = _client()
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=settings.BACKUP_S3_BUCKET, Prefix=_key(NIGHTLY_FILES))
    keys = sorted((item["Key"] for page in pages for item in page.get("Contents", [])), reverse=True)
    for key in keys[keep:]:
        client.delete_object(Bucket=settings.BACKUP_S3_BUCKET, Key=key)
