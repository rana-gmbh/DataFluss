"""Hybrid logical clock used to order changes between the two servers."""
import threading
import time

from django.conf import settings

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def tick():
    global _last_ms, _counter
    with _lock:
        now = int(time.time() * 1000)
        if now > _last_ms:
            _last_ms, _counter = now, 0
        else:
            _counter += 1
        return f"{_last_ms:015d}-{_counter:05d}-{settings.SERVER_ID}"


def observe(remote):
    """Move the local clock forward when a newer remote stamp arrives."""
    global _last_ms, _counter
    remote_ms, remote_counter = int(remote[:15]), int(remote[16:21])
    with _lock:
        if remote_ms > _last_ms or (remote_ms == _last_ms and remote_counter >= _counter):
            _last_ms, _counter = remote_ms, remote_counter
