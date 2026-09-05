from __future__ import annotations
from datetime import datetime, timezone


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mtime_to_utc(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_since(value: str) -> str:
    if len(value) == 10:
        return f"{value}T00:00:00Z"
    return value


def normalize_until(value: str) -> str:
    if len(value) == 10:
        return f"{value}T23:59:59Z"
    return value


def stale_seconds(last_sync_at: str | None, now: str | None = None) -> int | None:
    if last_sync_at is None:
        return None
    now_dt = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") if now else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    then_dt = datetime.strptime(last_sync_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return max(0, int((now_dt - then_dt).total_seconds()))
