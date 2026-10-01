"""Opaque keyset cursors for GET /messages pagination (mail-archive-server#2).

An offset shifts whenever the result set changes between two requests, so a
caller walking a range with it can skip or repeat messages as mail is marked
read, gains the deleted flag, or newly arrives mid-walk. A cursor instead
names the last row seen -- (date_utc, id) -- and the exact filters and order
it was issued under, and asks for rows strictly after that position:
stable regardless of what else changes in the index between two calls.

The server builds and reads these; callers only ever pass one back
unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict

from mail_archive_server.store import SearchFilters


class InvalidCursor(Exception):
    pass


def _normalise(value):
    if isinstance(value, (set, frozenset)):
        return sorted(_normalise(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    return value


def _fingerprint(filters: SearchFilters) -> str:
    """Everything that determines *which rows, in what order* -- not
    `limit`/`offset`/`after`, which legitimately differ page to page within
    one walk.
    """
    data = asdict(filters)
    for field in ("limit", "offset", "after"):
        data.pop(field, None)
    normalised = {key: _normalise(value) for key, value in data.items()}
    raw = json.dumps(normalised, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def encode(*, date_utc: str, id: str, filters: SearchFilters) -> str:
    payload = {"date_utc": date_utc, "id": id, "fp": _fingerprint(filters)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(token: str, *, filters: SearchFilters) -> tuple[str, str]:
    """The decoded (date_utc, id) position, or raises InvalidCursor -- a
    malformed token, or one issued under different filters or order.
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        date_utc, id_, fp = payload["date_utc"], payload["id"], payload["fp"]
    except Exception as exc:
        raise InvalidCursor("malformed cursor") from exc

    if not isinstance(date_utc, str) or not isinstance(id_, str) or not isinstance(fp, str):
        raise InvalidCursor("malformed cursor")
    if fp != _fingerprint(filters):
        raise InvalidCursor("cursor was issued under different filters or order")
    return date_utc, id_
