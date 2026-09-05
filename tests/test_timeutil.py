from __future__ import annotations
from mail_archive_server.timeutil import (
    mtime_to_utc,
    normalize_since,
    normalize_until,
    stale_seconds,
)


def test_normalize_since_bare_date_becomes_start_of_day():
    assert normalize_since("2026-09-01") == "2026-09-01T00:00:00Z"


def test_normalize_since_full_timestamp_passes_through():
    assert normalize_since("2026-09-01T14:30:00Z") == "2026-09-01T14:30:00Z"


def test_normalize_until_bare_date_becomes_end_of_day():
    assert normalize_until("2026-09-01") == "2026-09-01T23:59:59Z"


def test_normalize_until_full_timestamp_passes_through():
    assert normalize_until("2026-09-01T14:30:00Z") == "2026-09-01T14:30:00Z"


def test_mtime_to_utc_formats_as_rfc3339_z():
    # 2026-01-01T00:00:00Z
    assert mtime_to_utc(1767225600.0) == "2026-01-01T00:00:00Z"


def test_stale_seconds_none_when_no_last_sync():
    assert stale_seconds(None) is None


def test_stale_seconds_computes_difference():
    assert stale_seconds("2026-09-01T00:00:00Z", now="2026-09-01T01:00:00Z") == 3600


def test_stale_seconds_never_negative():
    assert stale_seconds("2026-09-01T01:00:00Z", now="2026-09-01T00:00:00Z") == 0
