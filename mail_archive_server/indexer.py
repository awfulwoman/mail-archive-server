from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from mail_archive_server.maildir import discover_accounts, walk_account
from mail_archive_server.parser import parse_message
from mail_archive_server.store import (
    compute_id,
    delete_ids,
    ids_for_account,
    insert_message,
    set_meta,
    update_flags,
)
from mail_archive_server.timeutil import mtime_to_utc, now_utc


@dataclass
class AccountIndexStats:
    parsed: int = 0
    updated: int = 0
    deleted: int = 0


def reindex_account(conn: sqlite3.Connection, account_root: Path, account: str) -> AccountIndexStats:
    stats = AccountIndexStats()
    existing = ids_for_account(conn, account)
    seen_ids: set[str] = set()

    for msg in walk_account(account_root, account):
        id_ = compute_id(msg.account, msg.folder, msg.maildir_name)
        seen_ids.add(id_)
        path_str = str(msg.path)
        flags_str = "".join(sorted(msg.flags))

        if id_ not in existing:
            raw = msg.path.read_bytes()
            parsed = parse_message(raw)
            date_utc = parsed.date_utc or mtime_to_utc(msg.mtime)
            insert_message(
                conn,
                id=id_,
                account=msg.account,
                folder=msg.folder,
                path=path_str,
                maildir_name=msg.maildir_name,
                message_id=parsed.message_id,
                from_name=parsed.from_name,
                from_raw=parsed.from_raw,
                from_addr=parsed.from_addr,
                to_raw=parsed.to_raw,
                cc_raw=parsed.cc_raw,
                subject=parsed.subject,
                date_utc=date_utc,
                size_bytes=msg.size,
                flags=flags_str,
                seen=msg.seen,
                deleted=msg.deleted,
                has_attachment=parsed.has_attachment,
                attachments=parsed.attachments,
                body_preview=parsed.body[:400],
                body=parsed.body,
                indexed_at=now_utc(),
                mtime=msg.mtime,
            )
            stats.parsed += 1
        elif existing[id_] != path_str:
            update_flags(conn, id=id_, path=path_str, flags=flags_str, seen=msg.seen, deleted=msg.deleted)
            stats.updated += 1
        # else: id present, path unchanged -> skip entirely, no reparse.

    vanished = set(existing.keys()) - seen_ids
    if vanished:
        delete_ids(conn, list(vanished))
        stats.deleted = len(vanished)

    set_meta(conn, f"last_indexed_at:{account}", now_utc())
    return stats


def reindex(
    conn: sqlite3.Connection, maildir_root: Path, accounts: list[str] | None = None
) -> dict[str, AccountIndexStats]:
    target_accounts = accounts if accounts is not None else discover_accounts(maildir_root)
    return {
        account: reindex_account(conn, maildir_root / account, account)
        for account in target_accounts
    }
