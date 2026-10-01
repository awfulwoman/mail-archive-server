from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from mail_archive_server.config import Config
from mail_archive_server.indexer import AccountIndexStats, reindex
from mail_archive_server.sync import SyncResult, generate_mbsyncrc, sync_all, write_mbsyncrc
from mail_archive_server.store import set_meta
from mail_archive_server.timeutil import now_utc


@dataclass
class PipelineResult:
    sync: dict[str, SyncResult] = field(default_factory=dict)
    index: dict[str, AccountIndexStats] = field(default_factory=dict)


def sync_and_reindex(conn: sqlite3.Connection, config: Config, account: str | None = None) -> PipelineResult:
    """`account`, when given, scopes both the sync and the reindex to just
    that one account (mail-archive-server#1's on-demand sync) -- other
    accounts' meta rows are left untouched. The mbsyncrc still describes
    every configured account regardless (mbsync's own `-V account` CLI arg
    is what actually limits a run to one channel), so a scoped call never
    needs to rewrite it differently.
    """
    sync_results: dict[str, SyncResult] = {}
    all_accounts = list(config.imap_accounts.values())
    accounts = [a for a in all_accounts if a.name == account] if account is not None else all_accounts

    if accounts:
        content = generate_mbsyncrc(all_accounts, config.maildir_path)
        write_mbsyncrc(config.mbsyncrc_path, content)
        sync_results = sync_all(accounts, config.mbsync_bin, config.mbsyncrc_path, config.maildir_path)
        attempted_at = now_utc()
        for name, result in sync_results.items():
            set_meta(conn, f"last_sync_attempt_at:{name}", attempted_at)
            set_meta(conn, f"last_sync_ok:{name}", "1" if result.ok else "0")

    index_results = reindex(conn, config.maildir_path, [a.name for a in accounts] if account else None)
    return PipelineResult(sync=sync_results, index=index_results)
